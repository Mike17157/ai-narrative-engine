// Shared reactive app state (Svelte 5 universal reactivity in a .svelte.js module).
import { get, post, put } from './api.js';

// Persist the last-used character so the Characters pane opens on it next session.
const LS_ACTIVE_CHAR = 'loom.activeChar';
const LS_ACTIVE_IMAGE = 'loom.activeImage';
// Personas now live server-side (configs/personas/). These localStorage keys are the
// migration source (loom.personas) + a one-shot migration-completed flag, plus the
// active-persona UI selection (a client concern, not data).
const LS_PERSONAS = 'loom.personas';
const LS_PERSONAS_MIGRATED = 'loom.personasMigrated';
const LS_ACTIVE_PERSONA = 'loom.activePersona';
const ls = (fn, fallback) => { try { return typeof localStorage !== 'undefined' ? fn() : fallback; } catch { return fallback; } };

// User personas — who *you* are in the chat (the {{user}} side), SillyTavern-style.
// Now server-backed; this starts empty and is filled by refreshPersonas() after mount.
function loadPersonas() {
  return [];
}

export const app = $state({
  health: null,
  models: { text: [], image: [] },
  conns: { active: { text: null, image: null }, connections: [] },
  allowNsfw: true,                    // global content gate (configs/app.json) — gates nsfw books
  activity: { jobs: [], running: 0 }, // live server-resident workloads
  localJobs: [],                      // client-driven workloads (renders, generation) + history
  // pending deep-link target (consumed by +page / panels), e.g. Settings ↔ Train
  nav: { screen: null, imageTab: null },
  // client-side selections
  activeChar: ls(() => localStorage.getItem(LS_ACTIVE_CHAR) || '', ''),
  activePipeline: '',
  activeImage: ls(() => localStorage.getItem(LS_ACTIVE_IMAGE) || '', ''), // last-used workflow, restored across sessions
  personas: loadPersonas(),
  activePersona: ls(() => localStorage.getItem(LS_ACTIVE_PERSONA) || 'you', 'you'),
  // Chat conversation lives in the store so it survives route navigation.
  chat: { messages: [], input: '', demoTurn: 0, seededFor: undefined }
});

// Set + persist the active character. '' is a valid choice (No character), so we
// record that an explicit choice was made and don't re-apply the server default.
export function setActiveChar(key) {
  app.activeChar = key || '';
  ls(() => localStorage.setItem(LS_ACTIVE_CHAR, app.activeChar), null);
}

// Set + persist the active workflow so the Images section reopens on it.
export function setActiveImage(key) {
  app.activeImage = key || '';
  ls(() => localStorage.setItem(LS_ACTIVE_IMAGE, app.activeImage), null);
}

// Section subnav is driven by per-section tree definitions (lib/nav.svelte.js)
// rendered by the root layout as a horizontal bar — no global subnav singleton.

// Client-side workload tracking — renders/generation the browser drives. Each entry
// shows in the Activity card with progress + an "Open" link to where the output landed.
// Returns a handle; mutate it as work proceeds. Finished entries linger as history but
// are pruned (see pruneLocalJobs) so the list doesn't grow without bound.
let _jid = 0;

// How much client-side history to keep. Active (running) jobs are never pruned; only the
// most recent finished ones linger so you can see "✓ done" / errors without the list piling
// up forever. Mirrors the server-side jobhub._prune(keep_finished=12) discipline, tighter
// because the Activity card is a small popup.
const KEEP_RUNNING = 8, KEEP_FINISHED = 6;
function pruneLocalJobs() {
  const active = app.localJobs.filter((j) => j.status === 'running' || j.status === 'queued');
  const finished = app.localJobs.filter((j) => j.status !== 'running' && j.status !== 'queued');
  app.localJobs = [...active.slice(0, KEEP_RUNNING), ...finished.slice(0, KEEP_FINISHED)];
}

export function startJob(kind, label, screen, total = 0) {
  const job = { id: 'c' + (++_jid), kind, label: label || '', screen: screen || '',
                status: 'running', done: 0, total, local: true, t: Date.now(),
                cancelling: false, onCancel: null };
  app.localJobs = [job, ...app.localJobs];
  pruneLocalJobs();
  // caller mutates job.done / job.total / job.status ('done'|'error'|'cancelled') and may set
  // job.onCancel = () => {…} to make it stoppable from the Activity card. A periodic sweep
  // (startPruneTimer, started in the root layout) re-runs pruneLocalJobs so jobs finalized
  // by direct status mutation still get cleaned up. finishJob() below finalizes + prunes now.
  return job;
}

// Mark a job terminal and prune immediately. Optional convenience — callers may also set
// job.status directly (the prune sweep will catch it within ~5s).
export function finishJob(job, status = 'done') {
  if (!job) return;
  job.status = status;
  pruneLocalJobs();
}

// Request cancellation of a client-side job: marks it and invokes its onCancel hook (which
// aborts the in-flight request and/or breaks the work loop). The loop sets the final status.
export function cancelJob(job) {
  if (!job || job.status !== 'running' || job.cancelling) return;
  job.cancelling = true;
  try { job.onCancel?.(); } catch { /* noop */ }
}

// Periodic prune sweep — guarantees finished jobs get cleaned up even when callers set
// job.status directly instead of via finishJob(). Returns the interval handle so the caller
// can clear it on teardown. Mirrors the refreshActivity polling cadence in the root layout.
export function startPruneTimer(ms = 5000) {
  return setInterval(pruneLocalJobs, ms);
}

// --- Bounded-concurrency render queue --------------------------------------
// Image renders hit the GPU / RunPod; firing many in parallel overloads it. This semaphore
// caps total in-flight renders across ALL jobs at MAX_CONCURRENT. Text/streaming generation
// (storyboard, scene/character extraction) bypasses it — those are CPU-light on the server
// and stream over SSE, so they don't compete for render slots. Use limitedPost() in render
// loops; plain post() everywhere else.
const MAX_CONCURRENT = 2;
const _slots = { active: 0, waiters: [] };
function acquireSlot() {
  return new Promise((resolve) => {
    const run = () => {
      _slots.active++;
      resolve(() => {
        _slots.active--;
        const next = _slots.waiters.shift();
        if (next) next();
      });
    };
    if (_slots.active < MAX_CONCURRENT) run();
    else _slots.waiters.push(run);
  });
}

// post() that waits for a render slot first. Same signature/return shape as post() so it's a
// drop-in swap inside render loops. If the job is cancelled while queued, the slot is dropped
// without issuing the request (the caller's loop checks job.cancelling after the await).
export async function limitedPost(path, body, opts = {}, job = null) {
  const release = await acquireSlot();
  try {
    if (job?.cancelling) return { ok: false, status: 0, aborted: true, data: null };
    return await post(path, body, opts);
  } finally { release(); }
}

// Personas are server-backed now (one YAML under configs/personas/). This keeps
// app.personas as an API-fed cache the chat page reads unchanged (each row's `id`
// is the server key). Field edits hit the API directly (debounced in the page), so
// there is no savePersonas() — only refresh (pull) + migrate (one-shot localStorage→server).
export async function refreshPersonas() {
  try {
    const rows = await get('/personas');
    app.personas = (rows || []).map((p) => ({ ...p, id: p.key }));
    // Drop the active selection if it no longer exists; fall back to the first row.
    if (app.personas.length && !app.personas.some((p) => p.id === app.activePersona)) {
      setActivePersona(app.personas[0].id);
    } else if (!app.personas.length && app.activePersona !== 'you') {
      // Nothing on the server yet (pre-startup-seed race) — keep 'you' so the chat still works.
      setActivePersona('you');
    }
  } catch { /* transient — keep whatever's cached */ }
}

// One-shot migration of legacy client-side personas (loom.personas in localStorage) to the
// server. Idempotent: guarded by loom.personasMigrated. Remaps the active-persona id to the
// returned server key and clears the legacy key. Safe to call every mount.
export async function migratePersonas() {
  const migrated = ls(() => localStorage.getItem(LS_PERSONAS_MIGRATED), null);
  if (migrated) return;
  const saved = ls(() => { try { return JSON.parse(localStorage.getItem(LS_PERSONAS) || 'null'); } catch { return null; } }, null);
  ls(() => localStorage.setItem(LS_PERSONAS_MIGRATED, '1'), null);   // mark done regardless of outcome
  if (!Array.isArray(saved) || !saved.length) return;                // nothing to migrate
  try {
    const r = await post('/personas/import', { personas: saved });
    if (r.ok && Array.isArray(r.data?.keys) && r.data.keys.length) {
      // Remap the active id (legacy uuid/'you') to the migrated server key of the same name.
      if (app.activePersona) {
        const prev = saved.find((p) => p.id === app.activePersona);
        const mapped = prev && r.data.keys.find((k) => k.name === prev.name);
        if (mapped) setActivePersona(mapped.key);
      }
      ls(() => localStorage.removeItem(LS_PERSONAS), null);          // legacy key no longer needed
    }
  } catch { /* server down — try again next load (flag already set; harmless) */ }
}

export function setActivePersona(id) {
  app.activePersona = id;
  ls(() => localStorage.setItem(LS_ACTIVE_PERSONA, id), null);
}

export async function refreshHealth() {
  app.health = await get('/health');
  const chosen = ls(() => localStorage.getItem(LS_ACTIVE_CHAR) !== null, false);
  if (!app.activeChar && !chosen && app.health.defaults?.character) app.activeChar = app.health.defaults.character;
  if (!app.activePipeline && app.health.defaults?.pipeline) app.activePipeline = app.health.defaults.pipeline;
  if (!app.activeImage && app.health.active_image_model) app.activeImage = app.health.active_image_model;
}

export async function refreshModels() {
  app.models = await get('/models');
  // Keep the cached workflow if it still exists; otherwise fall back to a default.
  if (app.models.image.length && !app.models.image.some((m) => m.key === app.activeImage)) {
    app.activeImage = app.models.image[0].key;
  }
}

export async function refreshConns() {
  app.conns = await get('/connections');
}

export async function refreshActivity() {
  try { app.activity = await get('/activity'); } catch { /* transient */ }
}

export async function refreshFlags() {
  try { app.allowNsfw = !!(await get('/app-flags')).allow_nsfw; } catch { /* keep default */ }
}

export async function setAllowNsfw(v) {
  app.allowNsfw = !!v;
  try { await put('/app-flags', { allow_nsfw: !!v }); } catch { /* best-effort */ }
}

export async function refreshAll() {
  await Promise.all([refreshHealth(), refreshModels(), refreshConns(), refreshFlags()]);
}

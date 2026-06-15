// Shared reactive app state (Svelte 5 universal reactivity in a .svelte.js module).
import { get } from './api.js';

// Persist the last-used character so the Characters pane opens on it next session.
const LS_ACTIVE_CHAR = 'loom.activeChar';
const LS_ACTIVE_IMAGE = 'loom.activeImage';
const LS_PERSONAS = 'loom.personas';
const LS_ACTIVE_PERSONA = 'loom.activePersona';
const ls = (fn, fallback) => { try { return typeof localStorage !== 'undefined' ? fn() : fallback; } catch { return fallback; } };

// User personas — who *you* are in the chat (the {{user}} side), SillyTavern-style.
function loadPersonas() {
  const saved = ls(() => JSON.parse(localStorage.getItem(LS_PERSONAS) || 'null'), null);
  return Array.isArray(saved) && saved.length ? saved : [{ id: 'you', name: 'You', description: '' }];
}

export const app = $state({
  health: null,
  models: { text: [], image: [] },
  conns: { active: { text: null, image: null }, connections: [] },
  activity: { jobs: [], running: 0 }, // live server-resident workloads
  localJobs: [],                      // client-driven workloads (renders, generation) + history
  // pending deep-link target (consumed by +page / panels), e.g. Settings ↔ Train
  nav: { screen: null, imageTab: null, loraView: null },
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

// Sub-header navigation: a feature page populates this and the ROOT layout renders a
// pulldown in a sub-header bar attached under the main header. Cleared on every route
// change (the destination page re-sets it via an effect if it wants one). onpick is a
// plain function (allowed in $state).
export const subnav = $state({ title: '', items: [], value: '', onpick: null, sub: null });
export function setSubnav({ title = '', items = [], value = '', onpick = null, sub = null } = {}) {
  subnav.title = title; subnav.items = items; subnav.value = value; subnav.onpick = onpick; subnav.sub = sub;
}
export function clearSubnav() { setSubnav({}); }

// Client-side workload tracking — renders/generation the browser drives. Each entry
// shows in the Activity card with progress + an "Open" link to where the output landed.
// Returns a handle; mutate it as work proceeds. Finished entries linger as history.
let _jid = 0;
export function startJob(kind, label, screen, total = 0) {
  const job = { id: 'c' + (++_jid), kind, label: label || '', screen: screen || '',
                status: 'running', done: 0, total, local: true, t: Date.now(),
                cancelling: false, onCancel: null };
  app.localJobs = [job, ...app.localJobs].slice(0, 24);
  // caller mutates job.done / job.total / job.status ('done'|'error'|'cancelled') and may set
  // job.onCancel = () => {…} to make it stoppable from the Activity card.
  return job;
}

// Request cancellation of a client-side job: marks it and invokes its onCancel hook (which
// aborts the in-flight request and/or breaks the work loop). The loop sets the final status.
export function cancelJob(job) {
  if (!job || job.status !== 'running' || job.cancelling) return;
  job.cancelling = true;
  try { job.onCancel?.(); } catch { /* noop */ }
}

export function savePersonas() { ls(() => localStorage.setItem(LS_PERSONAS, JSON.stringify(app.personas)), null); }
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

export async function refreshAll() {
  await Promise.all([refreshHealth(), refreshModels(), refreshConns()]);
}

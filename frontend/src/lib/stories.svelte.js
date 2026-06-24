// Story Builder UI state. Navigation is route-based (routes/stories/**); this store is
// data only — the library list, the loaded story, the edit clone, and the wizard draft.
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { startJob } from './app.svelte.js';
import { get, post, put, del } from './api.js';
import { consumeSse } from './sse.js';
import { loadChars } from './characters.svelte.js';

const blankWizard = () => ({
  step: 0,                 // lean flow: 0 premise · 1 characters · 2 outfits · 3 scenes
  character: '', charName: '', model: '',
  busy: false, streaming: false, streamText: '', error: null,
  name: '',
  // Character-first lean fields:
  premise: '',             // the story seed — enough on its own (no spine/storyboard)
  castKeys: [],            // the cast: existing character keys developed in the Lab
  locations: null,         // [{ id, name, description, background_prompt }] — generated from premise
  start: null,
  arcs: [],                // OPTIONAL themed arcs woven from the cast
  // Legacy fields kept so old drafts still load (unused by the lean flow):
  spine: null, board: null, cast: null, intended_ending: '', workshopPremise: '',
  draftId: null,           // server-side draft ID once persisted
  existingStoryKey: null,  // if set, save overwrites this story instead of creating a new one
  sessionId: null,         // server-side console checkpoint id (consult/graph), persists across reloads
});

function _uuid() {
  try { return crypto.randomUUID(); } catch { return 'sess-' + Date.now().toString(36); }
}

// Wizard draft persists across reloads (a model error / refresh mid-build keeps work).
const DRAFT_LS = 'loom.wizardDraft';
function loadDraft() {
  if (!browser) return null;
  try {
    const d = JSON.parse(localStorage.getItem(DRAFT_LS) || 'null');
    return d ? { ...blankWizard(), ...d, busy: false, streaming: false } : null;
  } catch { return null; }
}

// Per-story canvas view state (graph pan/zoom + list-vs-graph mode), persisted
// so swapping story stages and reloading keeps your place. Keyed by story key.
const VIEW_LS = 'loom.storyView';
function loadView() {
  if (!browser) return {};
  try { return JSON.parse(localStorage.getItem(VIEW_LS) || '{}') || {}; } catch { return {}; }
}
function saveView() {
  if (!browser) return;
  try { localStorage.setItem(VIEW_LS, JSON.stringify(stories.view)); } catch { /* quota / disabled */ }
}
function viewFor(key) { return (stories.view[key] ||= { mode: 'graph', viewport: null }); }
export function storyMode(key) { return viewFor(key).mode; }
export function setStoryMode(key, mode) { viewFor(key).mode = mode; saveView(); }
export function storyViewport(key) { return viewFor(key).viewport; }
export function setStoryViewport(key, vp) { viewFor(key).viewport = vp; saveView(); }

// AbortController for the in-flight step (kept out of reactive state).
let abortCtl = null;
export function cancelGen() {
  if (abortCtl) { try { abortCtl.abort(); } catch { /* noop */ } abortCtl = null; }
  const wz = stories.wizard;
  wz.busy = false; wz.streaming = false;
}

export const stories = $state({
  list: [],
  wizard: loadDraft() || blankWizard(),
  current: null,           // the loaded story for /stories/[key]
  view: loadView(),        // { [key]: { mode, viewport } } — canvas state, persisted
  editing: null,           // editable clone of `current` (the edit page)
  textModels: [],          // for per-stage model pickers (config/wizard)
  imageModels: [],         // scene workflows (backgrounds)
  saving: false,
  msg: null,
});

export async function loadStories() {
  try { stories.list = await get('/stories'); } catch { stories.list = []; }
}
export async function loadStory(key) {
  try { stories.current = await get(`/stories/${key}`); return stories.current; }
  catch { stories.current = null; return null; }
}
export async function loadModels() {
  try {
    stories.textModels = (await get('/text-models?kind=text')).models || [];
    stories.imageModels = (await get('/models')).image || [];
  } catch { /* offline */ }
}

// --- wizard --------------------------------------------------------------- //
export function startWizard(character, charName, model = '', existingStoryKey = null) {
  stories.wizard = { ...blankWizard(), character, charName, model, name: charName || '', existingStoryKey, sessionId: _uuid() };
  stories.msg = null;
  goto('/stories/new');
}
export function cancelWizard() {
  const id = stories.wizard.draftId;
  stories.wizard = blankWizard();
  goto('/stories');
  if (id) del(`/stories/draft/${id}`).catch(() => {});
}

// Reset wizard state without navigating (used when discarding a draft from the library).
export function resetWizard() { stories.wizard = blankWizard(); }

export function gotoStep(n) { stories.wizard.step = n; } // non-linear: jump to any step

// Derive per-step build status for the LEAN character-first flow from artifact presence
// (not the raw `step` index, a soft hint). Steps: Characters · Outfits · Scenes (Premise is
// the entry, not a status chip). Outfits can't be derived client-side, so it shows active by
// step only. status: 'done' | 'active' | 'todo'.
export function storySteps(s) {
  if (!s) return [];
  const castCount = Array.isArray(s.castKeys) ? s.castKeys.length
                  : (Array.isArray(s.cast) ? s.cast.length : 0);
  const locCount  = typeof s.locations === 'number' ? s.locations : (s.locations?.length || 0);
  const step      = s.step;   // present only on drafts / live wizard
  const stat = (done, idx) => done ? 'done' : (step != null && idx === step) ? 'active' : 'todo';
  return [
    { key: 'characters', label: 'Characters', route: 'characters', status: stat(castCount > 0, 1) },
    { key: 'outfits',    label: 'Outfits',    route: 'outfits',    status: stat(false,         2) },
    { key: 'scenes',     label: 'Scenes',     route: 'scenes',     status: stat(locCount > 0,  3) },
  ];
}

// ── Server-side draft persistence ─────────────────────────────────────────── //
// Auto-saves the full wizard state whenever the character is set, debounced to
// avoid hammering the server on every keystroke.
let _draftTimer = null;
async function _flushDraft() {
  const wz = stories.wizard;
  if (!wz.premise && !wz.castKeys?.length && !wz.character) return;
  const body = {
    ...(wz.draftId ? { id: wz.draftId } : {}),
    step: wz.step,
    name: wz.name || wz.charName || '',
    premise: wz.premise || '',
    character: wz.character,
    charName: wz.charName,
    castKeys: wz.castKeys,
    locations: wz.locations,
    start: wz.start,
    arcs: wz.arcs,
    existingStoryKey: wz.existingStoryKey,
    sessionId: wz.sessionId,
  };
  const r = await post('/stories/draft', body);
  if (r.data?.id && !wz.draftId) stories.wizard.draftId = r.data.id;
}
function scheduleDraftSave() {
  clearTimeout(_draftTimer);
  _draftTimer = setTimeout(_flushDraft, 1500);
}

// Load a draft from the server and restore wizard state so the user can continue.
// `to`: 'overview' → the non-linear build hub (see every step's status); 'step' →
// continue at the furthest reached step. Network/decode failures surface a message
// instead of silently doing nothing (a dead backend used to make the buttons no-op).
export async function resumeDraft(id, to = 'step') {
  let r = null;
  try { r = await get(`/stories/draft/${id}`); } catch { r = null; }
  if (!r || (!r.character && !(r.castKeys?.length) && !r.premise)) {
    stories.msg = { err: true, text: 'Could not load that draft — is the backend running?' };
    return;
  }
  stories.wizard = {
    ...blankWizard(),
    ...r,
    draftId: r.id,   // the persisted draft stores its id as `id`; the wizard tracks it as `draftId`.
                     // Without this remap, auto-save would write a NEW draft file every resume.
    busy: false, streaming: false, streamText: '', error: null,
  };
  if (to === 'overview') { goto('/stories/new/overview'); return; }
  const step = r.step ?? 0;
  const stepRoutes = ['premise', 'characters', 'outfits', 'scenes'];
  goto(`/stories/new/${stepRoutes[step] || 'premise'}`);
}

const w = () => stories.wizard;

async function postCancelable(path, payload) {
  abortCtl = new AbortController();
  try {
    const res = await fetch('/api' + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: abortCtl.signal
    });
    let data = null; try { data = await res.json(); } catch { /* none */ }
    return { ok: res.ok, data };
  } catch (e) {
    if (e?.name === 'AbortError') return null;
    return { ok: false, data: { error: String(e) } };
  } finally { abortCtl = null; }
}

// LEAN flow — generate locations straight from the PREMISE (no storyboard needed).
export async function genScenesFromPremise() {
  const wz = w(); wz.busy = true; wz.error = null;
  const job = startJob('Locations', wz.name || 'story', 'stories/new/scenes');
  job.onCancel = cancelGen;
  const r = await postCancelable('/stories/extract-scenes', { premise: wz.premise || '' });
  wz.busy = false;
  if (!r) { job.status = 'cancelled'; return; }
  if (r.ok && r.data?.locations) { wz.locations = r.data.locations; wz.start = r.data.start; job.status = 'done'; }
  else { wz.error = r.data?.error || 'location generation failed'; job.status = 'error'; }
}

// LEAN flow — persist the cast + locations (+ optional arcs) as a Story (no spine/storyboard).
export async function saveLeanStory() {
  const wz = w(); stories.saving = true; wz.error = null;
  const r = await post('/stories/from-cast', {
    name: wz.name || '', premise: wz.premise || '',
    characters: wz.castKeys || [], arcs: wz.arcs || [],
    locations: wz.locations || [], start: wz.start,
  });
  stories.saving = false;
  if (r.data?.ok) {
    stories.msg = { ok: true, text: `✓ Saved “${wz.name || 'story'}”` };
    const key = r.data.key, draftId = wz.draftId;
    await Promise.all([loadStories(), loadChars()]);
    if (draftId) del(`/stories/draft/${draftId}`).catch(() => {});
    // Navigate AWAY from /stories/new BEFORE clearing the cast — otherwise the wizard
    // guard sees an empty cast and bounces us back to premise.
    await goto(`/stories/${key}/overview`);
    stories.wizard = blankWizard();
  } else { wz.error = r.data?.error || 'save failed'; }
}

export async function expandArc(storyKey, arcId, onDelta, onArc) {
  const res = await fetch(`/api/stories/${storyKey}/arc/${arcId}/expand`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  await consumeSse(res, (ev) => {
    if (ev.type === 'delta') onDelta?.(ev.text);
    else if (ev.type === 'arc') onArc?.(ev);
  });
}

export async function generateTimelines(storyKey, arcId, onEvent) {
  const res = await fetch(`/api/stories/${storyKey}/arc/${arcId}/timelines`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  await consumeSse(res, (ev) => onEvent?.(ev));
}

// Expand a development graph into a fleshed (chapter-bearing) graph via the faithful
// expander. Returns the enriched graph (falls back to the input on failure).
export async function expandGraphRequest(character, graph) {
  let enriched = null;
  try {
    const res = await fetch('/api/stories/expand-graph', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character, graph }),
    });
    await consumeSse(res, (ev) => { if (ev.type === 'graph') enriched = ev.graph; });
  } catch { /* fall back to the raw graph */ }
  return enriched || graph;
}

// --- saved-story actions (route-based) ------------------------------------ //
export async function deleteStory(key) {
  await del(`/stories/${key}`);
  await Promise.all([loadStories(), loadChars()]);  // the story's generated cast is auto-pruned server-side
  if (stories.current?.key === key) { stories.current = null; goto('/stories'); }
}
// Relaunch the wizard from a story's source/primary character.
// Passes the existing story key so the save overwrites rather than duplicates.
export function regenStory(st) {
  const charKey = st.fields?.source_character || st.cast?.find((m) => m.primary)?.character || st.cast?.[0]?.character;
  startWizard(charKey || '', st.name || '', '', st.key || null);
}

// --- edit page (clone of current; debounced auto-save) -------------------- //
export function editStory() {
  stories.editing = JSON.parse(JSON.stringify(stories.current));
  stories.editing.themes = stories.editing.themes || [];
  stories.editing.locations = stories.editing.locations || [];
  stories.editing.storyboard = stories.editing.storyboard || { logline: '', beats: [] };
  stories.editing.storyboard.beats = stories.editing.storyboard.beats || [];
  stories.editing.cast = stories.editing.cast || [];
  stories.msg = null;
}
let editSaveTimer = null, editSaving = false;
function editPayload(e) {
  return { name: e.name, premise: e.premise, tone: e.tone, themes: e.themes,
           storyboard: e.storyboard, cast: e.cast, locations: e.locations, start: e.start };
}

// Persist edits made directly to the loaded story (e.g. a flat-beat card edited
// from the graph modal) without going through the full edit clone.
export async function persistCurrent() {
  const e = stories.current; if (!e) return;
  try { await put(`/stories/${e.key}`, editPayload(e)); } catch { /* keep local */ }
}
export function scheduleEditSave() { clearTimeout(editSaveTimer); editSaveTimer = setTimeout(autoSaveEdit, 700); }
async function autoSaveEdit() {
  const e = stories.editing; if (!e) return;
  if (editSaving) { scheduleEditSave(); return; }
  editSaving = true; stories.saving = true;
  const r = await put(`/stories/${e.key}`, editPayload(e));
  editSaving = false; stories.saving = false;
  stories.msg = r.ok ? { ok: true, text: '✓ Saved' } : { err: true, text: r.data?.error || 'save failed' };
}
// Leave the editor — flush a final save, refresh the read copy, go to the overview.
export async function finishEdit() {
  clearTimeout(editSaveTimer);
  const e = stories.editing;
  if (e) {
    try { await put(`/stories/${e.key}`, editPayload(e)); stories.current = await get(`/stories/${e.key}`); } catch { /* keep */ }
    stories.editing = null;
    goto(`/stories/${e.key}/overview`);
  } else { goto('/stories'); }
}
export function eAddLocation() {
  const id = `place-${stories.editing.locations.length + 1}`;
  stories.editing.locations.push({ id, name: 'New location', description: '', background_prompt: '' });
  if (!stories.editing.start) stories.editing.start = id;
}
export function eRemoveLocation(i) {
  const [g] = stories.editing.locations.splice(i, 1);
  if (stories.editing.start === g.id) stories.editing.start = stories.editing.locations[0]?.id || null;
}
export function eAddBeat() { stories.editing.storyboard.beats.push({ title: '', summary: '', location: '', characters: [] }); }
export function eRemoveBeat(i) { stories.editing.storyboard.beats.splice(i, 1); }
export function eMoveBeat(i, d) {
  const b = stories.editing.storyboard.beats, j = i + d;
  if (j < 0 || j >= b.length) return;
  [b[i], b[j]] = [b[j], b[i]];
}
export function eRemoveCast(i) { stories.editing.cast.splice(i, 1); }
export function eAddCast(charKey) {
  if (!charKey || stories.editing.cast.some((m) => m.character === charKey)) return;
  stories.editing.cast.push({ character: charKey, primary: false, outfit: null });
}

// Persist the wizard draft on change.
if (browser) {
  $effect.root(() => {
    $effect(() => {
      JSON.stringify(stories.wizard);
      try { localStorage.setItem(DRAFT_LS, JSON.stringify(stories.wizard)); } catch { /* quota */ }
    });
    // Auto-save to server once the build has any substance (debounced 1.5 s).
    $effect(() => {
      const wz = stories.wizard;
      if (!wz.premise && !wz.castKeys?.length && !wz.character) return;
      // Track fields that warrant a server re-save.
      JSON.stringify({ step: wz.step, name: wz.name, premise: wz.premise,
                       castKeys: wz.castKeys, locations: wz.locations, arcs: wz.arcs });
      scheduleDraftSave();
    });
  });
}

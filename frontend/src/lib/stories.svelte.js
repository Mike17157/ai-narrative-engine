// Story UI state. Navigation is route-based (routes/stories/**); this store is data only —
// the library list, the loaded story, the edit clone, and per-story canvas view state.
// (The old creation WIZARD was replaced by genesis — /stories/genesis → StructureWorkspace.)
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { get, post, put, del } from './api.js';
import { consumeSse } from './sse.js';
import { loadChars } from './characters.svelte.js';

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

// The in-progress genesis story — ONE client-held draft, cached so it survives navigation and shows
// in the library as a "New story" card. It graduates to a real library entry on commit (build),
// which clears the slot. (Server-side draft stories don't exist — see router.py "DRAFT store removed".)
const DRAFT_LS = 'loom.storyDraft';
function loadDraft() {
  if (!browser) return null;
  try { return JSON.parse(localStorage.getItem(DRAFT_LS) || 'null'); } catch { return null; }
}
export function saveDraft(snap) {
  stories.draft = snap;
  if (browser) try { localStorage.setItem(DRAFT_LS, JSON.stringify(snap)); } catch { /* quota/disabled */ }
}
export function clearDraft() {
  stories.draft = null;
  if (browser) try { localStorage.removeItem(DRAFT_LS); } catch { /* disabled */ }
}

export const stories = $state({
  list: [],
  current: null,           // the loaded story for /stories/[key]
  view: loadView(),        // { [key]: { mode, viewport } } — canvas state, persisted
  draft: loadDraft(),      // the single in-progress genesis snapshot (cached), or null
  editing: null,           // editable clone of `current` (the edit page)
  textModels: [],          // for per-stage model pickers
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

// --- edit page (clone of current; debounced auto-save) -------------------- //
export function editStory() {
  stories.editing = structuredClone(stories.current);
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
    goto(`/stories/${e.key}/structure`);
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

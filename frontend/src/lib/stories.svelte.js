// Story Builder UI state. Navigation is route-based (routes/stories/**); this store is
// data only — the library list, the loaded story, the edit clone, and the wizard draft.
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { startJob } from './app.svelte.js';
import { get, post, put, del } from './api.js';
import { consumeSse } from './sse.js';
import { loadChars } from './characters.svelte.js';
import { graphToBoard, graphToArcs } from './story_graph_model.js';

const blankWizard = () => ({
  step: 0,                 // 0 setup · 1 spine · 2 storyboard · 3 scenes · 4 characters
  character: '', charName: '', model: '',
  busy: false, streaming: false, streamText: '', error: null,
  name: '',
  spine: null,             // { wound, lie, truth, heart, beats[], logline, tone, themes }
  board: null,             // { logline, premise, tone, themes, beats[] }
  locations: null,         // [{ id, name, description, background_prompt }]
  start: null,
  cast: null,              // [{ name, persona, appearance, role, base_prompt, primary }] — protagonist first
  intended_ending: '',     // destination locked in during workshop phase 1
  arcs: [],                // [{id, name, mini_ending, dramatic_function, cast, rationale}]
  workshopPremise: '',     // premise text from workshop, passed to spine + storyboard
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

// Derive per-step build status from ARTIFACT PRESENCE (not the raw `step` index, which
// is only a soft "where gen last left off" hint). Defensive across all three shapes:
// a wizard draft (board/locations/cast arrays), the live stories.wizard, and a loaded
// Story (storyboard object, locations array). Never call this on a saved-story LIST
// summary — those omit spine/beats. status: 'done' | 'active' | 'todo'.
export function storySteps(s) {
  if (!s) return [];
  const board     = s.board || s.storyboard || null;
  const beats      = board?.beats || [];
  const spineDone  = !!(s.spine && (s.spine.wound || s.spine.lie || s.spine.truth));
  const locCount   = typeof s.locations === 'number' ? s.locations : (s.locations?.length || 0);
  const castCount  = Array.isArray(s.cast) ? s.cast.length : 0;
  const step       = s.step;   // present only on drafts / live wizard
  const stat = (done, idx) => done ? 'done' : (step != null && idx === step) ? 'active' : 'todo';
  return [
    { key: 'spine',      label: 'Spine',      route: 'spine',      status: stat(spineDone,        1) },
    { key: 'storyboard', label: 'Storyboard', route: 'storyboard', status: stat(beats.length > 0, 2) },
    { key: 'scenes',     label: 'Scenes',     route: 'scenes',     status: stat(locCount > 0,     3) },
    { key: 'cast',       label: 'Cast',       route: 'characters', status: stat(castCount > 0,    4) },
  ];
}

// ── Server-side draft persistence ─────────────────────────────────────────── //
// Auto-saves the full wizard state whenever the character is set, debounced to
// avoid hammering the server on every keystroke.
let _draftTimer = null;
async function _flushDraft() {
  const wz = stories.wizard;
  if (!wz.character) return;
  const body = {
    ...(wz.draftId ? { id: wz.draftId } : {}),
    step: wz.step,
    name: wz.name || wz.charName || '',
    premise: wz.board?.premise || '',
    character: wz.character,
    charName: wz.charName,
    spine: wz.spine,
    board: wz.board,
    locations: wz.locations,
    start: wz.start,
    cast: wz.cast,
    intended_ending: wz.intended_ending,
    workshopPremise: wz.workshopPremise,
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
  if (!r?.character) {
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
  const stepRoutes = ['setup', 'spine', 'storyboard', 'scenes', 'characters'];
  goto(`/stories/new/${stepRoutes[step] || 'setup'}`);
}

const w = () => stories.wizard;
function reqBody(extra = {}) { return { character: w().character, ...extra }; }  // per-stage cfg is server-side

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

// Stage 0.5 — Generate the emotional spine BEFORE the storyboard.
// Streams JSON token-by-token, then emits a final `spine` event.
export async function generateSpine(workshopPremise = '') {
  const wz = w();
  wz.busy = true; wz.streaming = true; wz.error = null; wz.streamText = '';
  abortCtl = new AbortController();
  const job = startJob('Emotional spine', wz.charName || wz.character, 'stories/new/spine');
  job.onCancel = cancelGen;
  let spine = null;
  const premise = workshopPremise || wz.workshopPremise || wz.board?.premise || '';
  const intendedEnding = wz.intended_ending || '';
  try {
    const res = await fetch('/api/stories/spine', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character: wz.character, premise, intended_ending: intendedEnding }),
      signal: abortCtl.signal
    });
    await consumeSse(res, (ev) => {
      if (ev.type === 'delta') wz.streamText += ev.text;
      else if (ev.type === 'spine') spine = ev.spine;
      else if (ev.type === 'error') wz.error = ev.error;
    });
    if (spine) {
      wz.spine = spine;
      // Pre-seed board fields from spine so storyboard can use them as defaults.
      if (!wz.board) wz.board = { logline: '', premise: '', tone: '', themes: [], beats: [], heart: '' };
      if (!wz.board.heart && spine.heart) wz.board.heart = spine.heart;
      if (!wz.board.logline && spine.logline) wz.board.logline = spine.logline;
      if (!wz.board.tone && spine.tone) wz.board.tone = spine.tone;
      if (!wz.board.themes?.length && spine.themes?.length) wz.board.themes = spine.themes;
      wz.step = 1;
      job.status = 'done';
    } else if (!wz.error) { wz.error = 'no spine produced'; job.status = 'error'; }
  } catch (e) {
    if (e?.name !== 'AbortError') wz.error = String(e);
    job.status = e?.name === 'AbortError' ? 'cancelled' : 'error';
  } finally {
    wz.busy = false; wz.streaming = false; abortCtl = null;
    if (job.status === 'running') job.status = wz.error ? 'error' : 'cancelled';
  }
}

// Stage 1 — streams the storyboard live; uses spine for context if available.
export async function genStoryboard(workshopPremise = '') {
  const wz = w();
  wz.busy = true; wz.streaming = true; wz.error = null; wz.streamText = '';
  abortCtl = new AbortController();
  const job = startJob('Storyboard', wz.charName || wz.character, 'stories/new/storyboard');
  job.onCancel = cancelGen;
  let board = null;
  const premise = workshopPremise || wz.workshopPremise || '';
  const bodyExtra = {
    ...(premise ? { premise } : {}),
    ...(wz.spine ? { spine: wz.spine } : {}),
  };
  try {
    const res = await fetch('/api/stories/storyboard', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody(bodyExtra)), signal: abortCtl.signal
    });
    await consumeSse(res, (ev) => {
      if (ev.type === 'delta') wz.streamText += ev.text;
      else if (ev.type === 'board') board = ev.board;
      else if (ev.type === 'error') wz.error = ev.error;
    });
    if (board) {
      wz.board = { logline: board.logline || '', premise: board.premise || '', tone: board.tone || '',
                   themes: board.themes || [], beats: board.beats || [], heart: board.heart || '' };
      if (!wz.name) wz.name = wz.charName || '';
      wz.step = 2;
      job.status = 'done';
    } else if (!wz.error) { wz.error = 'no storyboard produced'; job.status = 'error'; }
  } catch (e) {
    if (e?.name !== 'AbortError') wz.error = String(e);
    job.status = e?.name === 'AbortError' ? 'cancelled' : 'error';
  } finally {
    wz.busy = false; wz.streaming = false; abortCtl = null;
    if (job.status === 'running') job.status = wz.error ? 'error' : 'cancelled';
  }
}

export async function regenStoryboard() { await genStoryboard(); }

// Faithful draft: expand the development graph node-by-node into chapters (preserving
// branches), instead of re-deriving from a premise string. Seeds spine from the graph
// core and writes board + arcs. Returns true on success (caller navigates to storyboard).
export async function draftFromGraph(graph) {
  const wz = w();
  wz.busy = true; wz.streaming = true; wz.error = null;
  wz.streamText = 'Expanding each beat of the arc into a chapter…';
  wz.workshopPremise = graph?.logline || '';
  // The consultation already produced the spine — carry it straight in.
  wz.spine = {
    wound: graph?.wound || '', lie: graph?.lie || '', truth: graph?.truth || '',
    heart: '', logline: graph?.logline || '', tone: wz.board?.tone || '', themes: wz.board?.themes || [], beats: [],
  };
  abortCtl = new AbortController();
  const job = startJob('Drafting from graph', wz.charName || wz.character, 'stories/new/storyboard');
  job.onCancel = cancelGen;
  let enriched = null;
  try {
    const res = await fetch('/api/stories/expand-graph', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character: wz.character, graph }), signal: abortCtl.signal,
    });
    await consumeSse(res, (ev) => {
      // delta is raw structured-output JSON — ignore it; show a friendly status instead.
      if (ev.type === 'graph') enriched = ev.graph;
      else if (ev.type === 'error') wz.error = ev.error;
    });
    const g = enriched || graph;
    wz.board = graphToBoard(g, { tone: wz.board?.tone || '', themes: wz.board?.themes || [], heart: wz.spine.heart });
    wz.arcs = graphToArcs(g);
    if (!wz.name) wz.name = wz.charName || '';
    if (!wz.error) { wz.step = 2; job.status = 'done'; return true; }
    job.status = 'error'; return false;
  } catch (e) {
    if (e?.name !== 'AbortError') wz.error = String(e);
    job.status = e?.name === 'AbortError' ? 'cancelled' : 'error';
    return false;
  } finally {
    wz.busy = false; wz.streaming = false; abortCtl = null;
  }
}

export async function genScenes() {
  const wz = w(); wz.busy = true; wz.error = null;
  const job = startJob('Scene extraction', wz.charName || wz.character, 'stories/new/scenes');
  job.onCancel = cancelGen;
  const r = await postCancelable('/stories/extract-scenes', reqBody({ board: wz.board }));
  wz.busy = false;
  if (!r) { job.status = 'cancelled'; return; }
  if (r.ok && r.data?.locations) { wz.locations = r.data.locations; wz.start = r.data.start; wz.step = 3; job.status = 'done'; }
  else { wz.error = r.data?.error || 'scene extraction failed'; job.status = 'error'; }
}

export async function genCharacters() {
  const wz = w(); wz.busy = true; wz.error = null;
  const job = startJob('Character extraction', wz.charName || wz.character, 'stories/new/characters');
  job.onCancel = cancelGen;
  const r = await postCancelable('/stories/extract-characters', reqBody({ board: wz.board }));
  wz.busy = false;
  if (!r) { job.status = 'cancelled'; return; }
  if (r.ok && r.data?.cast !== undefined) {
    wz.cast = r.data.cast || [];                          // one uniform list, protagonist first
    const prot = wz.cast.find((c) => c.primary);
    if (prot?.name) wz.charName = prot.name;
    wz.step = 4; job.status = 'done';
  } else { wz.error = r.data?.error || 'character extraction failed'; job.status = 'error'; }
}

export async function suggestArcs(intendedEnding, messages) {
  const wz = w();
  wz.busy = true; wz.error = null;
  try {
    const res = await fetch('/api/stories/workshop/arcs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        character: wz.character,
        messages,
        intended_ending: intendedEnding,
        story_cast: [],
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data?.arcs) {
      wz.intended_ending = intendedEnding;
      wz.arcs = data.arcs;
    } else {
      wz.error = data?.error || 'arc suggestion failed';
    }
  } catch (e) {
    wz.error = String(e);
  } finally {
    wz.busy = false;
  }
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

export async function saveStory() {
  const wz = w();
  stories.saving = true; wz.error = null;
  const r = await post('/stories', {
    name: wz.name, premise: wz.board?.premise || '', tone: wz.board?.tone || '',
    themes: wz.board?.themes || [],
    storyboard: { logline: wz.board?.logline || '', beats: wz.board?.beats || [], heart: wz.board?.heart || '' },
    locations: wz.locations || [], start: wz.start,
    cast: wz.cast || [], source_character: wz.character,
    intended_ending: wz.intended_ending || '',
    arcs: wz.arcs || [],
    spine: wz.spine || null,
    ...(wz.existingStoryKey ? { existing_key: wz.existingStoryKey } : {}),
  });
  stories.saving = false;
  if (r.data?.ok) {
    stories.msg = { ok: true, text: `✓ Saved “${wz.name}”` + (r.data.created_characters?.length ? ` (+${r.data.created_characters.length} NPCs)` : '') };
    const storyKey = r.data.key;
    const draftId = wz.draftId;
    // Reload chars too: the story's freshly-generated cast must be in chars.list or every
    // charName(key) lookup falls back to the raw key (e.g. “riley_costello”).
    await Promise.all([loadStories(), loadChars()]);
    if (draftId) del(`/stories/draft/${draftId}`).catch(() => {});
    stories.wizard = blankWizard();
    // Kick off wardrobe planning immediately — navigate to cast so the user sees progress.
    let wardrobeJob = null;
    try {
      const wr = await post(`/stories/${storyKey}/plan-wardrobe-all`, {});
      if (wr.data?.job) wardrobeJob = wr.data.job;
    } catch { /* non-fatal — user can plan wardrobes manually from the cast page */ }
    goto(wardrobeJob ? `/stories/${storyKey}/cast?job=${wardrobeJob}` : `/stories/${storyKey}/cast`);
  } else { wz.error = r.data?.error || 'save failed'; }
}

// --- wizard draft editing ---
export function addBeat() { w().board.beats.push({ title: '', summary: '', location: '', characters: [] }); }
export function removeBeat(i) { w().board.beats.splice(i, 1); }
export function moveBeat(i, dir) {
  const b = w().board.beats, j = i + dir;
  if (j < 0 || j >= b.length) return;
  [b[i], b[j]] = [b[j], b[i]];
}
export function addLocation() {
  const n = w().locations.length + 1, id = `place-${n}`;
  w().locations.push({ id, name: `Location ${n}`, description: '', background_prompt: '' });
  if (!w().start) w().start = id;
}
export function removeLocation(i) {
  const [gone] = w().locations.splice(i, 1);
  if (w().start === gone.id) w().start = w().locations[0]?.id || null;
}
export function addNpc() { w().cast.push({ name: '', role: '', appearance: '', persona: '', base_prompt: '', primary: false }); }
export function removeNpc(i) { if (!w().cast[i]?.primary) w().cast.splice(i, 1); }   // never remove the protagonist

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
    // Auto-save to server when a character is selected (debounced 1.5 s).
    $effect(() => {
      const wz = stories.wizard;
      if (!wz.character) return;
      // Track fields that warrant a server re-save.
      JSON.stringify({ step: wz.step, name: wz.name, premise: wz.board?.premise,
                       board: wz.board, locations: wz.locations, cast: wz.cast });
      scheduleDraftSave();
    });
  });
}

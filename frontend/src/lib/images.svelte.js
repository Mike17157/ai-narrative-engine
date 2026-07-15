// Shared state for the Images section (owned by routes/images/+layout.svelte,
// consumed by the configure/json pages). Lifted out of the old ImagePanel so the
// workflow + test-render state survives navigating between Image sub-routes.
import { get, post } from './api.js';
import { consumeSse } from './sse.js';
import { app } from './app.svelte.js';

// Persisted test prompt + a suite of camera angles for evaluating a model/LoRA
// from every side. Each is SFW (rating `safe`) with quality anchors; swap the
// subject to taste.
const LS_TEST_PROMPT = 'loom.testPrompt';
const _lsGet = () => { try { return typeof localStorage !== 'undefined' ? localStorage.getItem(LS_TEST_PROMPT) : null; } catch { return null; } };
export function saveTestPrompt(v) { try { if (typeof localStorage !== 'undefined') localStorage.setItem(LS_TEST_PROMPT, v ?? ''); } catch { /* ignore */ } }

// Each test cell appends a RANDOM booru tag (drawn from this pool) to YOUR subject,
// instead of a fixed camera angle — a run spreads the subject across varied framing,
// poses, settings and moods, which is a much better read on a model/LoRA than the
// same eight angles every time. Kept wholesome so it doesn't fight an NSFW prior.
export const TEST_COUNT = 8;
export const TEST_TAGS = [
  // framing / composition
  'full body', 'upper body', 'portrait', 'close-up', 'cowboy shot', 'wide shot',
  'from above', 'from below', 'from side', 'from behind', 'dutch angle', 'profile',
  // pose
  'standing', 'sitting', 'kneeling', 'lying down', 'walking', 'running', 'jumping',
  'crouching', 'leaning forward', 'arms behind back', 'hand on hip', 'crossed arms',
  'stretching', 'looking back', 'arms up', 'hands in pockets', 'sitting on chair',
  // expression
  'smile', 'grin', 'open mouth', 'laughing', 'blush', 'surprised', 'pout', 'wink',
  'smirk', 'serious', 'sleepy', 'embarrassed', 'happy', 'frown',
  // gaze
  'looking at viewer', 'looking away', 'looking up', 'looking down', 'eyes closed',
  // action / props
  'holding cup', 'drinking', 'eating', 'reading book', 'playing guitar', 'holding umbrella',
  'waving', 'peace sign', 'holding flower', 'holding phone', 'adjusting hair', 'headphones',
  // setting
  'classroom', 'cafe', 'bedroom', 'city street', 'forest', 'beach', 'park', 'rooftop',
  'library', 'train interior', 'kitchen', 'garden', 'shrine', 'indoors', 'outdoors', 'window',
  // light / time / weather
  'sunset', 'golden hour', 'night', 'morning', 'backlighting', 'dramatic lighting',
  'rain', 'snow', 'overcast', 'neon lights', 'candlelight', 'dappled sunlight',
  // atmosphere / effects
  'cherry blossoms', 'falling leaves', 'wind', 'depth of field', 'bokeh', 'lens flare',
  // outfit (SFW)
  'school uniform', 'casual clothes', 'dress', 'hoodie', 'coat', 'sweater', 'kimono',
  'jacket', 'long skirt', 'scarf', 'sundress'
];

// n distinct random tags via a partial Fisher–Yates shuffle.
export function sampleTags(n = TEST_COUNT) {
  const pool = [...TEST_TAGS];
  const k = Math.min(n, pool.length);
  for (let i = 0; i < k; i++) {
    const j = i + Math.floor(Math.random() * (pool.length - i));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, k);
}

const _testCells = () =>
  sampleTags().map((t) => ({ label: t, view: t, pct: null, image: null, error: null }));

export const img = $state({
  workflow: null,
  choices: {},
  objectInfo: {}, // slim ComfyUI node schema (class_type -> {inputs, outputs}) — feeds combo-type sweep params
  loading: false,
  msg: null,
  test: null, // { phase, progress, node, images, error }
  testInitImage: null, // data-URL source image for testing an img2img workflow
  testPrompt: _lsGet() || 'rio \\(blue archive\\), 1girl, safe, masterpiece, best quality, detailed background', // your subject; persisted globally, rendered from every angle
});

// True when the active workflow is img2img — it has a LoadImage node that needs a
// source image fed in. The test grid must supply one (and so does the chat flow).
export function workflowNeedsInit() {
  const wf = img.workflow;
  if (!wf) return false;
  for (const k in wf) if (wf[k]?.class_type === 'LoadImage') return true;
  return false;
}

export async function loadWorkflow() {
  if (!app.activeImage) { img.workflow = null; return; }
  img.loading = true; img.msg = null;
  const r = await get('/workflow?model=' + encodeURIComponent(app.activeImage));
  img.loading = false;
  if (r.error) { img.workflow = null; img.msg = { err: true, text: r.error }; return; }
  img.workflow = r.json;
}

export async function loadChoices() { img.choices = await get('/comfy/choices'); }

export async function loadObjectInfo() {
  try { img.objectInfo = await get('/comfy/object_info'); } catch { img.objectInfo = {}; }
}

// A deep clone of the workflow snapshotted for a test render.
function executableWorkflow() {
  return structuredClone($state.snapshot(img.workflow) || {});
}

export function openTest() {
  img.test = { phase: 'input', cells: _testCells() };
}

// Render the subject (img.testPrompt) with a fresh batch of random booru tags,
// sequentially, filling in a grid as each completes. Your typed subject is the
// base; one sampled tag is appended per cell. Each run resamples. Cancellable mid-run.
let _testAbort = null;       // aborts the in-flight cell's fetch/stream
let _testCancelled = false;

export async function cancelTest() {
  _testCancelled = true;
  try { _testAbort?.abort(); } catch { /* already done */ }
  try { await fetch('/api/comfy/interrupt', { method: 'POST' }); } catch { /* comfy gone */ }
  if (img.test && img.test.phase === 'running') img.test.phase = 'cancelled';
}

export async function runTest() {
  _testCancelled = false;
  const base = (img.testPrompt || '').trim();
  const graph = executableWorkflow();
  img.test = { phase: 'running', mode: 'tags', cells: _testCells() };  // fresh random tags each run
  for (const cell of img.test.cells) {
    if (_testCancelled || !img.test) break;
    await _renderCell(cell, base ? `${base}, ${cell.view}` : cell.view, graph);
  }
  if (img.test && img.test.phase === 'running') img.test.phase = _testCancelled ? 'cancelled' : 'done';
}

// Parity test: compose prompts the SAME way production does (subject + expression + pose + full-body
// framing) so the grid mirrors real sprite/scene output — then render each through the active workflow.
// mode 'sprite' → one full-body cell per representative emotion; 'scene' → the subject through the
// scene framing. `src` = { subjectMode:'typed'|'char', subject, character }.
export async function composeTestCells(mode, src) {
  _testCancelled = false;
  const body = { mode };
  if (src?.subjectMode === 'char' && src.character) body.character = src.character;
  else body.subject = (src?.subject ?? img.testPrompt ?? '').trim();
  const r = await post('/test/prompts', body);
  if (!r.ok) {
    img.msg = { err: true, text: r.status === 404
      ? 'compose route missing — restart the backend (new endpoint not loaded yet)'
      : `compose failed (${r.status})${r.data?.error ? ': ' + r.data.error : ''}` };
    return;
  }
  const cells = (r.data?.cells || []).map((c) => ({ ...c, view: c.label, pct: null, image: null, error: null }));
  if (!cells.length) { img.msg = { err: true, text: 'no cells composed — pick a subject or character' }; return; }
  const graph = executableWorkflow();
  img.test = { phase: 'running', mode, cells };
  for (const cell of img.test.cells) {
    if (_testCancelled || !img.test) break;
    await _renderCell(cell, cell.prompt, graph);
  }
  if (img.test && img.test.phase === 'running') img.test.phase = _testCancelled ? 'cancelled' : 'done';
}

// --- parameter sweep: render one cell per value of a chosen workflow parameter,
// holding the prompt (and source image) fixed, so you can eyeball the best value. ---

// Tunable widget parameters in the active workflow (numeric ranges + combos).
export function sweepParams() {
  const wf = img.workflow, oi = img.objectInfo || {};
  if (!wf) return [];
  const out = [];
  for (const id in wf) {
    const node = wf[id];
    if (!node?.inputs) continue;
    const def = oi[node.class_type];
    for (const field in node.inputs) {
      const v = node.inputs[field];
      if (Array.isArray(v)) continue; // a wired link, not a widget
      const di = def?.inputs?.find((x) => x.name === field);
      const label = `${node._meta?.title || node.class_type} · ${field}`;
      if (di?.type === 'COMBO' && di.options?.length) out.push({ id, field, label, kind: 'combo', options: di.options, value: v });
      else if (typeof v === 'number') out.push({ id, field, label, kind: 'number', value: v });
    }
  }
  return out;
}

// Evenly spaced values across [min,max] (integers stay integers).
export function sweepValues(min, max, count) {
  const n = Math.max(2, Math.min(24, Math.round(count) || 6));
  const lo = +min, hi = +max;
  const ints = Number.isInteger(lo) && Number.isInteger(hi);
  const out = [];
  for (let i = 0; i < n; i++) {
    let v = lo + (hi - lo) * (i / (n - 1));
    v = ints ? Math.round(v) : Math.round(v * 100) / 100;
    if (!out.includes(v)) out.push(v);
  }
  return out;
}

export async function runSweep(param, values) {
  _testCancelled = false;
  const base = (img.testPrompt || '').trim();
  const snap = executableWorkflow();
  img.test = {
    phase: 'running', mode: 'sweep', param: param.label,
    cells: values.map((v) => ({ label: `${param.field} = ${v}`, value: v, pct: null, image: null, error: null }))
  };
  for (const cell of img.test.cells) {
    if (_testCancelled || !img.test) break;
    const g = structuredClone(snap);
    if (g[param.id]?.inputs) g[param.id].inputs[param.field] = cell.value;
    await _renderCell(cell, base, g);
  }
  if (img.test && img.test.phase === 'running') img.test.phase = _testCancelled ? 'cancelled' : 'done';
}

async function _renderCell(cell, prompt, graph) {
  cell.pct = 0;
  _testAbort = new AbortController();
  try {
    const res = await fetch('/api/workflow/test', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: _testAbort.signal,
      body: JSON.stringify({
        model: app.activeImage, json: graph, prompt,
        init_image: img.testInitImage || undefined,  // img2img source (ignored by txt2img workflows)
        width: cell.width || undefined, height: cell.height || undefined  // per-pose latent (sprite test)
      })
    });
    await consumeSse(res, (ev) => {
      if (ev.type === 'progress') cell.pct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
      else if (ev.type === 'image') cell.image = (ev.images || [])[0] || null;
      else if (ev.type === 'error') cell.error = ev.error;
    });
    if (!cell.image && !cell.error) cell.error = _testCancelled ? 'cancelled' : 'no image';
  } catch (e) {
    cell.error = (_testCancelled || e?.name === 'AbortError') ? 'cancelled' : String(e);
  }
}

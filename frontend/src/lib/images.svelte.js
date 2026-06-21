// Shared state for the Images section (owned by routes/images/+layout.svelte,
// consumed by the configure/json pages). Lifted out of the old ImagePanel so the
// workflow + test-render state survives navigating between Image sub-routes.
import { get, post } from './api.js';
import { consumeSse } from './sse.js';
import { app, setActiveImage } from './app.svelte.js';

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
  injects: {},
  sections: {},     // { prefix: { name, color, order, desc } } from meta.json — drives NodeTree grouping
  keyNodes: [],     // node IDs pinned to the Essentials section (checkpoint, LoRA anchor, samplers)
  activeSection: null, // currently focused section key (null = all visible)
  description: '',  // workflow-level prose (meta.json description) shown in the graph pane
  recipes: {},      // { id: { label, desc, flags, variant, latent, sections } } — Compose presets
  activeRecipe: null, // selected Compose preset id (dims non-active sections, drives the info panel)
  choices: {},
  objectInfo: {}, // slim ComfyUI node schema (class_type -> {inputs, outputs})
  embeddings: [], // textual-inversion embedding names (for the text-encode picker)
  loading: false,
  msg: null,
  test: null, // { phase, progress, node, images, error }
  testInitImage: null, // data-URL source image for testing an img2img workflow
  testPrompt: _lsGet() || 'rio \\(blue archive\\), 1girl, safe, masterpiece, best quality, detailed background', // your subject; persisted globally, rendered from every angle
  nodeSizes: {}, // per-block size overrides: { [nodeId]: { nodeW } }
  layoutNonce: 0, // bump to request a graph relayout (e.g. after a prompt box grew)
  familyMap: {}, // { relForwardSlash: family } for ckpt/diffusion/lora — strict node-dropdown filtering
  wfFamily: ''   // the active workflow's family (its node model pickers show only this family's files)
});

// Load the family data the graph editor needs: a file→family map (from the scan) and the active
// workflow's own family (from /loras/bases), so node model dropdowns can be scoped to that family.
export async function loadGraphFamilies() {
  try {
    const scan = await get('/comfy/models');
    img.familyMap = Object.fromEntries((scan.items || [])
      .filter((i) => i.family && ['lora', 'checkpoint', 'diffusion'].includes(i.kind))
      .map((i) => [(i.rel || '').replace(/\\/g, '/'), i.family]));
  } catch { /* scan unavailable */ }
  try {
    const bases = await get('/loras/bases');
    img.wfFamily = bases.find((b) => b.key === app.activeImage)?.family || '';
  } catch { /* none */ }
}

// Strict family filter for a node's model-picker options. Non-model widgets pass through unchanged;
// model widgets keep only files of the active workflow's family. No-op when the family is unknown.
const _MODEL_WIDGETS = new Set(['ckpt_name', 'unet_name', 'lora_name']);
const _folderFam = (n) => { const p = (n || '').replace(/\\/g, '/').split('/'); return p.length > 1 ? p[0].toLowerCase() : ''; };
const _famOfFile = (rel) => img.familyMap[(rel || '').replace(/\\/g, '/')] || _folderFam(rel);
export function familyFilteredOptions(widgetName, options) {
  if (!_MODEL_WIDGETS.has(widgetName) || !img.wfFamily || img.wfFamily === 'unknown' || !Array.isArray(options)) return options;
  return options.filter((o) => _famOfFile(o) === img.wfFamily);
}

export async function loadObjectInfo() {
  try { img.objectInfo = await get('/comfy/object_info'); } catch { img.objectInfo = {}; }
  try { img.embeddings = await get('/comfy/embeddings'); } catch { img.embeddings = []; }
}

// Toggle `embedding:NAME` in a text-encode node's text field (the lazy-embedding
// picker). Prepends when off, strips when on.
export function hasEmbedding(id, field, name) {
  const t = String(img.workflow?.[id]?.inputs?.[field] ?? '');
  return new RegExp(`(^|[\\s,])embedding:${name}([\\s,]|$)`).test(t);
}
export function toggleEmbedding(id, field, name) {
  const n = img.workflow?.[id];
  if (!n?.inputs) return;
  let t = String(n.inputs[field] ?? '');
  if (hasEmbedding(id, field, name)) {
    t = t.replace(new RegExp(`embedding:${name}\\s*,?\\s*`, 'g'), '').replace(/^\s*,\s*/, '').trim();
  } else {
    t = `embedding:${name}, ${t}`.trim();
  }
  n.inputs[field] = t;
}

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
  img.workflow = r.json; img.injects = r.injects || {};
  img.sections = r.sections || {};
  img.keyNodes = r.key_nodes || [];
  img.description = r.description || '';
  img.recipes = r.recipes || {};
  img.activeRecipe = null;
  img.activeSection = null;
}

// Map a Compose recipe's boolean flags onto the live workflow's ComfySwitchNode
// gates, located by their _meta.title (mirrors loom/providers/_workflow.apply_flags
// so the editor and the backend agree on what each flag toggles). Mutates img.workflow.
const FLAG_TITLES = {
  detailer: ['Use Detailer'],
  upscale: ['Using USDU'],
  highrez: ['Use HighRez'],
};
export function applyRecipeFlags(recipe) {
  const wf = img.workflow;
  if (!wf || !recipe?.flags) return;
  const titleFlag = {};
  for (const [flag, titles] of Object.entries(FLAG_TITLES))
    for (const t of titles) titleFlag[t] = flag;
  for (const n of Object.values(wf)) {
    if (n?.class_type !== 'ComfySwitchNode') continue;
    const flag = titleFlag[n._meta?.title];
    if (flag && flag in recipe.flags) (n.inputs ||= {}).switch = !!recipe.flags[flag];
  }
  img.layoutNonce = (img.layoutNonce || 0) + 1;
}

export async function loadChoices() { img.choices = await get('/comfy/choices'); }

// Set + persist the selection; routes/images/+layout.svelte reacts and loads it. Refresh the active
// workflow's family so node model dropdowns re-scope to it.
export function selectWorkflow(v) {
  setActiveImage(v);
  get('/loras/bases').then((bases) => { img.wfFamily = bases.find((b) => b.key === v)?.family || ''; }).catch(() => {});
}

// --- topology edits (operate on the live workflow; caller rebuilds the graph) ---
export function connectLink(targetId, inputName, sourceId, slot = 0) {
  const t = img.workflow?.[targetId];
  if (!t || !inputName) return;
  (t.inputs ||= {})[inputName] = [String(sourceId), Number(slot) || 0];
}
// Map each OUTPUT slot of a node to its own same-typed INPUT link (model->model, clip->clip, …)
// using objectInfo, so the node can be SPLICED out: a consumer of one of its outputs gets rewired
// to that input's source. Falls back to {} for nodes with no matching passthrough.
function passthroughSources(node) {
  const def = (img.objectInfo || {})[node?.class_type];
  const outs = def?.outputs || [];
  const src = {};
  outs.forEach((o, slot) => {
    for (const f in (node.inputs || {})) {
      const v = node.inputs[f];
      if (!Array.isArray(v)) continue;
      const di = def?.inputs?.find((x) => x.name === f);
      if (di && o && di.type === o.type) { src[slot] = v; break; }
    }
  });
  return src;
}

export function deleteNode(id) {
  if (!img.workflow) return;
  // SPLICE, don't sever: before removing the node, wire each consumer of its outputs to the node's
  // matching-typed input source (deleting a LoRA bridges prev model/clip -> next). No match -> unset.
  const pass = passthroughSources(img.workflow[id]);
  delete img.workflow[id];
  for (const n of Object.values(img.workflow)) {
    for (const [k, v] of Object.entries(n.inputs || {})) {
      if (Array.isArray(v) && String(v[0]) === String(id)) {
        const repl = pass[v[1]];
        if (repl) n.inputs[k] = [String(repl[0]), Number(repl[1]) || 0];
        else delete n.inputs[k];
      }
    }
  }
}
export function deleteLink(targetId, inputName) {
  const n = img.workflow?.[targetId];
  if (n?.inputs && inputName in n.inputs) delete n.inputs[inputName];
}

// Toggle a node's bypass flag. Bypassed nodes are kept in the graph (so it's
// reversible + saved) but skipped at render time — their consumers are rerouted
// to the same-typed input source (passthrough), like ComfyUI's mute/bypass.
export function toggleBypass(id) {
  const n = img.workflow?.[id];
  if (!n) return;
  (n._meta ||= {}).bypassed = !n._meta.bypassed;
}
export function isBypassed(id) {
  return !!img.workflow?.[id]?._meta?.bypassed;
}

// A deep clone of the workflow with every bypassed node rewired out: for each of
// its outputs, consumers are repointed to the node's same-typed input source.
function executableWorkflow() {
  const g = JSON.parse(JSON.stringify($state.snapshot(img.workflow) || {}));
  for (const id of Object.keys(g)) {
    if (!g[id]?._meta?.bypassed) continue;
    // map each output slot -> the node's matching-typed input link (same splice as deleteNode)
    const passSrc = passthroughSources(g[id]);
    // reroute consumers of this node, then drop it
    for (const cid in g) {
      const ins = g[cid]?.inputs;
      if (!ins) continue;
      for (const f in ins) {
        const v = ins[f];
        if (Array.isArray(v) && v[0] === id) {
          if (passSrc[v[1]]) ins[f] = passSrc[v[1]];  // passthrough
          else delete ins[f];                          // no match → leave unset
        }
      }
    }
    delete g[id];
  }
  return g;
}

export function addNode(classType) {
  if (!img.workflow) img.workflow = {};
  const ids = Object.keys(img.workflow).map(Number).filter((n) => !Number.isNaN(n));
  const id = String((ids.length ? Math.max(...ids) : 0) + 1);
  const def = img.objectInfo?.[classType];
  const inputs = {};
  for (const di of def?.inputs || []) {
    if (di.widget && di.default != null) inputs[di.name] = di.default; // seed widget defaults
  }
  img.workflow[id] = { class_type: classType, inputs, _meta: { title: classType } };
  return id;
}

// Append a LoRA to the model/clip chain (bundled checkpoint or split UNet+CLIP),
// repointing whatever consumed the old tail onto the new loader. Mirrors the
// backend inject_models, but adds one node interactively. lora_name is left blank
// to fill in on the canvas.
export function chainLora() {
  const g = img.workflow;
  if (!g) return;
  const first = (types) => Object.keys(g).find((id) => types.includes(g[id].class_type));
  const ckpt = first(['CheckpointLoaderSimple', 'CheckpointLoader', 'CheckpointLoaderSimpleShared']);
  const unet = first(['UNETLoader', 'UnetLoaderGGUF']);
  const clip = first(['CLIPLoader', 'DualCLIPLoader', 'TripleCLIPLoader', 'CLIPLoaderGGUF', 'DualCLIPLoaderGGUF']);
  let baseModel, baseClip;
  if (ckpt) { baseModel = [ckpt, 0]; baseClip = [ckpt, 1]; }
  else if (unet) { baseModel = [unet, 0]; baseClip = clip ? [clip, 0] : null; }
  else { img.msg = { err: true, text: 'no checkpoint / UNet to chain a LoRA from' }; return; }

  const loaders = Object.keys(g).filter((id) => ['LoraLoader', 'LoraLoaderModelOnly'].includes(g[id].class_type));
  const referenced = new Set(loaders.map((id) => (Array.isArray(g[id].inputs?.model) ? g[id].inputs.model[0] : null)));
  let tailModel, tailClip;
  if (loaders.length) {
    const tail = loaders.find((id) => !referenced.has(id)) || loaders[loaders.length - 1];
    tailModel = [tail, 0]; tailClip = [tail, 1];
  } else { tailModel = baseModel; tailClip = baseClip; }

  const ids = Object.keys(g).map(Number).filter((n) => !Number.isNaN(n));
  const id = String((ids.length ? Math.max(...ids) : 0) + 1);
  const hasClip = !!tailClip;
  g[id] = hasClip
    ? { class_type: 'LoraLoader', inputs: { lora_name: '', strength_model: 1, strength_clip: 1, model: tailModel, clip: tailClip }, _meta: { title: 'LoRA' } }
    : { class_type: 'LoraLoaderModelOnly', inputs: { lora_name: '', strength_model: 1, model: tailModel }, _meta: { title: 'LoRA' } };

  for (const [nid, n] of Object.entries(g)) {
    if (nid === id) continue;
    for (const [k, v] of Object.entries(n.inputs || {})) {
      if (!Array.isArray(v) || v.length !== 2) continue;
      if (v[0] === tailModel[0] && v[1] === tailModel[1]) n.inputs[k] = [id, 0];
      else if (hasClip && v[0] === tailClip[0] && v[1] === tailClip[1]) n.inputs[k] = [id, 1];
    }
  }
  img.layoutNonce++;
}

// Drop a model file onto a graph node → file it in the right folder (if not
// already installed) and set that node's model field to it. class_type tells us
// the field + kind.
const NODE_MODEL = {
  CheckpointLoaderSimple: ['ckpt_name', 'checkpoint'], CheckpointLoader: ['ckpt_name', 'checkpoint'],
  UNETLoader: ['unet_name', 'diffusion'], UnetLoaderGGUF: ['unet_name', 'diffusion'],
  VAELoader: ['vae_name', 'vae'], LoraLoader: ['lora_name', 'lora'], LoraLoaderModelOnly: ['lora_name', 'lora'],
  CLIPLoader: ['clip_name', 'clip'], DualCLIPLoader: ['clip_name1', 'clip'],
  ControlNetLoader: ['control_net_name', 'controlnet'], UpscaleModelLoader: ['model_name', 'upscale'],
  SAMLoader: ['model_name', 'sam'], UltralyticsDetectorProvider: ['model_name', 'ultralytics'],
};

export function nodeTakesModel(classType) { return !!NODE_MODEL[classType]; }

// Heuristic CLIP/text-encoder ↔ model-architecture check. Best-effort by filename
// — catches the clear mismatches (Anima needs Qwen, Flux needs t5/clip_l).
function clipFamily(name) {
  const n = (name || '').toLowerCase();
  if (n.includes('qwen')) return 'qwen';
  if (n.includes('t5') || n.includes('umt5')) return 't5';
  if (n.includes('gemma')) return 'gemma';
  if (n.includes('clip_l') || n.includes('clip-l') || n.includes('clip_g') || n.includes('clip-g') || n.includes('vit-l') || n.includes('vit-g')) return 'clip';
  if (n.includes('llava') || n.includes('llama')) return 'llama';
  return null;
}
function diffusionModelName(wf) {
  for (const node of Object.values(wf || {})) {
    if (['UNETLoader', 'UnetLoaderGGUF'].includes(node.class_type)) return node.inputs?.unet_name || '';
    if (['CheckpointLoaderSimple', 'CheckpointLoader'].includes(node.class_type)) return node.inputs?.ckpt_name || '';
  }
  return '';
}
export function clipCompatWarning(wf, clipName) {
  const model = diffusionModelName(wf).toLowerCase();
  if (!model || !clipName) return null;
  const fam = clipFamily(clipName);
  const n = clipName.toLowerCase();
  if (model.includes('anima') || model.includes('anisnuff')) {
    // Anima needs a Qwen text encoder; anything else (incl. embeddings files) fails to load.
    if (!n.includes('qwen')) return `Anima needs a Qwen text encoder (e.g. qwen_3_06b_base) — “${clipName}” isn't one and will fail to load.`;
  } else if (model.includes('flux')) {
    if (fam && fam !== 't5' && fam !== 'clip') return `Flux expects t5xxl + clip_l — “${clipName}” looks like ${fam}.`;
  }
  return null;
}

export async function dropModelOnNode(nodeId, file) {
  const n = img.workflow?.[nodeId];
  if (!n || !file) return;
  const map = NODE_MODEL[n.class_type];
  if (!map) { img.msg = { err: true, text: `“${n.class_type}” doesn't take a model file` }; return; }
  const [field, kind] = map;
  img.msg = { text: `Adding ${file.name}…` };
  try {
    // already installed for this kind? reference it instead of re-uploading.
    const res = await post('/comfy/models/resolve', { kind, filename: file.name });
    let rel = res.data?.found ? res.data.rel : null;
    if (!rel) {
      const fd = new FormData();
      fd.append('file', file);
      const up = await fetch(`/api/comfy/models/upload?kind=${encodeURIComponent(kind)}`, { method: 'POST', body: fd });
      const d = await up.json().catch(() => ({}));
      if (!d.ok) { img.msg = { err: true, text: d.error || 'upload failed' }; return; }
      rel = d.rel;
    }
    (n.inputs ||= {})[field] = rel;
    await loadObjectInfo();   // refresh the combo so the new file is selectable
    img.layoutNonce++;
    const warn = kind === 'clip' ? clipCompatWarning(img.workflow, rel) : null;
    img.msg = warn ? { err: true, text: '⚠ ' + warn + ' (set anyway)' }
                   : { ok: true, text: `${rel} → ${n.class_type} · ${field}` };
  } catch (e) { img.msg = { err: true, text: String(e) }; }
}

export async function saveWorkflow(jsonFromEditor) {
  const payload = jsonFromEditor ?? $state.snapshot(img.workflow);
  img.msg = { text: 'Saving…' };
  const r = await post('/workflow', { model: app.activeImage, json: payload });
  if (r.data?.ok) {
    if (jsonFromEditor) img.workflow = jsonFromEditor;
    img.msg = { ok: true, text: '✓ Saved' };
  } else {
    img.msg = { err: true, text: r.data?.error || 'save failed' };
  }
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
  const graph = executableWorkflow();  // bypassed nodes rerouted out
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
  const graph = executableWorkflow();  // bypassed nodes rerouted out
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
  const snap = executableWorkflow();  // bypassed nodes rerouted out
  img.test = {
    phase: 'running', mode: 'sweep', param: param.label,
    cells: values.map((v) => ({ label: `${param.field} = ${v}`, value: v, pct: null, image: null, error: null }))
  };
  for (const cell of img.test.cells) {
    if (_testCancelled || !img.test) break;
    const g = JSON.parse(JSON.stringify(snap));
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

// Convert a ComfyUI API-format workflow into a Svelte Flow graph, laid out
// LEFT→RIGHT with ELK's `layered` algorithm (the Sugiyama framework): proper
// layer assignment, crossing minimization, and ORTHOGONAL edge routing that
// threads wires through reserved channels so they never cross node boxes.
// Ports are pinned to each node's slot rows (FIXED_POS) so handles/labels and
// the routed wire endpoints line up. Wires + handles are colored by data type.
import ELK from 'elkjs/lib/elk.bundled.js';

export const DIMS = { W: 320, HEADER: 36, SLOT: 28, ROW: 30, MULTI: 110, PAD: 12 }; // base sizes

// Metadata for virtual (computed) section keys that don't come from the workflow's subgraph prefixes.
export const VIRTUAL_SECTIONS = {
  '__top_src__': { name: 'Inputs',        color: '#27ae60', order: -1   },
  '__top_snk__': { name: 'Output',        color: '#c0392b', order: 10   },
  '__top__':     { name: 'Config',        color: '#607d8b', order: 9    },
  '__styles__':  { name: 'Style Presets', color: '#e91e8c', order: 3.5  },
};

// Effective dims given a block's prefs ({ nodeW }). Width widens the box (value
// fields fill it); multiline text boxes auto-size to their content (see
// textHeight). Header/slot/single-line rows stay compact. Used by both the
// layout and ComfyNode so handles/edges line up at any size.
export function dims(prefs = {}) {
  return { W: prefs.nodeW || DIMS.W, HEADER: DIMS.HEADER, SLOT: DIMS.SLOT, ROW: DIMS.ROW, MULTI: DIMS.MULTI, PAD: DIMS.PAD };
}

// Estimated rendered height of a multiline value at a given box width — so ELK
// reserves the right vertical space (the textarea auto-grows to match in the DOM).
export function textHeight(text, widthPx) {
  const cpl = Math.max(8, Math.floor((widthPx - 28) / 7.4)); // chars/line @ 13px mono
  const lines = String(text || '').split('\n').reduce((a, ln) => a + Math.max(1, Math.ceil((ln.length || 1) / cpl)), 0);
  return Math.min(520, Math.max(DIMS.MULTI, 24 + lines * 18));
}

// y-center of slot row i — must match ComfyNode's handle placement.
export function slotTop(i, prefs) { const d = dims(prefs); return d.HEADER + i * d.SLOT + d.SLOT / 2; }

// ComfyUI-ish palette; unknown types get a stable generated hue.
const TYPE_COLORS = {
  MODEL: '#b48aff', CLIP: '#ffd86b', VAE: '#ff6b6b', CONDITIONING: '#ffa657',
  LATENT: '#ff79c6', IMAGE: '#5ab0ff', MASK: '#57d9a3', CONTROL_NET: '#7ee787',
  CLIP_VISION: '#c9b6ff', STYLE_MODEL: '#8fd0c0', UPSCALE_MODEL: '#8fe0d0',
  SAMPLER: '#9ad0ff', SIGMAS: '#d0a0ff', GUIDER: '#9ad', NOISE: '#b9c2d0'
};
function hashHue(s) { let h = 0; for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0; return h % 360; }
export function typeColor(t) { return !t ? '#7f8aa3' : (TYPE_COLORS[t] || `hsl(${hashHue(t)} 65% 64%)`); }

export function sectionOf(id, classType = '') {
  const m = String(id).match(/^(s\d+)__/);
  if (m) return m[1];
  // Top-level nodes: classify Lora Stacker style presets separately so they get their own section.
  const ct = classType.toLowerCase();
  if (ct.includes('lora stacker') || classType === 'ImpactSwitch') return '__styles__';
  return '__top__';
}

// Build a function that returns the RESOLVED section for any workflow node.
// Top-level nodes (__top__) are split by their pipeline role:
//   - sends data to other sections but receives none → '__top_src__' (Inputs, left)
//   - receives data from other sections but sends none → '__top_snk__' (Output, right)
//   - both or neither → '__top__' (Config)
export function buildSectionMap(workflow) {
  if (!workflow || typeof workflow !== 'object') return sectionOf;
  const topSends    = new Set(); // __top__ IDs that have outgoing cross-section edges
  const topReceives = new Set(); // __top__ IDs that have incoming cross-section edges
  for (const [id, node] of Object.entries(workflow)) {
    if (!node?.inputs) continue;
    const dstBase = sectionOf(id, node.class_type || '');
    for (const [, val] of Object.entries(node.inputs)) {
      if (!Array.isArray(val) || val.length !== 2 || typeof val[1] !== 'number') continue;
      const srcId = String(val[0]);
      const srcNode = workflow[srcId];
      if (!srcNode) continue;
      const srcBase = sectionOf(srcId, srcNode.class_type || '');
      if (srcBase === dstBase) continue;
      if (srcBase === '__top__') topSends.add(srcId);
      if (dstBase === '__top__') topReceives.add(id);
    }
  }
  return (id, classType = '') => {
    const base = sectionOf(id, classType);
    if (base !== '__top__') return base;
    const sends    = topSends.has(id);
    const receives = topReceives.has(id);
    if (sends && !receives) return '__top_src__';
    if (receives && !sends) return '__top_snk__';
    return '__top__';
  };
}

// ── Section-level graph ──────────────────────────────────────────────────────
// Heuristically map an input field name to a ComfyUI data type for coloring.
export function inferType(name) {
  const n = String(name).toLowerCase();
  if (n === 'model' || n === 'base_model' || (n.endsWith('_model') && !n.includes('upscale') && !n.includes('face'))) return 'MODEL';
  if (n === 'clip' || n === 'clip1' || n === 'clip2') return 'CLIP';
  if (n === 'vae') return 'VAE';
  if (n === 'positive' || n === 'negative' || n.includes('conditioning')) return 'CONDITIONING';
  if (n.includes('latent')) return 'LATENT';
  if ((n === 'image' || n === 'pixels' || n.endsWith('_image')) && !n.includes('latent')) return 'IMAGE';
  if (n === 'mask' || n.endsWith('_mask')) return 'MASK';
  if (n.includes('upscale_model')) return 'UPSCALE_MODEL';
  if (n.includes('control_net')) return 'CONTROL_NET';
  if (n === 'sigmas' || n === 'noise') return 'SIGMAS';
  return '';
}

const SH = 36, SR = 30, SW = 240, SP = 12; // section node geometry (header/row/width/pad)

// Build a high-level graph where each node represents one workflow section and
// edges represent cross-section data flows. Used for the "Sections" view.
export function toSectionGraph(workflow, metaSections = {}) {
  if (!workflow || typeof workflow !== 'object') return { nodes: [], edges: [] };

  // Use resolved sections so __top__ is split into Inputs / Output / Config.
  const rsec = buildSectionMap(workflow);

  // Collect cross-section edges deduped by (srcSec, dstSec, dataType).
  const seen = new Set();
  const crossEdges = [];
  for (const [id, node] of Object.entries(workflow)) {
    if (!node?.inputs) continue;
    const dstSec = rsec(id, node.class_type || '');
    for (const [inputName, val] of Object.entries(node.inputs)) {
      if (!Array.isArray(val) || val.length !== 2 || typeof val[1] !== 'number') continue;
      const srcId = String(val[0]);
      const srcNode = workflow[srcId];
      if (!srcNode) continue;
      const srcSec = rsec(srcId, srcNode.class_type || '');
      if (srcSec === dstSec) continue;
      const type = inferType(inputName);
      const label = type || inputName;
      const key = `${srcSec}__${dstSec}__${label}`;
      if (seen.has(key)) continue;
      seen.add(key);
      crossEdges.push({ srcSec, dstSec, label, type });
    }
  }

  // Per-section port lists
  const secIns = {}, secOuts = {};
  for (const e of crossEdges) {
    const ek = `${e.srcSec}__${e.dstSec}__${e.label}`;
    (secOuts[e.srcSec] ||= []).push({ handleId: `out__${ek}`, label: e.label, type: e.type });
    (secIns[e.dstSec]  ||= []).push({ handleId: `in__${ek}`,  label: e.label, type: e.type });
  }

  // Count workflow nodes per resolved section
  const counts = {};
  for (const [id, n] of Object.entries(workflow)) {
    const k = rsec(id, n?.class_type || '');
    counts[k] = (counts[k] || 0) + 1;
  }

  // One node per resolved section
  const allSecs = new Set(Object.entries(workflow).map(([id, n]) => rsec(id, n?.class_type || '')));
  const nodes = [];
  for (const sec of allSecs) {
    const info = metaSections[sec] || VIRTUAL_SECTIONS[sec] || {
      name: sec.startsWith('s') ? `Subgraph ${sec.slice(1)}` : sec,
      color: '#7f8aa3', order: 9999,
    };
    const inputs  = secIns[sec]  || [];
    const outputs = secOuts[sec] || [];
    const rows = Math.max(inputs.length, outputs.length, 1);
    nodes.push({
      id: sec, type: 'section', position: { x: 0, y: 0 },
      data: { name: info.name, color: info.color, inputs, outputs,
              order: info.order ?? 9999, section: sec, count: counts[sec] || 0 },
      width: SW, height: SH + rows * SR + SP,
    });
  }

  // One edge per unique (srcSec, dstSec, type-label)
  const edges = crossEdges.map((e) => {
    const ek = `${e.srcSec}__${e.dstSec}__${e.label}`;
    const color = typeColor(e.type || '');
    return {
      id: `se__${ek}`, source: e.srcSec, sourceHandle: `out__${ek}`,
      target: e.dstSec, targetHandle: `in__${ek}`,
      data: { type: e.type, points: [] },
      style: `stroke:${color};stroke-width:1.8;opacity:.85`,
      markerEnd: { type: 'arrowclosed', color },
    };
  });

  // Top-level virtual sections are shown in the sidebar I/O pane, not the graph.
  // Removing them avoids disconnected floating nodes (Config) and noisy wires to
  // primitive string/seed nodes (Inputs / Output).
  const GRAPH_EXCLUDED = new Set(['__top_src__', '__top_snk__', '__top__']);
  const graphNodes = nodes.filter(n => !GRAPH_EXCLUDED.has(n.id));
  const graphEdges = edges.filter(e => !GRAPH_EXCLUDED.has(e.source) && !GRAPH_EXCLUDED.has(e.target));

  return { nodes: graphNodes, edges: graphEdges };
}

export async function layoutSectionGraph(nodes, edges) {
  if (!nodes.length) return { nodes, edges };

  // Sort by order so ELK's model-order strategy places sections in pipeline order (left→right).
  const sorted = [...nodes].sort((a, b) => (a.data?.order ?? 9999) - (b.data?.order ?? 9999));

  const elkNodes = sorted.map((n) => {
    const { inputs = [], outputs = [] } = n.data;
    const ports = [];
    inputs.forEach((inp, i) =>
      ports.push({ id: inp.handleId, x: 0, y: SH + i * SR + SR / 2, width: 8, height: 8,
        layoutOptions: { 'elk.port.side': 'WEST' } })
    );
    outputs.forEach((out, i) =>
      ports.push({ id: out.handleId, x: n.width, y: SH + i * SR + SR / 2, width: 8, height: 8,
        layoutOptions: { 'elk.port.side': 'EAST' } })
    );
    return { id: n.id, width: n.width, height: n.height, ports,
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' } };
  });

  // ELK only needed for node positions — SvelteFlow draws the edges as bezier curves.
  const elkEdges = edges.map((e) => ({ id: e.id, sources: [e.sourceHandle], targets: [e.targetHandle] }));

  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered', 'elk.direction': 'RIGHT',
      'elk.layered.spacing.nodeNodeBetweenLayers': '100',
      'elk.spacing.nodeNode': '50',
      // Respect input order (sorted by section.order) for layer placement of disconnected nodes.
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
    },
    children: elkNodes, edges: elkEdges,
  };

  let res;
  try { res = await elk.layout(graph); }
  catch { return { nodes, edges }; }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  // Map ELK positions back to the ORIGINAL (unsorted) nodes array so Svelte state stays stable.
  const laidNodes = nodes.map((n) => { const c = byId[n.id]; return c ? { ...n, position: { x: c.x, y: c.y } } : n; });

  return { nodes: laidNodes, edges };
}

// ── Raw node graph ───────────────────────────────────────────────────────────
// hintUsedSlots: {nodeId → Set<slotNumber>} — pre-computed external slot usage.
// Pass this in drill-in mode so nodes whose outputs only go to other sections
// still get the right number of output handles (instead of defaulting to slot 0).
export function toGraph(workflow, objectInfo = {}, nodeSizes = {}, hintUsedSlots = {}) {
  const nodes = [];
  const edges = [];
  if (!workflow || typeof workflow !== 'object') return { nodes, edges };

  // Seed usedSlots with external consumption hints before processing internal edges.
  const usedSlots = {};
  for (const [id, slots] of Object.entries(hintUsedSlots)) {
    usedSlots[id] = new Set(slots);
  }
  const raw = [];
  for (const [id, node] of Object.entries(workflow)) {
    if (!node || typeof node !== 'object') continue;
    const def = objectInfo?.[node.class_type] || null;
    const d = dims(nodeSizes[id] || {}); // this block's own size
    const connInputs = [];
    const widgets = [];
    const seen = new Set();
    const consider = (name, di) => {
      if (seen.has(name)) return;
      seen.add(name);
      const val = node.inputs?.[name];
      const linked = Array.isArray(val) && val.length === 2 && typeof val[1] === 'number';
      const isWidget = di ? di.widget : !linked;
      if (!isWidget) {
        // connection slot — show if wired, or required (so it's wireable even when empty)
        if (!linked && di && di.required === false) return;
        let otype = di?.type || '';
        if (linked) {
          const slot = val[1];
          (usedSlots[String(val[0])] ||= new Set()).add(slot);
          const srcClass = workflow[String(val[0])]?.class_type;
          otype = objectInfo?.[srcClass]?.outputs?.[slot]?.type || otype;
          const color = typeColor(otype);
          edges.push({
            id: `e-${val[0]}-${slot}-${id}-${name}`,
            source: String(val[0]), sourceHandle: `out-${slot}`,
            target: String(id), targetHandle: name, label: name,
            data: { type: otype },
            style: `stroke:${color};stroke-width:2;opacity:.92`,
            markerEnd: { type: 'arrowclosed', color }
          });
        }
        connInputs.push({ name, type: otype, linked });
      } else {
        const has = val !== undefined;
        if (!has && !(di && di.required)) return; // hide optional unset widgets
        const v = has ? val : di?.default;

        if (!di) {
          // Without a schema, skip ComfyUI's positional serialization keys (widget_1, widget_2…)
          // and uninformative empty/null values — these are internal state, not user-facing data.
          if (/^widget_\d+$/.test(name)) return;
          if (v === '' || v === null || v === undefined) return;
        }

        // Normalize ComfyUI string-booleans ('True'/'False') so they render as checkboxes.
        const normV = v === 'True' ? true : v === 'False' ? false : v;

        // Some node types always warrant a textarea regardless of current string length —
        // Lora Stacker's `text` holds LoRA tag stacks that grow as stacks are chained.
        const alwaysMultiline = !di
          && name === 'text'
          && (node.class_type || '').includes('Lora Stacker');

        const multiline = di
          ? di.multiline && di.type === 'STRING'
          : alwaysMultiline || (typeof normV === 'string' && (normV.includes('\n') || normV.length > 40));
        const options = di?.type === 'COMBO' ? di.options : null;
        // text-encode nodes get an embedding-picker chip row above the textarea;
        // reserve a little extra height so it doesn't crowd the node below.
        const embeds = multiline && (node.class_type || '').includes('CLIPTextEncode');
        const h = multiline ? textHeight(normV, d.W) + (embeds ? 34 : 0) : d.ROW;
        widgets.push({ name, value: normV, multiline, options, embeds, h });
      }
    };
    // schema order first (so unset required slots appear), then any extras present on the node
    for (const di of def?.inputs || []) consider(di.name, di);
    for (const name of Object.keys(node.inputs || {})) consider(name, def?.inputs?.find((x) => x.name === name));
    raw.push({ id: String(id), title: node._meta?.title || node.class_type || id, classType: node.class_type || '', def, connInputs, widgets, d });
  }

  for (const r of raw) {
    const d = r.d;
    let outputs;
    if (r.def?.outputs?.length) {
      outputs = r.def.outputs.map((o, i) => ({ slot: i, name: o.name, type: o.type }));
    } else {
      const s = [...(usedSlots[r.id] || [])].sort((a, b) => a - b);
      outputs = (s.length ? s : [0]).map((x) => ({ slot: x, name: `out ${x}`, type: '' }));
    }
    const slotRows = Math.max(r.connInputs.length, outputs.length);
    const wSum = r.widgets.reduce((a, b) => a + b.h, 0);
    nodes.push({
      id: r.id, type: 'comfy', position: { x: 0, y: 0 },
      data: { title: r.title, classType: r.classType, connInputs: r.connInputs, outputs, widgets: r.widgets, slotRows, section: sectionOf(r.id, r.classType) },
      width: d.W, height: d.HEADER + slotRows * d.SLOT + wSum + d.PAD
    });
  }
  return { nodes, edges };
}

const elk = new ELK();

export async function layoutGraph(nodes, edges, nodeSizes = {}) {
  if (!nodes.length) return { nodes, edges };

  // Since edges are bezier curves (no orthogonal routing), ELK only needs the
  // graph topology for layer assignment — no port IDs required. This avoids
  // failures when a node's output slot is only consumed outside the current
  // subset and was never registered in usedSlots.
  const elkNodes = nodes.map((n) => ({ id: n.id, width: n.width, height: n.height }));

  // Deduplicate parallel edges (same source→target) so ELK doesn't complain.
  const seenPairs = new Set();
  const elkEdges = [];
  for (const e of edges) {
    const key = `${e.source}→${e.target}`;
    if (seenPairs.has(key)) continue;
    seenPairs.add(key);
    elkEdges.push({ id: e.id, sources: [e.source], targets: [e.target] });
  }

  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.layered.spacing.nodeNodeBetweenLayers': '90',
      'elk.spacing.nodeNode': '34',
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES'
    },
    children: elkNodes,
    edges: elkEdges
  };

  let res;
  try {
    res = await elk.layout(graph);
  } catch {
    return { nodes, edges };
  }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  const laidNodes = nodes.map((n) => {
    const c = byId[n.id];
    return c ? { ...n, position: { x: c.x, y: c.y } } : n;
  });

  // Node positions from ELK, edges drawn as SvelteFlow bezier curves.
  return { nodes: laidNodes, edges };
}

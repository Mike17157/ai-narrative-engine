// Convert a ComfyUI API-format workflow into a Svelte Flow graph, laid out
// LEFT→RIGHT with ELK's `layered` algorithm (the Sugiyama framework): proper
// layer assignment, crossing minimization, and ORTHOGONAL edge routing that
// threads wires through reserved channels so they never cross node boxes.
// Ports are pinned to each node's slot rows (FIXED_POS) so handles/labels and
// the routed wire endpoints line up. Wires + handles are colored by data type.
import ELK from 'elkjs/lib/elk.bundled.js';

export const DIMS = { W: 320, HEADER: 36, SLOT: 28, ROW: 30, MULTI: 110, PAD: 12 }; // base sizes

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

export function toGraph(workflow, objectInfo = {}, nodeSizes = {}) {
  const nodes = [];
  const edges = [];
  if (!workflow || typeof workflow !== 'object') return { nodes, edges };

  const usedSlots = {};
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
        const multiline = di
          ? di.multiline && di.type === 'STRING'
          : typeof v === 'string' && (v.includes('\n') || v.length > 40);
        const options = di?.type === 'COMBO' ? di.options : null;
        // text-encode nodes get an embedding-picker chip row above the textarea;
        // reserve a little extra height so it doesn't crowd the node below.
        const embeds = multiline && (node.class_type || '').includes('CLIPTextEncode');
        const h = multiline ? textHeight(v, d.W) + (embeds ? 34 : 0) : d.ROW;
        widgets.push({ name, value: v, multiline, options, embeds, h });
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
      data: { title: r.title, classType: r.classType, connInputs: r.connInputs, outputs, widgets: r.widgets, slotRows },
      width: d.W, height: d.HEADER + slotRows * d.SLOT + wSum + d.PAD
    });
  }
  return { nodes, edges };
}

const elk = new ELK();

export async function layoutGraph(nodes, edges, nodeSizes = {}) {
  if (!nodes.length) return { nodes, edges };

  // Ports are pinned to slot-row positions (per this block's size); ELK keeps
  // them and routes wires to those exact points (endpoints meet the handles).
  const elkNodes = nodes.map((n) => {
    const p = nodeSizes[n.id] || {};
    const ports = [];
    n.data.connInputs.forEach((inp, i) =>
      ports.push({ id: `${n.id}::in::${inp.name}`, x: 0, y: slotTop(i, p), width: 8, height: 8,
        layoutOptions: { 'elk.port.side': 'WEST' } }));
    n.data.outputs.forEach((o, i) =>
      ports.push({ id: `${n.id}::out::${o.slot}`, x: n.width, y: slotTop(i, p), width: 8, height: 8,
        layoutOptions: { 'elk.port.side': 'EAST' } }));
    return { id: n.id, width: n.width, height: n.height, ports, layoutOptions: { 'elk.portConstraints': 'FIXED_POS' } };
  });

  const elkEdges = edges.map((e) => ({
    id: e.id,
    sources: [`${e.source}::out::${String(e.sourceHandle).slice(4)}`],
    targets: [`${e.target}::in::${e.targetHandle}`]
  }));

  const graph = {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': '90',
      'elk.spacing.nodeNode': '34',
      'elk.spacing.edgeNode': '18',
      'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
      // Bias crossing-minimization toward our reading order (top-left → bottom-right).
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES'
    },
    children: elkNodes,
    edges: elkEdges
  };

  let res;
  try {
    res = await elk.layout(graph);
  } catch {
    return { nodes, edges }; // leave as-is on layout failure
  }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  const laidNodes = nodes.map((n) => {
    const c = byId[n.id];
    return c ? { ...n, position: { x: c.x, y: c.y } } : n;
  });

  // ELK returns orthogonal edge routes (start → bends → end) in absolute coords.
  const edgeById = {};
  for (const ee of res.edges || []) edgeById[ee.id] = ee;
  const laidEdges = edges.map((e) => {
    const sec = edgeById[e.id]?.sections?.[0];
    const pts = sec ? [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint] : [];
    return { ...e, type: 'routed', data: { ...(e.data || {}), points: pts } };
  });

  return { nodes: laidNodes, edges: laidEdges };
}

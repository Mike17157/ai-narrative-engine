// Build a WHOLE-STORY Svelte Flow graph.
//
// Arc layout:
//   Arcs stack VERTICALLY (top→bottom story order) in a single column.
//   • Timelines (arc.timelines present): 2-3 vertical COLUMNS per arc, chapters
//     flow top→bottom within each column; horizontal dashed-amber edges for transitions.
//   • Legacy flat chain (arc.nodes only): single vertical column using ELK layered.
import ELK from 'elkjs/lib/elk.bundled.js';

export const BEAT_W = 300;

// ── Arc and timeline colours ───────────────────────────────────────────────
export const ARC_COLORS = [
  { stroke: 'rgba(109,140,255,.85)', fill: 'rgba(109,140,255,.18)', label: '#6d8cff' },  // blue
  { stroke: 'rgba(255,140,109,.85)', fill: 'rgba(255,140,109,.18)', label: '#ff8c6d' },  // amber
  { stroke: 'rgba(100,210,130,.8)',  fill: 'rgba(100,210,130,.16)', label: '#64d282' },  // green
  { stroke: 'rgba(200,110,255,.8)',  fill: 'rgba(200,110,255,.16)', label: '#c86eff' },  // purple
  { stroke: 'rgba(80,220,200,.8)',   fill: 'rgba(80,220,200,.16)',  label: '#50dcc8' },  // teal
];

export const TL_COLORS = [
  { stroke: 'rgba(109,180,255,.8)',  label: '#6db4ff', bg: 'rgba(109,180,255,.07)' },
  { stroke: 'rgba(255,140,109,.8)',  label: '#ff8c6d', bg: 'rgba(255,140,109,.07)' },
  { stroke: 'rgba(140,255,140,.75)', label: '#8cff8c', bg: 'rgba(140,255,140,.06)' },
];

// ── Height estimator ──────────────────────────────────────────────────────
export function estimateBeatHeight(node) {
  const CHARS_TITLE = 28;
  const CHARS_BODY  = 37;
  const LINE_H = 20;
  let h = 108 + 22 + 28;
  const titleLines = Math.max(1, Math.ceil((node.title || '').length / CHARS_TITLE));
  h += Math.min(titleLines, 3) * LINE_H;
  if (node.emotional_core) h += 5 + 16;
  if (node.hook) {
    const hookLines = Math.max(1, Math.ceil(node.hook.length / CHARS_BODY));
    h += 5 + Math.min(hookLines, 8) * LINE_H;
  }
  if (node.characters?.length) h += 5 + 22;
  return Math.max(200, h);
}

// ── Layout constants ──────────────────────────────────────────────────────
const ARC_GAP    = 56;   // vertical gap between arc lanes
const LANE_PAD   = 18;   // inner padding inside a lane box
const LANE_HEAD  = 40;   // room for arc lane header
const EMPTY_W    = 300;
const EMPTY_H    = 120;

// Timeline layout (vertical columns of chapters)
const TL_HEAD_H  = 58;   // height of timeline header at top
const TL_HEAD_GAP = 8;   // gap between header and first chapter
const CH_GAP_V   = 36;   // vertical gap between chapters in a column
const TL_GAP_H   = 32;   // horizontal gap between timeline columns

// ELK layout (single-column arcs)
const ELK_NODE_GAP   = 52;
const ELK_LAYER_GAP  = 52;

const elk = new ELK();
const gid   = (arcId, nodeId) => `${arcId}::${nodeId}`;
const tlGid = (arcId, tlId, nodeId) => `${arcId}::${tlId}::${nodeId}`;

function locBgMap(story) {
  const m = {};
  for (const l of (story?.locations || [])) {
    if (!l?.background) continue;
    if (l.id)   m[String(l.id).toLowerCase()]   = l.background;
    if (l.name) m[String(l.name).toLowerCase()] = l.background;
  }
  return m;
}
const bgFor = (loc, map) => (loc ? map[String(loc).toLowerCase()] || null : null);

function arcsOf(story) {
  if (story?.arcs?.length) return story.arcs;
  const beats = story?.storyboard?.beats || [];
  if (!beats.length) return [];
  const nodes = {};
  beats.forEach((b, i) => {
    nodes[`b${i}`] = {
      title: b.title || `Chapter ${i + 1}`,
      hook: b.summary || '',
      characters: b.characters || [],
      location: b.location || '',
      next: i < beats.length - 1 ? [`b${i + 1}`] : [],
    };
  });
  return [{ id: '_board', name: story.name || 'Story', nodes, start: 'b0', _flat: true }];
}

function walkNodes(nodeMap, startId) {
  const ordered = [];
  let cur = startId || Object.keys(nodeMap)[0];
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const nd = nodeMap[cur];
    if (!nd) break;
    ordered.push({ id: cur, ...nd });
    cur = nd.next?.[0] || null;
  }
  return ordered;
}

// ── Timeline layout (vertical columns, top→bottom) ────────────────────────
function layoutArcTimelines(arc, arcIdx, bgMap) {
  const timelines = arc.timelines || [];
  if (!timelines.length) return { nodes: [], edges: [], w: EMPTY_W, h: EMPTY_H };

  const arcColor = ARC_COLORS[arcIdx % ARC_COLORS.length];
  const nodes = [];
  const edges = [];

  const tlData = timelines.map((tl, tlIdx) => ({
    ...tl,
    tlIdx,
    color: TL_COLORS[tlIdx] || TL_COLORS[0],
    ordered: walkNodes(tl.nodes || {}, tl.start),
  }));

  let maxH = 0;
  let totalW = 0;

  tlData.forEach((tl) => {
    const colX = tl.tlIdx * (BEAT_W + TL_GAP_H);

    // Header node for this timeline column
    nodes.push({
      id: `tl-head-${arc.id}-${tl.id}`,
      type: 'timelineHead',
      position: { x: colX, y: 0 },
      data: { name: tl.name, premise: tl.premise, color: tl.color.label, bg: tl.color.bg, tlIdx: tl.tlIdx, vertical: true },
      width: BEAT_W,
      height: TL_HEAD_H,
      draggable: false, selectable: false,
      zIndex: 1,
    });

    // Chapter cards stacked top→bottom
    tl.ordered.forEach((node, chIdx) => {
      const estH = estimateBeatHeight(node);
      const x = colX;
      const y = TL_HEAD_H + TL_HEAD_GAP + chIdx * (estimateBeatHeight(node) + CH_GAP_V);
      const nodeId = tlGid(arc.id, tl.id, node.id);

      nodes.push({
        id: nodeId,
        type: 'beat',
        position: { x, y },
        data: {
          ...node, arcId: arc.id, arcIdx, timelineId: tl.id, nodeId: node.id,
          bg: bgFor(node.location, bgMap),
          tlColor: tl.color.stroke, tlIdx: tl.tlIdx,
          arcColor: arcColor.label,
        },
        width: BEAT_W,
        draggable: false, selectable: true,
        zIndex: 1,
      });
      maxH = Math.max(maxH, y + estH);

      // Within-column edge (bottom → top)
      const nextId = node.next?.[0];
      if (nextId && (tl.nodes || {})[nextId]) {
        edges.push({
          id: `e-${arc.id}-${tl.id}-${node.id}-${nextId}`,
          source: nodeId,
          sourceHandle: 'out-bottom',
          target: tlGid(arc.id, tl.id, nextId),
          targetHandle: 'in-top',
          type: 'smoothstep',
          style: `stroke:${tl.color.stroke};stroke-width:2.5`,
          markerEnd: { type: 'arrowclosed', color: tl.color.stroke, width: 20, height: 20 },
        });
      }
    });

    totalW = Math.max(totalW, colX + BEAT_W);
  });

  // Transition edges (horizontal, dashed amber) between timeline columns
  for (const tr of (arc.transitions || [])) {
    const srcId = tlGid(arc.id, tr.from_timeline, tr.from_node);
    const tgtId = tlGid(arc.id, tr.to_timeline,   tr.to_node);
    const srcTl = timelines.find(t => t.id === tr.from_timeline);
    const tgtTl = timelines.find(t => t.id === tr.to_timeline);
    if (!srcTl?.nodes?.[tr.from_node] || !tgtTl?.nodes?.[tr.to_node]) continue;

    const srcIdx = timelines.indexOf(srcTl);
    const tgtIdx = timelines.indexOf(tgtTl);
    const goingRight = tgtIdx > srcIdx;

    edges.push({
      id: `tr-${srcId}-${tgtId}`,
      source: srcId,
      sourceHandle: goingRight ? 'out-right' : 'out-left',
      target: tgtId,
      targetHandle: goingRight ? 'in-left' : 'in-right',
      type: 'smoothstep',
      data: { condition: tr.condition },
      label: tr.condition,
      labelBgStyle: { fill: 'rgba(20,22,34,.85)', rx: 5, ry: 5 },
      labelStyle: { fontSize: 10, fill: 'rgba(255,200,90,.9)' },
      style: 'stroke:rgba(255,200,90,.6);stroke-width:1.5;stroke-dasharray:5,3',
      markerEnd: { type: 'arrowclosed', color: 'rgba(255,200,90,.75)', width: 16, height: 16 },
      zIndex: 2,
    });
  }

  return { nodes, edges, w: totalW, h: maxH };
}

// ── Legacy flat layout (ELK layered, top→bottom) ──────────────────────────
async function layoutArcLegacy(arc, arcIdx, bgMap) {
  const nodeMap = arc?.nodes || {};
  const keys = Object.keys(nodeMap);
  if (!keys.length) return { nodes: [], edges: [], w: EMPTY_W, h: EMPTY_H };

  const arcColor = ARC_COLORS[arcIdx % ARC_COLORS.length];

  const svelteNodes = keys.map((id) => ({
    id: gid(arc.id, id),
    type: 'beat',
    position: { x: 0, y: 0 },
    data: {
      ...nodeMap[id], arcId: arc.id, arcIdx, nodeId: id,
      bg: bgFor(nodeMap[id].location, bgMap),
      arcColor: arcColor.label,
    },
    width: BEAT_W,
    draggable: false, selectable: true,
    zIndex: 1,
  }));

  const edges = [];
  for (const id of keys) {
    for (const nextId of (nodeMap[id].next || [])) {
      if (nodeMap[nextId]) {
        edges.push({
          id: `e-${gid(arc.id, id)}-${gid(arc.id, nextId)}`,
          source: gid(arc.id, id),
          sourceHandle: 'out-bottom',
          target: gid(arc.id, nextId),
          targetHandle: 'in-top',
          type: 'smoothstep',
          style: `stroke:${arcColor.stroke};stroke-width:2.5`,
          markerEnd: { type: 'arrowclosed', color: arcColor.stroke, width: 22, height: 22 },
        });
      }
    }
  }

  // ELK layout for node positions
  const elkChildren = keys.map((id) => {
    const h = estimateBeatHeight(nodeMap[id]);
    return {
      id: gid(arc.id, id),
      width: BEAT_W, height: h,
      ports: [
        { id: `${gid(arc.id, id)}::out-bottom`, x: BEAT_W / 2, y: h,    width: 1, height: 1, layoutOptions: { 'elk.port.side': 'SOUTH' } },
        { id: `${gid(arc.id, id)}::in-top`,     x: BEAT_W / 2, y: 0,    width: 1, height: 1, layoutOptions: { 'elk.port.side': 'NORTH' } },
      ],
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' },
    };
  });

  const graph = {
    id: `arc-${arc.id}`,
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': String(ELK_LAYER_GAP),
      'elk.spacing.nodeNode': String(ELK_NODE_GAP),
    },
    children: elkChildren,
    edges: edges.map((e) => ({
      id: e.id,
      sources: [`${e.source}::out-bottom`],
      targets: [`${e.target}::in-top`],
    })),
  };

  let res;
  try { res = await elk.layout(graph); } catch { return { nodes: svelteNodes, edges, w: BEAT_W, h: 220 }; }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  let w = 0, h = 0;
  const laidNodes = svelteNodes.map((n) => {
    const c = byId[n.id];
    if (!c) return n;
    w = Math.max(w, c.x + BEAT_W);
    h = Math.max(h, c.y + estimateBeatHeight((nodeMap[Object.keys(nodeMap).find(k => gid(arc.id, k) === n.id)] || {})));
    return { ...n, position: { x: c.x, y: c.y } };
  });
  return { nodes: laidNodes, edges, w: w || BEAT_W, h: h || 220 };
}

// ── Whole-story graph (arcs stacked vertically) ───────────────────────────
export async function buildStoryGraph(story) {
  const arcs = arcsOf(story);
  if (!arcs.length) return { nodes: [], edges: [] };
  const bgMap = locBgMap(story);

  const laid = await Promise.all(arcs.map((arc, i) =>
    arc.timelines?.length
      ? Promise.resolve(layoutArcTimelines(arc, i, bgMap))
      : layoutArcLegacy(arc, i, bgMap)
  ));

  const nodes = [];
  const edges = [];
  let yCursor = 0;

  arcs.forEach((arc, i) => {
    const { nodes: aNodes, edges: aEdges, w, h } = laid[i];
    const flat  = !!arc._flat;
    const arcColor = ARC_COLORS[i % ARC_COLORS.length];

    const laneW = w + LANE_PAD * 2;
    const laneH = h + LANE_HEAD + LANE_PAD;
    const dx    = flat ? 0 : LANE_PAD;
    const dy    = yCursor + (flat ? 0 : LANE_HEAD);

    if (!flat) {
      nodes.push({
        id: `lane-${arc.id}`,
        type: 'arcGroup',
        position: { x: 0, y: yCursor },
        data: {
          name: arc.name || `Arc ${i + 1}`,
          dramatic_function: arc.dramatic_function,
          divergence_axis: arc.divergence_axis,
          arcId: arc.id, arcIdx: i,
          empty: !aNodes.length,
          laneW, laneH,
          arcColor: arcColor.label,
        },
        width: laneW, height: laneH,
        draggable: false, selectable: true,
        zIndex: 0,
      });
    }

    for (const n of aNodes) nodes.push({ ...n, position: { x: n.position.x + dx, y: n.position.y + dy } });
    for (const e of aEdges) edges.push(e);

    yCursor += (flat ? h : laneH) + ARC_GAP;
  });

  return { nodes, edges };
}

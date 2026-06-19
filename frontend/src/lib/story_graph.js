// Build a WHOLE-STORY Svelte Flow graph: every arc is laid out as its own
// top→bottom column ("lane"), and the lanes sit side by side left→right in story
// order. Each arc's beats are laid out with ELK (layered, orthogonal edges) in
// arc-local coordinates, then translated into absolute flow coordinates by the
// lane's x-offset — so RoutedEdge (which draws ELK's absolute points) keeps
// working without Svelte Flow parent nesting.
//
// Node ids are namespaced `${arcId}::${nodeId}` because arc node-dicts reuse
// keys (n1, n2…) across arcs; the original {arcId, arcIdx, nodeId} is kept on
// node.data so click handlers can map back to the story.
import ELK from 'elkjs/lib/elk.bundled.js';

// Node box. IMPORTANT: BEAT_H must equal the fixed height of ArcBeatNode's
// .beat-node in CSS — ELK lays out (and edges anchor) to this exact height, so a
// mismatch makes the arrows float off the cards. Keep the two in sync.
export const BEAT_W = 300;
export const BEAT_H = 188;

const LANE_GAP = 72;      // horizontal gap between arc lanes
const LANE_PAD = 18;      // inner padding inside a lane box
const LANE_HEAD = 40;     // room at the lane top for its label
const EMPTY_W = 300;      // placeholder lane size for un-expanded arcs
const EMPTY_H = 120;

const elk = new ELK();

const gid = (arcId, nodeId) => `${arcId}::${nodeId}`;

// Map a location reference (id or name, case-insensitive) → its background image
// URL, so each beat card can render the scene of the place it happens in.
function locBgMap(story) {
  const m = {};
  for (const l of (story?.locations || [])) {
    if (!l?.background) continue;
    if (l.id) m[String(l.id).toLowerCase()] = l.background;
    if (l.name) m[String(l.name).toLowerCase()] = l.background;
  }
  return m;
}
const bgFor = (loc, map) => (loc ? map[String(loc).toLowerCase()] || null : null);

// Older stories have no arcs — only a flat storyboard.beats list. Present them as
// one implicit arc (a linear chain) so the canvas is universal.
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

// Lay out ONE arc's beats in arc-local coordinates. Returns laid nodes/edges
// (positions/points relative to the arc origin) and the arc's content bbox.
async function layoutArc(arc, arcIdx, bgMap) {
  const nodeMap = arc?.nodes || {};
  const keys = Object.keys(nodeMap);
  if (!keys.length) return { nodes: [], edges: [], w: EMPTY_W, h: EMPTY_H };

  const nodes = keys.map((id) => ({
    id: gid(arc.id, id),
    type: 'beat',
    position: { x: 0, y: 0 },
    data: { ...nodeMap[id], arcId: arc.id, arcIdx, nodeId: id, bg: bgFor(nodeMap[id].location, bgMap) },
    width: BEAT_W,
    height: BEAT_H,
    zIndex: 1,
  }));

  const edges = [];
  for (const id of keys) {
    for (const nextId of (nodeMap[id].next || [])) {
      if (nodeMap[nextId]) {
        edges.push({
          id: `e-${gid(arc.id, id)}-${gid(arc.id, nextId)}`,
          source: gid(arc.id, id),
          sourceHandle: 'out',
          target: gid(arc.id, nextId),
          targetHandle: 'in',
          type: 'routed',
          style: 'stroke:rgba(129,156,255,.75);stroke-width:2.5',
          markerEnd: { type: 'arrowclosed', color: 'rgba(129,156,255,.95)', width: 22, height: 22 },
        });
      }
    }
  }

  const graph = {
    id: `arc-${arc.id}`,
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': '56',
      'elk.spacing.nodeNode': '40',
      'elk.spacing.edgeNode': '16',
      'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
    },
    children: nodes.map((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
      ports: [
        { id: `${n.id}::in`,  x: n.width / 2, y: 0,        width: 1, height: 1, layoutOptions: { 'elk.port.side': 'NORTH' } },
        { id: `${n.id}::out`, x: n.width / 2, y: n.height,  width: 1, height: 1, layoutOptions: { 'elk.port.side': 'SOUTH' } },
      ],
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' },
    })),
    edges: edges.map((e) => ({ id: e.id, sources: [`${e.source}::out`], targets: [`${e.target}::in`] })),
  };

  let res;
  try { res = await elk.layout(graph); }
  catch { return { nodes, edges, w: BEAT_W, h: BEAT_H }; }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  let w = 0, h = 0;
  const laidNodes = nodes.map((n) => {
    const c = byId[n.id];
    if (!c) return n;
    w = Math.max(w, c.x + n.width);
    h = Math.max(h, c.y + n.height);
    return { ...n, position: { x: c.x, y: c.y } };
  });

  const edgeById = {};
  for (const ee of res.edges || []) edgeById[ee.id] = ee;
  const laidEdges = edges.map((e) => {
    const sec = edgeById[e.id]?.sections?.[0];
    const pts = sec ? [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint] : [];
    return { ...e, data: { ...(e.data || {}), points: pts } };
  });

  return { nodes: laidNodes, edges: laidEdges, w: w || BEAT_W, h: h || BEAT_H };
}

// Build the whole-story graph: lay out each arc, then place the lanes left→right.
export async function buildStoryGraph(story) {
  const arcs = arcsOf(story);
  if (!arcs.length) return { nodes: [], edges: [] };
  const bgMap = locBgMap(story);

  const laid = await Promise.all(arcs.map((arc, i) => layoutArc(arc, i, bgMap)));

  const nodes = [];
  const edges = [];
  let xCursor = 0;

  arcs.forEach((arc, i) => {
    const { nodes: aNodes, edges: aEdges, w, h } = laid[i];
    const laneW = w + LANE_PAD * 2;
    const laneH = h + LANE_HEAD + LANE_PAD;
    const flat = !!arc._flat;
    const dx = xCursor + (flat ? 0 : LANE_PAD);
    const dy = flat ? 0 : LANE_HEAD;

    // Lane background/label node (rendered behind the beats). Skipped for the
    // synthetic single lane of an arc-less (flat-beats) story.
    if (!flat) {
      nodes.push({
        id: `lane-${arc.id}`,
        type: 'arcGroup',
        position: { x: xCursor, y: 0 },
        data: { name: arc.name || `Arc ${i + 1}`, dramatic_function: arc.dramatic_function,
                arcId: arc.id, arcIdx: i, empty: !aNodes.length, laneW, laneH },
        width: laneW,
        height: laneH,
        draggable: false,
        selectable: true,
        zIndex: 0,
      });
    }

    // Translate this arc's beats + edge points into absolute coordinates.
    for (const n of aNodes) nodes.push({ ...n, position: { x: n.position.x + dx, y: n.position.y + dy } });
    for (const e of aEdges) {
      const pts = (e.data?.points || []).map((p) => ({ x: p.x + dx, y: p.y + dy }));
      edges.push({ ...e, data: { ...e.data, points: pts } });
    }

    xCursor += laneW + LANE_GAP;
  });

  return { nodes, edges };
}

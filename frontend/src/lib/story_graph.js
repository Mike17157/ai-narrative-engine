// Convert an arc's {id: ArcBeat} node dict into a Svelte Flow graph,
// laid out TOP→BOTTOM with ELK's layered algorithm. Branches (next.length > 1)
// fan out; merges converge. RoutedEdge draws the orthogonal paths from ELK.
import ELK from 'elkjs/lib/elk.bundled.js';

export const BEAT_W = 264;
export const BEAT_H = 112;

const elk = new ELK();

export function toStoryGraph(arc) {
  const nodes = [];
  const edges = [];
  const nodeMap = arc?.nodes || {};
  const keys = Object.keys(nodeMap);
  if (!keys.length) return { nodes, edges };

  for (const id of keys) {
    const beat = nodeMap[id];
    nodes.push({
      id,
      type: 'beat',
      position: { x: 0, y: 0 },
      data: { ...beat },
      width: BEAT_W,
      height: BEAT_H
    });
    for (const nextId of (beat.next || [])) {
      if (nodeMap[nextId]) {
        edges.push({
          id: `e-${id}-${nextId}`,
          source: id,
          sourceHandle: 'out',
          target: nextId,
          targetHandle: 'in',
          type: 'routed',
          style: 'stroke:rgba(109,140,255,.55);stroke-width:2',
          markerEnd: { type: 'arrowclosed', color: 'rgba(109,140,255,.75)' }
        });
      }
    }
  }

  return { nodes, edges };
}

export async function layoutStoryGraph(nodes, edges) {
  if (!nodes.length) return { nodes, edges };

  const elkNodes = nodes.map((n) => ({
    id: n.id,
    width: n.width,
    height: n.height,
    ports: [
      { id: `${n.id}::in`,  x: n.width / 2, y: 0,         width: 1, height: 1, layoutOptions: { 'elk.port.side': 'NORTH' } },
      { id: `${n.id}::out`, x: n.width / 2, y: n.height,   width: 1, height: 1, layoutOptions: { 'elk.port.side': 'SOUTH' } }
    ],
    layoutOptions: { 'elk.portConstraints': 'FIXED_POS' }
  }));

  const elkEdges = edges.map((e) => ({
    id: e.id,
    sources: [`${e.source}::out`],
    targets: [`${e.target}::in`]
  }));

  const graph = {
    id: 'story-arc',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'DOWN',
      'elk.edgeRouting': 'ORTHOGONAL',
      'elk.layered.spacing.nodeNodeBetweenLayers': '56',
      'elk.spacing.nodeNode': '40',
      'elk.spacing.edgeNode': '16',
      'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
      'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES'
    },
    children: elkNodes,
    edges: elkEdges
  };

  let res;
  try { res = await elk.layout(graph); }
  catch { return { nodes, edges }; }

  const byId = {};
  for (const c of res.children || []) byId[c.id] = c;
  const laidNodes = nodes.map((n) => {
    const c = byId[n.id];
    return c ? { ...n, position: { x: c.x, y: c.y } } : n;
  });

  const edgeById = {};
  for (const ee of res.edges || []) edgeById[ee.id] = ee;
  const laidEdges = edges.map((e) => {
    const sec = edgeById[e.id]?.sections?.[0];
    const pts = sec ? [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint] : [];
    return { ...e, data: { ...(e.data || {}), points: pts } };
  });

  return { nodes: laidNodes, edges: laidEdges };
}

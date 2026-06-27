// Converters between the editable story graph (the Console canvas shape) and the
// persisted models (flat storyboard.beats[] and arcs[].nodes DAG). One canonical
// graph shape is shared by setup, the storyboard step, and the saved story.
//
// Graph shape:
//   { logline, wound, lie, truth, nodes: [{ id, title, start, end, what_happened, next[], x, y }] }

// Topological order (Kahn); falls back to input order if there's a cycle.
export function topoOrder(nodes) {
  const ids = nodes.map((n) => n.id);
  const idset = new Set(ids);
  const indeg = new Map(ids.map((id) => [id, 0]));
  for (const n of nodes) for (const t of (n.next || [])) if (idset.has(t)) indeg.set(t, (indeg.get(t) || 0) + 1);
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const queue = ids.filter((id) => (indeg.get(id) || 0) === 0);
  const out = [];
  const seen = new Set();
  while (queue.length) {
    const id = queue.shift();
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(byId.get(id));
    for (const t of (byId.get(id)?.next || [])) {
      if (!idset.has(t)) continue;
      indeg.set(t, (indeg.get(t) || 0) - 1);
      if ((indeg.get(t) || 0) <= 0) queue.push(t);
    }
  }
  for (const n of nodes) if (!seen.has(n.id)) out.push(n);   // cycle remainder
  return out;
}

function firstNodeId(nodes) {
  const targeted = new Set();
  for (const n of nodes) for (const t of (n.next || [])) targeted.add(t);
  return (nodes.find((n) => !targeted.has(n.id)) || nodes[0])?.id || '';
}

// ── persisted → graph ───────────────────────────────────────────────────────

// Flat storyboard.beats[] → graph (linear chain). wound/lie/truth start empty (edited on the graph).
export function boardToGraph(board) {
  const beats = board?.beats || [];
  const nodes = beats.map((b, i) => ({
    id: `b${i}`,
    title: b.title || `Beat ${i + 1}`,
    inflection: b.emotional_core || '',
    start: b.start || '',
    end: b.end || '',
    what_happened: b.what_happened || b.summary || '',
    summary: b.summary || '',
    scene_prompt: b.scene_prompt || '',
    location: b.location || '',
    characters: b.characters || [],
    next: i < beats.length - 1 ? [`b${i + 1}`] : [],
  }));
  return {
    logline: board?.logline || '',
    wound: '',
    lie: '',
    truth: '',
    nodes,
  };
}

// Saved Story → graph. Full-fidelity when it's a single un-branched-into-timelines arc;
// otherwise linearize from storyboard.beats (a clean re-branchable starting point).
export function storyToGraph(story) {
  const arcs = story?.arcs || [];
  const single = arcs.length === 1 ? arcs[0] : null;
  if (single && single.nodes && Object.keys(single.nodes).length && !(single.timelines?.length)) {
    const nodes = Object.entries(single.nodes).map(([id, n]) => ({
      id,
      title: n.title || '',
      inflection: n.emotional_core || '',
      start: n.start || '',
      end: n.end || '',
      what_happened: n.what_happened || n.summary || n.hook || '',
      summary: n.summary || '',
      scene_prompt: n.scene_prompt || '',
      location: n.location || '',
      characters: n.characters || [],
      next: (n.next || []).filter((t) => single.nodes[t]),
    }));
    return {
      logline: story?.storyboard?.logline || '',
      wound: '', lie: '', truth: '',
      nodes,
    };
  }
  return boardToGraph(story?.storyboard || {});
}

// ── graph → persisted ─────────────────────────────────────────────────────────

// Graph → flat storyboard (topo order) — keeps downstream scene/character extraction working.
export function graphToBoard(graph, prev = {}) {
  const ordered = topoOrder(graph?.nodes || []);
  const beats = ordered.map((n) => ({
    title: n.title || '',
    summary: n.summary || n.what_happened || '',
    emotional_core: n.inflection || '',
    hook: n.end || '',
    scene_prompt: n.scene_prompt || '',
    location: n.location || '',
    characters: n.characters || [],
    start: n.start || '',
    what_happened: n.what_happened || '',
    end: n.end || '',
  }));
  return {
    ...prev,
    logline: graph?.logline || prev.logline || '',
    heart: prev.heart || '',
    beats,
  };
}

// Graph → arcs[] (a single 'main' arc whose nodes form the DAG, branches preserved).
export function graphToArcs(graph) {
  const list = graph?.nodes || [];
  if (!list.length) return [];
  const nodes = {};
  for (const n of list) {
    nodes[n.id] = {
      id: n.id,
      title: n.title || '',
      summary: n.summary || n.what_happened || '',
      hook: n.summary || n.what_happened || '',   // overview ArcBeatNode renders `hook`
      emotional_core: n.inflection || '',
      location: n.location || '',
      characters: n.characters || [],
      scene_prompt: n.scene_prompt || '',
      next: (n.next || []).filter((t) => list.some((m) => m.id === t)),
      start: n.start || '',
      what_happened: n.what_happened || '',
      end: n.end || '',
    };
  }
  return [{
    id: 'main',
    name: (graph?.logline || 'Story').slice(0, 48),
    mini_ending: graph?.truth || '',
    dramatic_function: '',
    cast: [],
    nodes,
    start: firstNodeId(list),
    order: 0,
    timelines: [],
    transitions: [],
  }];
}

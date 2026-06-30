// Builders for the non-structure canvas layers, shaped for the SAME Svelte Flow renderer the
// structure graph uses (see story_graph.js / FlowGraph.svelte). Each returns { nodes, edges };
// the caller injects data._onClick. See [[overview-is-editor]].

// Stance → edge colour + width (categorical, mirrors RelationshipGraph).
const STANCE = {
  devoted:  { color: 'rgba(110,199,127,0.95)', width: 3.0 },
  warm:     { color: 'rgba(110,199,127,0.6)',  width: 2.2 },
  neutral:  { color: 'rgba(150,150,160,0.55)', width: 1.6 },
  strained: { color: 'rgba(224,122,122,0.6)',  width: 2.2 },
  hostile:  { color: 'rgba(224,122,122,0.95)', width: 3.0 },
};

// Cast on a ring (focus character pinned to the top), bonds as MUTUAL (undirected) edges.
export function buildRelationshipGraph(cast = [], relationships = [], focus = '') {
  if (!cast.length) return { nodes: [], edges: [] };
  const ordered = focus && cast.some((c) => c.key === focus)
    ? [cast.find((c) => c.key === focus), ...cast.filter((c) => c.key !== focus)]
    : cast;

  const n = ordered.length;
  const R = Math.max(300, n * 88);            // ring grows with the cast so cards + bonds have room
  const nodes = ordered.map((c, i) => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;   // i=0 (focus) at top
    return {
      id: c.key, type: 'character',
      position: { x: R * Math.cos(a), y: R * Math.sin(a) },
      width: 132, draggable: false, selectable: true,
      data: { key: c.key, name: c.name, img: c.img, role: c.role, primary: c.primary, focus: c.key === focus },
    };
  });

  const has = new Set(ordered.map((c) => c.key));
  const byName = Object.fromEntries(ordered.map((c) => [(c.name || '').toLowerCase(), c.key]));
  const resolve = (id) => (has.has(id) ? id : byName[(id || '').toLowerCase()] || null);

  const resolved = (relationships || []).map((r) => {
    const s = resolve(r.source), t = resolve(r.target);
    return s && t && s !== t ? { ...r, s, t } : null;
  }).filter(Boolean);

  // Bonds are MUTUAL: collapse reciprocal A→B / B→A into ONE undirected edge (prefer the one carrying
  // a picked potential), gentle bow, NO arrowhead — the line reads as a shared bond, not a direction.
  const byPair = new Map();
  for (const r of resolved) {
    const k = [r.s, r.t].sort().join('|');
    const cur = byPair.get(k);
    if (!cur || ((r.potential || r.trajectory) && !(cur.potential || cur.trajectory))) byPair.set(k, r);
  }

  const edges = [...byPair.values()].map((r, i) => {
    const sc = STANCE[r.stance] ? r.stance : 'neutral';
    return {
      id: `re${i}-${r.s}-${r.t}`, source: r.s, target: r.t, sourceHandle: 's', targetHandle: 't',
      type: 'rel', label: r.dynamic || r.nature || '', data: { bow: 0.35 },
      style: `stroke:${STANCE[sc].color};stroke-width:${STANCE[sc].width}`,
    };
  });

  return { nodes, edges };
}

// Locations as a light map: top-level areas across, their nested locations stacked below.
export function buildMapGraph(locations = [], start = null) {
  if (!locations.length) return { nodes: [], edges: [] };
  const bg = {};
  for (const l of locations) if (l.background) bg[l.id] = l.background;
  const roots = locations.filter((l) => !l.parent);
  const childrenOf = (id) => locations.filter((l) => l.parent === id);
  const COLW = 220, ROWH = 92;

  const nodes = [], edges = [];
  roots.forEach((r, ri) => {
    const kids = childrenOf(r.id);
    nodes.push({
      id: r.id, type: 'location', position: { x: ri * COLW, y: 0 }, width: 168, draggable: false, selectable: true,
      data: { id: r.id, name: r.name || r.id, isArea: kids.length > 0, start: start === r.id, bg: bg[r.id] || null },
    });
    kids.forEach((k, ki) => {
      nodes.push({
        id: k.id, type: 'location', position: { x: ri * COLW, y: (ki + 1) * ROWH }, width: 168, draggable: false, selectable: true,
        data: { id: k.id, name: k.name || k.id, isArea: false, start: start === k.id, bg: bg[k.id] || null },
      });
      edges.push({
        id: `me-${r.id}-${k.id}`, source: r.id, target: k.id, sourceHandle: 'b', targetHandle: 't',
        type: 'smoothstep', style: 'stroke:rgba(150,150,160,.5);stroke-width:1.5',
      });
    });
  });
  return { nodes, edges };
}

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

// ── The MC HUB (left→right flow) ──────────────────────────────────────────────
// A RAILROAD map of the FIXED relationships: the cast are stations grouped into PLACE lanes
// (one horizontal line per home location), bonds are the track between them. Edges are styled by
// the KIND of bond (family / friend / enemy / mentor / lover — the fixed structure set at story
// start), NOT by warmth: intimacy DRIFTS in play (the scribe's rel-deltas + the StoryMaster), so
// it isn't authored here. A bond with a hidden undercurrent (potential/trajectory) is dotted.
const NATURE_COLOR = { family: '#d9a441', friend: '#5ec27a', enemy: '#d0655a', mentor: '#6d8cff', lover: '#d98cc9', bond: '#9aa2b8' };
export function natureKind(nature) {
  const n = (nature || '').toLowerCase();
  if (/mother|father|sister|brother|son|daughter|aunt|uncle|cousin|famil|kin|parent|sibling|grand|niece|nephew/.test(n)) return 'family';
  if (/enemy|rival|foe|nemesis|advers|hostile/.test(n)) return 'enemy';
  if (/lover|love|beloved|betroth|spouse|wife|husband|sweetheart|paramour/.test(n)) return 'lover';
  if (/mentor|teacher|master|guardian|tutor|guide|protector/.test(n)) return 'mentor';
  if (/friend|ally|companion|comrade|confidant|neighbo/.test(n)) return 'friend';
  return 'bond';
}

export function buildRelationshipHub(cast = [], relationships = [], locations = [], mcKey = '') {
  if (!cast.length) return { nodes: [], edges: [], mcKey: '' };
  const mc = cast.find((c) => c.key === mcKey) || cast.find((c) => c.primary) || cast[0];

  // Group by HOME location → one container BOX per place (a Svelte Flow group node); characters
  // are its children (parentId), so they sit inside the box. Boxes stack vertically.
  const locIds = new Set((locations || []).map((l) => l.id));
  const homeOf = (c) => (c.home && locIds.has(c.home)) ? c.home : '__unplaced';
  const order = [];
  for (const l of (locations || [])) if (cast.some((c) => homeOf(c) === l.id)) order.push(l.id);
  if (cast.some((c) => homeOf(c) === '__unplaced')) order.push('__unplaced');
  const boxName = (id) => id === '__unplaced' ? 'Unplaced' : ((locations || []).find((l) => l.id === id)?.name || id);

  const HEADER = 34, PAD = 16, CARD_W = 132, STATION_W = 168, STATION_H = 156;
  const nodes = [];
  let y = 0;
  order.forEach((lid, li) => {
    const members = cast.filter((c) => homeOf(c) === lid);
    const boxW = PAD * 2 + Math.max(members.length, 1) * STATION_W;
    const boxH = HEADER + PAD + STATION_H + PAD;
    const boxId = `box-${lid}`;
    // Visual BOX behind (a plain group node); characters are TOP-LEVEL nodes positioned inside it
    // (absolute coords). Edges between top-level nodes resolve reliably — Svelte Flow v1 drops
    // edges whose endpoints are group CHILDREN (parentId), so we keep the box purely visual.
    nodes.push({ id: boxId, type: 'placebox', position: { x: 0, y }, width: boxW, height: boxH,
      selectable: false, draggable: false, data: { name: boxName(lid), icon: lid === '__unplaced' ? '❓' : '🚉', hue: (li * 57 + 20) % 360 } });
    members.forEach((c, ci) => {
      nodes.push({ id: c.key, type: 'character',
        position: { x: PAD + ci * STATION_W, y: y + HEADER + PAD }, width: CARD_W, draggable: true, selectable: true,
        data: { key: c.key, name: c.name, img: c.img, role: c.role, primary: c.primary, focus: c.key === mc.key } });
    });
    y += boxH + 28;
  });

  const has = new Set(cast.map((c) => c.key));
  const byName = Object.fromEntries(cast.map((c) => [(c.name || '').toLowerCase(), c.key]));
  const resolve = (id) => (has.has(id) ? id : byName[(id || '').toLowerCase()] || null);

  // One edge per PAIR (reciprocal rows collapse; prefer the row carrying depth). NATIVE Svelte
  // Flow edges: `smoothstep` = orthogonal rounded routing (the railroad look), coloured by the
  // bond KIND, dotted when a hidden undercurrent runs beneath.
  const byPair = new Map();
  for (const r of (relationships || [])) {
    const s = resolve(r.source), t = resolve(r.target);
    if (!s || !t || s === t) continue;
    const k = [s, t].sort().join('|');
    const cur = byPair.get(k);
    if (!cur || ((r.potential || r.trajectory) && !(cur.potential || cur.trajectory))) byPair.set(k, { ...r, s, t });
  }
  const edges = [...byPair.values()].map((r, i) => {
    const kind = natureKind(r.nature);
    const hidden = !!(r.potential || r.trajectory);
    const color = NATURE_COLOR[kind];
    // Bare relationship term for the label — strip a possessive prefix ("Eli's mother" → "mother").
    const label = (r.nature || kind).replace(/^[\w.-]+[’'`]s\s+/i, '').trim() || kind;
    return { id: `re${i}-${r.s}-${r.t}`, source: r.s, target: r.t, sourceHandle: 's', targetHandle: 't',
      type: 'smoothstep', label, data: { rel: r, kind, hidden },
      style: `stroke:${color};stroke-width:2.4;${hidden ? 'stroke-dasharray:2 6;' : ''}`,
      markerEnd: { type: 'arrowclosed', color } };
  });

  return { nodes, edges, mcKey: mc.key };
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

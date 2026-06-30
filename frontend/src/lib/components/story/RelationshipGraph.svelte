<script>
  // The cast's relationship web — characters as nodes on a ring, relationships as directional
  // edges coloured by warmth (red = hostile … green = devoted). Bond text lives in HOVER tooltips
  // and a legend below (NOT painted on the edges — long natures overlapped and were unreadable).
  // Click a node to inspect that character. Pure SVG, no physics: a ring reads clearly for small casts.
  // `pending` edges (no defined bond yet) draw faint + dashed; `selected` (the other endpoint of the
  // focus↔other spoke being edited) bolds that one spoke. `legend` off hides the bond list when the
  // cards beside the graph already show it (the genesis potentials view).
  let { cast = [], relationships = [], size = 460, onSelect = () => {}, pulse = null, focus = '', selected = '', legend = true, directed = true, youFocus = false } = $props();

  const cx = $derived(size / 2);
  const cy = $derived(size / 2);
  const R = $derived(size / 2 - 56);
  // Node LABELS sit outside the ring, so the viewBox needs horizontal/vertical PADDING beyond the ring
  // or the names clip at the edges. Labels are also capped (full name stays in the hover + legend).
  const PADX = 96, PADY = 28;
  const short = (s) => { s = String(s || ''); return s.length > 18 ? s.slice(0, 17) + '…' : s; };

  // The FOCUS character anchors the ring: rotated to the top (12 o'clock) so the through-line
  // stays put as you fly between the canvas's depth planes. Others keep their order after it.
  let ordered = $derived.by(() => {
    if (!focus) return cast;
    const f = cast.find((c) => c.key === focus);
    return f ? [f, ...cast.filter((c) => c.key !== focus)] : cast;
  });

  let nodes = $derived.by(() => {
    const n = ordered.length || 1;
    return ordered.map((c, i) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / n;
      const isFocus = c.key === focus;
      // Portraits read as faces only at avatar size — make faced nodes much larger than plain ones.
      const rad = c.face ? (isFocus ? 27 : 24) : (isFocus ? 14 : 12);
      return { ...c, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, focus: isFocus, rad };
    });
  });

  let byKey = $derived(Object.fromEntries(nodes.map((n) => [n.key, n])));
  let byName = $derived(Object.fromEntries(nodes.map((n) => [(n.name || '').toLowerCase(), n])));
  const resolve = (id) => byKey[id] || byName[(id || '').toLowerCase()] || null;

  // Stance is a CATEGORICAL label (not a -3..+3 scalar) — colour + a width hint for the graph only.
  const STANCE = {
    devoted:  { color: 'rgba(110,199,127,0.95)', width: 3.0 },
    warm:     { color: 'rgba(110,199,127,0.6)',  width: 2.2 },
    neutral:  { color: 'rgba(150,150,160,0.55)', width: 1.6 },
    strained: { color: 'rgba(224,122,122,0.6)',  width: 2.2 },
    hostile:  { color: 'rgba(224,122,122,0.95)', width: 3.0 },
  };
  const stanceOf = (r) => STANCE[r.stance] ? r.stance : 'neutral';

  // Undirected mode: a bond is MUTUAL — collapse reciprocal A→B / B→A into ONE edge (prefer the one
  // carrying a picked potential), draw it straight with no arrowhead. Directed mode keeps both + bows them.
  let edgeRels = $derived.by(() => {
    if (directed) return relationships || [];
    const byPair = new Map();
    for (const r of (relationships || [])) {
      const k = [r.source, r.target].sort().join('|');
      const cur = byPair.get(k);
      if (!cur || ((r.potential || r.trajectory) && !(cur.potential || cur.trajectory)) || (cur.pending && !r.pending)) byPair.set(k, r);
    }
    return [...byPair.values()];
  });

  let edges = $derived.by(() =>
    edgeRels.map((r) => {
      const s = resolve(r.source), t = resolve(r.target);
      if (!s || !t || s === t) return null;
      // Directed: bow reciprocal pairs to opposite sides so they don't overlap. Undirected: straight line.
      const dx = t.x - s.x, dy = t.y - s.y, len = Math.hypot(dx, dy) || 1;
      const px = -dy / len, py = dx / len, off = directed ? 30 : 0;
      const cxp = (s.x + t.x) / 2 + px * off, cyp = (s.y + t.y) / 2 + py * off;
      // A bond reads BOTH ways: source side = stance, target side = target_stance (falls back to source).
      // When they DIFFER, the edge is a two-tone gradient (source colour near s → target colour near t).
      const sc = stanceOf(r);
      const tc = (r.target_stance && STANCE[r.target_stance]) ? r.target_stance : sc;
      const label = (r.dynamic || r.nature || 'connected');
      const pending = !!r.pending;
      const join = directed ? '→' : '↔';
      const c1 = STANCE[sc].color, c2 = STANCE[tc].color;
      const twoTone = !pending && !directed && c1 !== c2;
      return {
        s, t, nature: r.nature || '', dynamic: r.dynamic || '', stance: sc, label, pending, twoTone, c1, c2,
        d: `M ${s.x} ${s.y} Q ${cxp} ${cyp} ${t.x} ${t.y}`,
        color: pending ? 'rgba(150,150,160,0.4)' : c1,
        width: pending ? 1.3 : Math.max(STANCE[sc].width, STANCE[tc].width),
        dash: pending ? '5 5' : 'none',
        title: pending ? `${s.name} ${join} ${t.name} — click to define`
          : `${s.name} ${join} ${t.name}: ${s.name} ${sc}${tc !== sc ? `, ${t.name} ${tc}` : ''} — ${label}`,
      };
    }).filter(Boolean)
  );

  // The one spoke currently being edited: focus (lead) ↔ selected (other).
  const selMatch = (e) => selected && focus &&
    ((e.s.key === focus && e.t.key === selected) || (e.s.key === selected && e.t.key === focus));

  // Node label placed outside the ring along its radial direction — offset scales with node radius so
  // big portrait avatars don't overlap their own labels.
  const lAnchor = (n) => (Math.cos(n.a) > 0.25 ? 'start' : Math.cos(n.a) < -0.25 ? 'end' : 'middle');
  const lDx = (n) => { const r = (n.rad || 11) + 3; return Math.cos(n.a) > 0.25 ? r : Math.cos(n.a) < -0.25 ? -r : 0; };
  const lDy = (n) => { const r = n.rad || 11; return Math.sin(n.a) > 0.5 ? r + 14 : Math.sin(n.a) < -0.5 ? -(r + 4) : 4; };

  // An edge pulses when a just-applied change targets it (resolve pulse.source/target → nodes).
  const pulseMatch = (e) => {
    if (!pulse) return false;
    const ps = resolve(pulse.source), pt = resolve(pulse.target);
    return ps && pt && e.s === ps && e.t === pt;
  };
</script>

{#if nodes.length}
  <div class="rg-stage">
  <svg class="relgraph" viewBox="{-PADX} {-PADY} {size + 2 * PADX} {size + 2 * PADY}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="rg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
              orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--faint, #7a7f97)" />
      </marker>
      <!-- Two-tone bond: source-side colour near s → target-side colour near t (an asymmetric read). -->
      {#each edges as e}
        {#if e.twoTone}
          <linearGradient id="rg-grad-{e.s.key}-{e.t.key}" gradientUnits="userSpaceOnUse" x1={e.s.x} y1={e.s.y} x2={e.t.x} y2={e.t.y}>
            <stop offset="0%" stop-color={e.c1} />
            <stop offset="100%" stop-color={e.c2} />
          </linearGradient>
        {/if}
      {/each}
    </defs>

    {#each edges as e}
      <path d={e.d} fill="none" stroke={e.twoTone ? `url(#rg-grad-${e.s.key}-${e.t.key})` : e.color}
            stroke-width={selMatch(e) ? e.width + 2 : e.width}
            stroke-dasharray={e.dash} class:pulse={pulseMatch(e)} class:sel={selMatch(e)}
            marker-end={(e.pending || !directed) ? '' : 'url(#rg-arrow)'}
            opacity={selMatch(e) ? 1 : (e.pending ? 0.55 : 0.9)}><title>{e.title}</title></path>
    {/each}

    {#each nodes as n}
      <g class="nodeg" role="button" tabindex="0" onclick={() => onSelect(n.key)}
         onkeydown={(ev) => (ev.key === 'Enter' || ev.key === ' ') && onSelect(n.key)}>
        <title>{n.name} — click to inspect</title>
        {#if n.groupColor}<circle cx={n.x} cy={n.y} r={n.rad + 5} class="grouphalo" fill={n.groupColor} />{/if}
        {#if n.focus}<circle cx={n.x} cy={n.y} r={n.rad + 4} class="focusring" />{/if}
        {#if n.facing}<circle cx={n.x} cy={n.y} r={n.rad + 4} class="genring" />{/if}
        {#if n.face}
          <!-- The portrait is an HTML <img> overlaid OUTSIDE the svg (see .rg-avatar below): SVG <image>
               won't paint via clip-path/pattern, and foreignObject mis-positions under this viewBox.
               Here we just leave a backing disc; the focus/group/gen rings above still frame it. -->
          <circle cx={n.x} cy={n.y} r={n.rad} class="faceback" />
        {:else}
          <circle cx={n.x} cy={n.y} r={n.rad} class="node" class:primary={n.primary} class:focus={n.focus} class:sel={n.key === selected} />
          <text x={n.x} y={n.y} class="ninitial" text-anchor="middle">{(n.name || '?').trim().charAt(0).toUpperCase()}</text>
        {/if}
        <text x={n.x + lDx(n)} y={n.y + lDy(n)} class="nlabel" class:focus={n.focus} text-anchor={lAnchor(n)}>{short(n.name)}</text>
        {#if n.focus && youFocus}<text x={n.x} y={n.y + n.rad + 13} class="youmark" text-anchor="middle">you · MC</text>{/if}
      </g>
    {/each}
  </svg>

  <!-- Portrait avatars as HTML <img> overlays, positioned by viewBox-derived % so they track the SVG
       node circles exactly (the only rendering path that paints reliably here). -->
  {#each nodes as n (n.key)}
    {#if n.face}
      <img class="rg-avatar" class:sel={n.key === selected} src={n.face} alt={n.name}
           style="left:{((n.x + PADX) / (size + 2 * PADX) * 100).toFixed(3)}%; top:{((n.y + PADY) / (size + 2 * PADY) * 100).toFixed(3)}%; width:{((n.rad * 2) / (size + 2 * PADX) * 100).toFixed(3)}%;" />
    {/if}
  {/each}
  </div>

  <!-- Legend: the bond text, readable, instead of overlapping the edges. Pending spokes are omitted. -->
  {#if legend && edges.some((e) => !e.pending)}
    <ul class="rg-legend">
      {#each edges.filter((e) => !e.pending) as e}
        <li>
          <span class="rg-dot" style={`background:${e.color}`}></span>
          <span class="rg-pair"><b>{e.s.name}</b> → <b>{e.t.name}</b>{#if e.nature} <span class="rg-kind">({e.nature})</span>{/if}</span>
          <span class="rg-nat">{e.dynamic || e.stance}</span>
        </li>
      {/each}
    </ul>
  {/if}
{:else}
  <div class="rg-empty">No cast yet.</div>
{/if}

<style>
  .rg-stage { position: relative; width: 100%; max-width: 560px; margin: 0 auto; }
  .relgraph { width: 100%; height: auto; display: block; overflow: visible; }
  /* HTML portrait overlay — centred on its node, scaled as a % of the stage (viewBox-aligned). The
     base accent ring is the img border; focus/group/gen rings are drawn in the SVG just outside it. */
  .rg-avatar { position: absolute; transform: translate(-50%, -50%); aspect-ratio: 1; border-radius: 50%;
    object-fit: cover; pointer-events: none; box-sizing: border-box;
    border: 2px solid var(--accent, #6d8cff); background: var(--elev-2, #2a2f44); }
  .rg-avatar.sel { border-width: 3px; filter: brightness(1.12); }
  .faceback { fill: var(--elev-2, #2a2f44); }
  .nodeg { cursor: pointer; }
  .node { fill: var(--elev-2, #2a2f44); stroke: var(--accent, #6d8cff); stroke-width: 2; transition: r .1s, filter .1s; }
  .node.primary { fill: var(--accent, #6d8cff); }
  .node.focus { stroke-width: 3; filter: drop-shadow(0 0 6px var(--accent, #6d8cff)); }
  .node.sel { stroke: var(--accent, #6d8cff); stroke-width: 3; filter: brightness(1.25); }
  .grouphalo { opacity: 0.18; }
  .genring { fill: none; stroke: var(--accent, #6d8cff); stroke-width: 2; stroke-dasharray: 3 4;
             animation: genpulse .9s ease-in-out infinite; }
  @keyframes genpulse { 0%,100% { opacity: .25; } 50% { opacity: .9; } }
  .relgraph path.sel { filter: drop-shadow(0 0 3px var(--accent, #6d8cff)); }
  .focusring { fill: none; stroke: var(--accent, #6d8cff); stroke-width: 2; opacity: .5;
               animation: focuspulse 2s ease-in-out infinite; }
  @keyframes focuspulse { 0%,100% { opacity: .55; } 50% { opacity: .15; } }
  .nodeg:hover .node, .nodeg:focus .node { filter: brightness(1.3); }
  .nodeg:focus { outline: none; }
  .ninitial { font-size: 11px; fill: var(--text, #e6e8f0); font-weight: 700; pointer-events: none;
              dominant-baseline: central; opacity: .85; }
  .nlabel { font-size: 12px; fill: var(--text, #e6e8f0); font-weight: 600; pointer-events: none; }
  .nlabel.focus { fill: var(--accent, #6d8cff); font-weight: 800; }
  .youmark { font-size: 9px; fill: var(--accent, #6d8cff); font-weight: 700; letter-spacing: .5px;
             text-transform: uppercase; pointer-events: none; opacity: .85; }

  .rg-legend { list-style: none; margin: 10px auto 0; padding: 0; max-width: 480px;
               display: flex; flex-direction: column; gap: 4px; }
  .rg-legend li { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
  .rg-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
  .rg-pair { color: var(--text); white-space: nowrap; }
  .rg-pair b { font-weight: 600; }
  .rg-kind { color: var(--faint); font-weight: 400; }
  .rg-nat { color: var(--muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rg-empty { color: var(--faint); font-size: 13px; padding: 8px 2px; }
  .pulse { animation: rgpulse 1.2s ease-out 2; }
  @keyframes rgpulse { 0% { stroke-width: 9; opacity: 1; } 100% { opacity: .9; } }
</style>

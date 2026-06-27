<script>
  // The cast's relationship web — characters as nodes on a ring, relationships as directional
  // edges coloured by warmth (red = hostile … green = devoted). Bond text lives in HOVER tooltips
  // and a legend below (NOT painted on the edges — long natures overlapped and were unreadable).
  // Click a node to inspect that character. Pure SVG, no physics: a ring reads clearly for small casts.
  let { cast = [], relationships = [], size = 460, onSelect = () => {}, pulse = null, focus = '' } = $props();

  const cx = $derived(size / 2);
  const cy = $derived(size / 2);
  const R = $derived(size / 2 - 56);

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
      return { ...c, x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, focus: c.key === focus };
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

  let edges = $derived.by(() =>
    (relationships || []).map((r) => {
      const s = resolve(r.source), t = resolve(r.target);
      if (!s || !t || s === t) return null;
      // Bow reciprocal pairs (A→B, B→A) to opposite sides so they don't overlap.
      const dx = t.x - s.x, dy = t.y - s.y, len = Math.hypot(dx, dy) || 1;
      const px = -dy / len, py = dx / len, off = 30;
      const cxp = (s.x + t.x) / 2 + px * off, cyp = (s.y + t.y) / 2 + py * off;
      const sc = stanceOf(r);
      const label = (r.dynamic || r.nature || 'connected');
      return {
        s, t, nature: r.nature || '', dynamic: r.dynamic || '', stance: sc, label,
        d: `M ${s.x} ${s.y} Q ${cxp} ${cyp} ${t.x} ${t.y}`,
        color: STANCE[sc].color, width: STANCE[sc].width,
        title: `${s.name} → ${t.name} (${sc}): ${label}`,
      };
    }).filter(Boolean)
  );

  // Node label placed outside the ring along its radial direction.
  const lAnchor = (n) => (Math.cos(n.a) > 0.25 ? 'start' : Math.cos(n.a) < -0.25 ? 'end' : 'middle');
  const lDx = (n) => (Math.cos(n.a) > 0.25 ? 14 : Math.cos(n.a) < -0.25 ? -14 : 0);
  const lDy = (n) => (Math.sin(n.a) > 0.5 ? 22 : Math.sin(n.a) < -0.5 ? -14 : 4);

  // An edge pulses when a just-applied change targets it (resolve pulse.source/target → nodes).
  const pulseMatch = (e) => {
    if (!pulse) return false;
    const ps = resolve(pulse.source), pt = resolve(pulse.target);
    return ps && pt && e.s === ps && e.t === pt;
  };
</script>

{#if nodes.length}
  <svg class="relgraph" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="rg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7"
              orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--faint, #7a7f97)" />
      </marker>
    </defs>

    {#each edges as e}
      <path d={e.d} fill="none" stroke={e.color} stroke-width={e.width} class:pulse={pulseMatch(e)}
            marker-end="url(#rg-arrow)" opacity="0.9"><title>{e.title}</title></path>
    {/each}

    {#each nodes as n}
      <g class="nodeg" role="button" tabindex="0" onclick={() => onSelect(n.key)}
         onkeydown={(ev) => (ev.key === 'Enter' || ev.key === ' ') && onSelect(n.key)}>
        <title>{n.name} — click to inspect</title>
        {#if n.focus}<circle cx={n.x} cy={n.y} r="17" class="focusring" />{/if}
        <circle cx={n.x} cy={n.y} r={n.focus ? 13 : 11} class="node" class:primary={n.primary} class:focus={n.focus} />
        <text x={n.x + lDx(n)} y={n.y + lDy(n)} class="nlabel" class:focus={n.focus} text-anchor={lAnchor(n)}>{n.name}</text>
      </g>
    {/each}
  </svg>

  <!-- Legend: the bond text, readable, instead of overlapping the edges. -->
  {#if edges.length}
    <ul class="rg-legend">
      {#each edges as e}
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
  .relgraph { width: 100%; max-width: 480px; height: auto; display: block; margin: 0 auto; }
  .nodeg { cursor: pointer; }
  .node { fill: var(--elev-2, #2a2f44); stroke: var(--accent, #6d8cff); stroke-width: 2; transition: r .1s, filter .1s; }
  .node.primary { fill: var(--accent, #6d8cff); }
  .node.focus { stroke-width: 3; filter: drop-shadow(0 0 6px var(--accent, #6d8cff)); }
  .focusring { fill: none; stroke: var(--accent, #6d8cff); stroke-width: 1.5; opacity: .5;
               animation: focuspulse 2s ease-in-out infinite; }
  @keyframes focuspulse { 0%,100% { r: 16; opacity: .45; } 50% { r: 19; opacity: .15; } }
  .nodeg:hover .node, .nodeg:focus .node { filter: brightness(1.3); }
  .nodeg:focus { outline: none; }
  .nlabel { font-size: 12px; fill: var(--text, #e6e8f0); font-weight: 600; pointer-events: none; }
  .nlabel.focus { fill: var(--accent, #6d8cff); font-weight: 800; }

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

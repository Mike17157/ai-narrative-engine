<script>
  // The World MAP — a READ-ONLY spatial view of the locations (rendered from the story JSON). You can
  // DRAG nodes to arrange the map (that's layout metadata in Story.world.map, not narrative content);
  // all content editing (names, descriptions, adding/removing places) happens in the section editor.
  import { stories } from '$lib/stories.svelte.js';

  let st = $derived(stories.current);
  let locs = $derived(st?.locations || []);

  const W = 168, H = 58, COLGAP = 210, ROWGAP = 120;

  // Auto-layout for locations without a saved position: top-level across a row, children clustered.
  function autoLayout(list) {
    const tops = list.filter((l) => !l.parent);
    const out = {};
    tops.forEach((t, i) => {
      out[t.id] = { x: 40 + i * (COLGAP + 60), y: 40 };
      const kids = list.filter((l) => l.parent === t.id);
      kids.forEach((k, j) => { out[k.id] = { x: 40 + i * (COLGAP + 60) + (j - (kids.length - 1) / 2) * COLGAP, y: 40 + ROWGAP }; });
    });
    list.forEach((l, i) => { if (!out[l.id]) out[l.id] = { x: 40 + (i % 4) * (COLGAP + 60), y: 40 + (2 + Math.floor(i / 4)) * ROWGAP }; });
    return out;
  }

  let positions = $state({});
  let seeded = '';
  $effect(() => {
    if (!st || seeded === st.key) return;
    seeded = st.key;
    positions = { ...autoLayout(locs), ...((st.world || {}).map || {}) };
  });
  const at = (id) => positions[id] || { x: 40, y: 40 };

  let saveT = null;
  function savePositions() {   // layout only — write the {id:{x,y}} map back to Story.world.map
    stories.current.world = { ...(stories.current.world || {}), map: positions };
    clearTimeout(saveT);
    saveT = setTimeout(() => import('$lib/api.js').then(({ put }) => put(`/stories/${st.key}`, { world: stories.current.world })), 500);
  }

  // ── drag (arrange the map) ──
  let drag = null;
  let selected = $state(null);
  function down(e, id) {
    if (e.button != null && e.button !== 0) return;
    selected = id;
    const p = at(id);
    drag = { id, mx: e.clientX, my: e.clientY, ox: p.x, oy: p.y };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  function move(e) {
    if (!drag) return;
    positions = { ...positions, [drag.id]: { x: Math.max(0, drag.ox + (e.clientX - drag.mx)), y: Math.max(0, drag.oy + (e.clientY - drag.my)) } };
  }
  function up() { drag = null; window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); savePositions(); }

  function sceneCount(loc) {
    const arcs = st?.arcs || []; let n = 0;
    for (const a of arcs) for (const k of Object.keys(a.nodes || {})) { const nd = a.nodes[k]; if (nd?.location === loc.id || nd?.location === loc.name) n++; }
    return n;
  }
  const childCount = (loc) => locs.filter((l) => l.parent === loc.id).length;

  // Direct type-editing of the selected node's name/description (no add/remove/set-start buttons —
  // those structural ops go to the section editor).
  let selLoc = $derived(locs.find((l) => l.id === selected) || null);
  let editT = null;
  function saveLocs() { clearTimeout(editT); editT = setTimeout(() => import('$lib/api.js').then(({ put }) => put(`/stories/${st.key}`, { locations: stories.current.locations })), 500); }
  function setLoc(field, v) { stories.current.locations = locs.map((l) => l.id === selected ? { ...l, [field]: v } : l); saveLocs(); }

  let canvasSize = $derived.by(() => {
    let mx = 600, my = 400;
    for (const l of locs) { const p = at(l.id); mx = Math.max(mx, p.x + W + 60); my = Math.max(my, p.y + H + 80); }
    return { w: mx, h: my };
  });
</script>

<div class="mapwrap">
  <div class="bar">
    <span class="t">World map <span class="lo">· drag to arrange · lines are containment · edit in the editor</span></span>
  </div>

  <div class="canvas-scroll">
    <div class="canvas" style:width={`${canvasSize.w}px`} style:height={`${canvasSize.h}px`}
         onpointerdown={(e) => { if (e.target.classList.contains('canvas')) selected = null; }}>
      <svg class="edges" width={canvasSize.w} height={canvasSize.h}>
        {#each locs.filter((l) => l.parent && positions[l.parent]) as l (l.id)}
          {@const p = at(l.parent)}{@const c = at(l.id)}
          <line x1={p.x + W / 2} y1={p.y + H} x2={c.x + W / 2} y2={c.y} stroke="var(--border)" stroke-width="1.5" />
        {/each}
      </svg>

      {#each locs as l (l.id)}
        {@const p = at(l.id)}{@const sc = sceneCount(l)}
        <div class="node" class:sel={selected === l.id} class:start={st.start === l.id}
             style:left={`${p.x}px`} style:top={`${p.y}px`} style:width={`${W}px`}
             onpointerdown={(e) => down(e, l.id)} role="button" tabindex="0">
          <div class="nname">{#if st.start === l.id}<span class="startdot" title="Start">◆</span>{/if}{l.name || 'Location'}</div>
          <div class="nmeta">
            {#if childCount(l)}<span class="chip">{childCount(l)} within</span>{/if}
            {#if sc}<span class="chip sc">{sc} scene{sc === 1 ? '' : 's'}</span>{/if}
            {#if !childCount(l) && !sc}<span class="chip dim">empty</span>{/if}
          </div>
        </div>
      {:else}
        <p class="none">No locations yet — ask the editor to add some.</p>
      {/each}
    </div>
  </div>

  {#if selLoc}
    <div class="editbar">
      <input class="ename" value={selLoc.name || ''} oninput={(e) => setLoc('name', e.target.value)} placeholder="location name" />
      <input class="edesc" value={selLoc.description || ''} oninput={(e) => setLoc('description', e.target.value)} placeholder="what this place is…" />
    </div>
  {/if}
</div>

<style>
  .editbar { display: flex; align-items: center; gap: 8px; padding: 9px 12px; border-top: 1px solid var(--border-soft); background: var(--elev); }
  .ename { flex: 0 0 200px; font-size: 12.5px; font-weight: 600; }
  .edesc { flex: 1; font-size: 12.5px; color: var(--muted); }
  .editbar input { background: var(--panel); border: 1px solid var(--border-soft); color: var(--text); border-radius: 7px; padding: 6px 9px; font: inherit; }
  .editbar input:focus { outline: none; border-color: var(--accent); }
  .mapwrap { display: flex; flex-direction: column; gap: 0; margin-bottom: 18px;
             border: 1px solid var(--border-soft); border-radius: 12px; overflow: hidden; background: var(--panel); }
  .bar { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px;
         border-bottom: 1px solid var(--border-soft); }
  .t { font-size: 12px; font-weight: 800; color: var(--text); }
  .t .lo { font-weight: 500; color: var(--faint); font-size: 11px; }

  .canvas-scroll { overflow: auto; max-height: 560px; background:
    radial-gradient(circle, var(--border-soft) 1px, transparent 1px) 0 0 / 26px 26px; }
  .canvas { position: relative; min-width: 100%; }
  .edges { position: absolute; inset: 0; pointer-events: none; }
  .none { padding: 24px; color: var(--faint); font-size: 13px; }

  .node { position: absolute; box-sizing: border-box; padding: 8px 10px; border-radius: 10px;
          background: var(--elev-2, #222838); border: 1px solid var(--border); cursor: grab;
          box-shadow: 0 2px 8px rgba(0,0,0,.25); user-select: none; touch-action: none; }
  .node:hover { border-color: var(--muted); }
  .node.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), 0 2px 10px rgba(0,0,0,.3); }
  .node.start { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); }
  .node:active { cursor: grabbing; }
  .nname { font-size: 12.5px; font-weight: 700; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .startdot { color: var(--accent); font-size: 9px; margin-right: 4px; }
  .nmeta { display: flex; gap: 4px; margin-top: 5px; }
  .chip { font-size: 9.5px; color: var(--muted); background: var(--panel); border: 1px solid var(--border-soft); padding: 1px 7px; border-radius: 999px; }
  .chip.sc { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 30%, transparent); }
  .chip.dim { color: var(--faint); }
</style>

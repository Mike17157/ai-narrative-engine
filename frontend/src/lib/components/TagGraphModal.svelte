<script>
  import { tick, onDestroy } from 'svelte';
  import { get } from '$lib/api.js';
  import { pickerState, resolveGraphPicker } from '$lib/graphpicker.svelte.js';

  // Graph-driven prompt editor. Shows the current prompt as chips; click a chip to TARGET it for
  // replacement — the live similarity graph for that tag then appears in-place, and clicking a node
  // swaps it in. With no target, a faceted suggestion list lets you add tags. Search adds/swaps from
  // the full vocabulary. Apply returns the edited tag array via the store.
  const norm = (s) => (s || '').toString().trim().toLowerCase().replace(/\s+/g, ' ');
  const dedupe = (a) => { const seen = new Set(), out = []; for (const t of a) { const k = norm(t); if (t && !seen.has(k)) { seen.add(k); out.push(t); } } return out; };

  const COLORS = {
    hair:'#e0b04a', eyes:'#4ab0e0', skin:'#e08a6a', body:'#e06aa0', ears_tail:'#b06ae0', face:'#6ae0b0',
    swimwear:'#4ad6e0', piercing:'#e04a6a', makeup:'#e04ab0', dress:'#9a6ae0', top:'#5a8cf0',
    bottom:'#46c08a', outerwear:'#e0883a', legwear:'#c0c04a', footwear:'#a07a5a', headwear:'#e0c04a',
    sleeves:'#7ac0e0', accessories:'#8fa0b5', other:'#5a626f'
  };

  let tags = $state([]);
  let kind = $state('clothing');
  let target = $state(null);      // the chip selected for replacement (or null = add mode)
  let suggestions = $state({});   // { facet: [tag, ...] } — the no-target add list
  let busy = $state(false);
  let seeded = false;

  // query / autocomplete
  let query = $state('');
  let results = $state([]);
  let open = $state(false);
  let active = $state(-1);

  // ---- live graph state (shown when a chip is targeted) ----
  let canvasEl = $state(null);
  let gnodes = [], gedges = [], gadj = new Map(), ghover = null, gdrag = null, gdragged = false;
  let galpha = 1, graf = null, gctx = null, gW = 0, gH = 0, gDPR = 1;
  let gloading = $state(false);
  let gempty = $state(false);
  let glegend = $state([]);        // facets present in the current graph (for the legend)
  let hoverInfo = $state(null);    // { id, facet, post_count } of the hovered node
  const fmtCount = (n) => (n >= 1000 ? Math.round(n / 1000) + 'k' : '' + (n || 0));

  // camera: the graph lays out to canvas-fit in world coords, then renders at ZOOM (2x linear =
  // 4x the fit area); drag the background to pan around it.
  const ZOOM = 2;
  let panX = 0, panY = 0, panning = false, panSX = 0, panSY = 0, panPX = 0, panPY = 0;
  function centerPan() { panX = (gW / 2) * (1 - ZOOM); panY = (gH / 2) * (1 - ZOOM); }
  function clampPan() { panX = Math.min(0, Math.max(gW - gW * ZOOM, panX)); panY = Math.min(0, Math.max(gH - gH * ZOOM, panY)); }

  // seed local state on the rising edge of `open`
  $effect(() => {
    if (pickerState.open && !seeded) {
      seeded = true;
      tags = pickerState.tags.slice();
      kind = pickerState.kind;
      target = pickerState.target || null; query = ''; results = []; open = false; suggestions = {};
      if (target) loadGraph(target); else refreshSuggestions();
    }
    if (!pickerState.open) { seeded = false; stopGraph(); }
  });

  let sugTimer = null;
  function scheduleSuggestions() { clearTimeout(sugTimer); sugTimer = setTimeout(refreshSuggestions, 180); }
  async function refreshSuggestions() {
    const seed = tags.join(',');
    if (!seed) { suggestions = {}; return; }
    busy = true;
    try { const d = await get('/tags/related?kind=' + kind + '&tags=' + encodeURIComponent(seed)); suggestions = d?.palette || {}; }
    catch { suggestions = {}; }
    busy = false;
  }

  function applyTag(t) {
    t = (t || '').trim(); if (!t) return;
    if (target) { tags = dedupe(tags.map((x) => (norm(x) === norm(target) ? t : x))); setTarget(null); }
    else { tags = dedupe([...tags, t]); }
    query = ''; results = []; open = false; active = -1;
    scheduleSuggestions();
  }
  function removeChip(t) { tags = tags.filter((x) => norm(x) !== norm(t)); if (target && norm(target) === norm(t)) setTarget(null); scheduleSuggestions(); }
  function setTarget(t) {
    const next = (t && target && norm(target) === norm(t)) ? null : t;   // toggle off if same
    target = next;
    if (next) loadGraph(next); else stopGraph();
  }

  // ---- the live graph ----
  async function loadGraph(seed) {
    stopGraph(); gloading = true; gempty = false;
    try {
      const d = await get('/tags/graph?kind=' + kind + '&tags=' + encodeURIComponent(seed));
      buildGraph(d);
    } catch { gnodes = []; gempty = true; }
    gloading = false;
  }
  async function buildGraph(d) {
    await tick();                       // ensure the canvas is in the DOM
    if (!canvasEl) return;
    sizeCanvas();
    const ns = d?.nodes || [];
    if (!ns.length) { gnodes = []; gedges = []; gempty = true; return; }
    gempty = false;
    const maxpc = Math.max(1, ...ns.map(n => n.post_count || 0));
    gnodes = ns.map((n, i) => { const ang = i * 2.399, rad = 30 + i * 4;
      return { ...n, r: 4 + 8 * Math.sqrt((n.post_count || 1) / maxpc) + (n.seed ? 5 : 0),
        x: gW / 2 + Math.cos(ang) * rad, y: gH / 2 + Math.sin(ang) * rad, vx: 0, vy: 0,
        col: COLORS[n.facet] || COLORS.other }; });
    const byId = new Map(gnodes.map(n => [n.id, n]));
    gedges = (d.edges || []).map(e => ({ a: byId.get(e.source), b: byId.get(e.target), w: e.weight })).filter(e => e.a && e.b);
    gadj = new Map(gnodes.map(n => [n.id, new Set()]));
    gedges.forEach(e => { gadj.get(e.a.id).add(e.b.id); gadj.get(e.b.id).add(e.a.id); });
    glegend = [...new Set(gnodes.map(n => n.facet))];
    centerPan();
    galpha = 1;
    if (!graf) graf = requestAnimationFrame(loop);
  }
  function stopGraph() { if (graf) cancelAnimationFrame(graf); graf = null; gnodes = []; gedges = []; ghover = null; gdrag = null; }
  function sizeCanvas() { if (!canvasEl) return; gDPR = Math.max(1, window.devicePixelRatio || 1);
    const r = canvasEl.parentElement.getBoundingClientRect(); gW = r.width; gH = r.height;
    canvasEl.width = gW * gDPR; canvasEl.height = gH * gDPR; gctx = canvasEl.getContext('2d'); gctx.setTransform(gDPR, 0, 0, gDPR, 0, 0); }
  function step() {
    const REP = 1400, SPRING = 0.02, GRAV = 0.025, DAMP = 0.9, LEN = 64, VMAX = 16;
    for (let i = 0; i < gnodes.length; i++) { const a = gnodes[i];
      for (let j = i + 1; j < gnodes.length; j++) { const b = gnodes[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
        let f = REP / d2, fx = f * dx / d, fy = f * dy / d; a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy; } }
    gedges.forEach(e => { let dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      let f = SPRING * (d - LEN) * (0.4 + e.w * 3), fx = f * dx / d, fy = f * dy / d;
      e.a.vx += fx; e.a.vy += fy; e.b.vx -= fx; e.b.vy -= fy; });
    gnodes.forEach(n => { if (n === gdrag) return;
      n.vx += (gW / 2 - n.x) * GRAV; n.vy += (gH / 2 - n.y) * GRAV; n.vx *= DAMP; n.vy *= DAMP;
      const sp = Math.hypot(n.vx, n.vy); if (sp > VMAX) { n.vx *= VMAX / sp; n.vy *= VMAX / sp; }
      n.x += n.vx * galpha; n.y += n.vy * galpha;
      n.x = Math.max(n.r, Math.min(gW - n.r, n.x)); n.y = Math.max(n.r, Math.min(gH - n.r, n.y)); });
    galpha *= 0.985;
  }
  function draw() {
    if (!gctx) return;
    gctx.clearRect(0, 0, gW, gH);
    gctx.save();
    gctx.translate(panX, panY);
    gctx.scale(ZOOM, ZOOM);
    const hi = ghover ? gadj.get(ghover.id) : null;
    gedges.forEach(e => { const on = ghover && (e.a === ghover || e.b === ghover);
      gctx.strokeStyle = on ? 'rgba(150,180,255,.55)' : 'rgba(120,130,150,' + (0.05 + e.w * 0.5) + ')';
      gctx.lineWidth = on ? 1.6 : Math.max(0.4, e.w * 4);
      gctx.beginPath(); gctx.moveTo(e.a.x, e.a.y); gctx.lineTo(e.b.x, e.b.y); gctx.stroke(); });
    gnodes.forEach(n => { const dim = ghover && n !== ghover && !(hi && hi.has(n.id));
      gctx.globalAlpha = dim ? 0.3 : 1;
      gctx.beginPath(); gctx.arc(n.x, n.y, n.r, 0, 7); gctx.fillStyle = n.col; gctx.fill();
      if (n.seed) { gctx.lineWidth = 2.5; gctx.strokeStyle = '#fff'; gctx.stroke(); }
      // label EVERY node; emphasise seed / hovered / neighbours, fade the rest a touch
      const emph = n.seed || n === ghover || (hi && hi.has(n.id));
      gctx.globalAlpha = dim ? 0.4 : (emph ? 1 : 0.82);
      gctx.fillStyle = emph ? '#ffffff' : '#cfd4dc';
      gctx.font = (n.seed ? '600 12px' : (emph ? '11px' : '10px')) + ' system-ui,sans-serif';
      gctx.fillText(n.id, n.x + n.r + 3, n.y + 3);
      gctx.globalAlpha = 1; });
    gctx.restore();
  }
  function loop() { if (gnodes.length) { if (galpha > 0.03 || gdrag) step(); draw(); } graf = requestAnimationFrame(loop); }
  // screen (canvas px) → node, accounting for pan + zoom
  function gat(mx, my) { const wx = (mx - panX) / ZOOM, wy = (my - panY) / ZOOM;
    let best = null, bd = 1e9; gnodes.forEach(n => { let d = (n.x - wx) ** 2 + (n.y - wy) ** 2; if (d < bd && d < (n.r + 5) ** 2) { bd = d; best = n; } }); return best; }
  function gmove(e) { const r = canvasEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (gdrag) { gdrag.x = (mx - panX) / ZOOM; gdrag.y = (my - panY) / ZOOM; gdrag.vx = gdrag.vy = 0; gdragged = true; galpha = Math.max(galpha, 0.4); }
    else if (panning) { panX = panPX + (mx - panSX); panY = panPY + (my - panSY); clampPan(); gdragged = true; }
    else { ghover = gat(mx, my); canvasEl.style.cursor = ghover ? 'pointer' : 'grab';
      hoverInfo = ghover ? { id: ghover.id, facet: ghover.facet, post_count: ghover.post_count } : null; } }
  function gleave() { ghover = null; hoverInfo = null; }
  function gdown(e) { const r = canvasEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const n = gat(mx, my); gdragged = false;
    if (n) { gdrag = n; }                                  // drag a node
    else { panning = true; panSX = mx; panSY = my; panPX = panX; panPY = panY; canvasEl.style.cursor = 'grabbing'; } }  // pan the view
  function gup() { gdrag = null; panning = false; if (canvasEl) canvasEl.style.cursor = 'grab'; }
  function gclick(e) { if (gdragged) { gdragged = false; return; }   // ignore the click that ends a drag/pan
    const r = canvasEl.getBoundingClientRect(); const n = gat(e.clientX - r.left, e.clientY - r.top);
    if (n && !n.seed) applyTag(n.id);   // swap the targeted chip for the clicked node
  }
  onDestroy(stopGraph);

  // search the full vocabulary
  $effect(() => {
    const q = query;
    if (!q.trim()) { results = []; open = false; return; }
    const id = setTimeout(async () => {
      const d = await get('/tags/search?limit=12&q=' + encodeURIComponent(q));
      results = d?.tags || []; active = results.length ? 0 : -1; open = results.length > 0;
    }, 140);
    return () => clearTimeout(id);
  });
  function onkey(e) {
    if (e.key === 'Enter') { e.preventDefault(); if (open && active >= 0 && results[active]) applyTag(results[active].tag); else if (query.trim()) applyTag(query); }
    else if (e.key === 'ArrowDown' && open) { e.preventDefault(); active = Math.min(active + 1, results.length - 1); }
    else if (e.key === 'ArrowUp' && open) { e.preventDefault(); active = Math.max(active - 1, 0); }
    else if (e.key === 'Escape') { if (open) open = false; else cancel(); }
  }

  function setKind(k) { if (k !== kind) { kind = k; if (target) loadGraph(target); else refreshSuggestions(); } }
  const apply = () => resolveGraphPicker(dedupe(tags));
  const cancel = () => resolveGraphPicker(null);
  let facetOrder = $derived(Object.keys(suggestions));
</script>

{#if pickerState.open}
  <div class="overlay" onclick={cancel} role="presentation">
    <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="head">
        <h3 class="title">{pickerState.title}</h3>
        <div class="kinds">
          <button class:on={kind === 'clothing'} onclick={() => setKind('clothing')}>clothing</button>
          <button class:on={kind === 'appearance'} onclick={() => setKind('appearance')}>appearance</button>
        </div>
      </div>

      <div class="chips">
        {#each tags as t (t)}
          <span class="chip" class:target={target && norm(target) === norm(t)}>
            <button class="lbl" onclick={() => setTarget(t)} title="select to replace (shows its graph)">{t}</button>
            <button class="x" onclick={() => removeChip(t)} title="remove">×</button>
          </span>
        {/each}
        {#if !tags.length}<span class="empty">no tags yet — search or pick below</span>{/if}
      </div>

      <div class="status">
        {#if target}Replacing <b>{target}</b> — click a node to swap it · drag the background to pan. <button class="link" onclick={() => setTarget(null)}>back to list</button>
        {:else}Click a chip to replace it (opens its graph), or add tags below.{/if}
      </div>

      <div class="search">
        <input placeholder="search any tag…" bind:value={query} onkeydown={onkey} />
        {#if open}
          <div class="pop">
            {#each results as r, i (r.name)}
              <div class="opt" class:on={i === active} onmousedown={(e) => { e.preventDefault(); applyTag(r.tag); }}>
                <span>{r.tag}</span><span class="cnt">{r.count >= 1000 ? Math.round(r.count / 1000) + 'k' : r.count}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <div class="body">
        {#if target}
          <!-- live similarity graph for the targeted tag -->
          <div class="graphwrap">
            <canvas bind:this={canvasEl} onmousemove={gmove} onmouseleave={gleave}
              onmousedown={gdown} onmouseup={gup} onclick={gclick}></canvas>
            {#if glegend.length}
              <div class="glegend">
                {#each glegend as f (f)}
                  <span class="lrow"><span class="lsw" style="background:{COLORS[f] || COLORS.other}"></span>{f}</span>
                {/each}
              </div>
            {/if}
            {#if hoverInfo}
              <div class="ghover">{hoverInfo.id}{#if hoverInfo.facet} · {hoverInfo.facet}{/if} · {fmtCount(hoverInfo.post_count)} posts</div>
            {/if}
            {#if gloading}<div class="gmsg">loading graph…</div>
            {:else if gempty}<div class="gmsg">“{target}” isn’t in the graph — use search to swap it.</div>{/if}
          </div>
        {:else}
          <!-- faceted add list -->
          <div class="sug">
            {#if busy && !facetOrder.length}<p class="lo">finding compatible tags…</p>
            {:else if !facetOrder.length}<p class="lo">no graph suggestions for these tags — use search above.</p>
            {:else}
              {#each facetOrder as f (f)}
                <div class="facet">
                  <div class="fname">{f}</div>
                  <div class="pills">
                    {#each suggestions[f] as t (t)}<button class="pill" onclick={() => applyTag(t)} title="add">{t}</button>{/each}
                  </div>
                </div>
              {/each}
            {/if}
          </div>
        {/if}
      </div>

      <div class="acts">
        <span class="lo">{tags.length} tag{tags.length === 1 ? '' : 's'}</span>
        <button class="ghost" onclick={cancel}>Cancel</button>
        <button onclick={apply}>Apply</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onmouseup={gup} onkeydown={(e) => { if (pickerState.open && e.key === 'Escape' && !open) cancel(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 90; background: rgba(6,8,12,.62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(95vw, 880px); height: min(92vh, 880px); display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg, 14px);
    box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55)); padding: 16px 18px; animation: pop .13s ease; }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .title { margin: 0; font-size: 16px; font-weight: 680; color: var(--text); flex: 1; }
  .kinds { display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .kinds button { background: var(--elev); border: 0; color: var(--muted); padding: 5px 12px; font-size: 12px; box-shadow: none; }
  .kinds button.on { background: var(--accent); color: #fff; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 9px; border: 1px solid var(--border);
    border-radius: 10px; background: var(--bg); min-height: 44px; max-height: 18vh; overflow: auto; }
  .chip { display: inline-flex; align-items: center; border: 1px solid var(--border-soft); background: var(--elev-2); border-radius: 8px; overflow: hidden; }
  .chip.target { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .chip .lbl { background: none; border: 0; color: var(--text); font-size: 12.5px; padding: 3px 4px 3px 9px; box-shadow: none; cursor: pointer; }
  .chip .x { background: none; border: 0; color: var(--muted); font-size: 15px; padding: 0 7px 0 3px; box-shadow: none; cursor: pointer; }
  .chip .x:hover { color: #ff8a8a; }
  .empty { color: var(--faint); font-size: 12.5px; align-self: center; padding: 2px 4px; }
  .status { font-size: 12px; color: var(--muted); margin: 8px 2px; }
  .status b { color: var(--text); }
  .link { background: none; border: 0; color: var(--accent); box-shadow: none; cursor: pointer; font-size: 12px; padding: 0 2px; }
  .search { position: relative; }
  .search input { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 9px; padding: 8px 11px; color: var(--text); font-size: 13px; }
  .search input:focus { border-color: var(--accent); outline: none; }
  .pop { position: absolute; z-index: 5; left: 0; right: 0; top: 100%; margin-top: 5px; background: var(--elev);
    border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); max-height: 240px; overflow: auto; padding: 4px; }
  .opt { display: flex; justify-content: space-between; gap: 8px; padding: 6px 9px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  .opt.on, .opt:hover { background: var(--elev-2); }
  .opt .cnt { color: var(--faint); font-size: 11px; }
  .body { flex: 1; min-height: 0; margin: 10px 0; display: flex; }
  .sug { flex: 1; overflow: auto; padding-right: 4px; }
  .facet { margin-bottom: 10px; }
  .fname { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 5px; }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); box-shadow: none; cursor: pointer; }
  .pill:hover { border-color: var(--accent); color: var(--accent); filter: none; }
  .graphwrap { position: relative; flex: 1; min-height: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--bg); }
  .graphwrap canvas { display: block; width: 100%; height: 100%; }
  .glegend { position: absolute; top: 8px; right: 8px; background: rgba(20,23,30,.82); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 8px; font-size: 11px; max-width: 150px; max-height: 60%; overflow: auto; pointer-events: none; }
  .glegend .lrow { display: flex; align-items: center; gap: 6px; margin: 2px 0; color: var(--muted); }
  .glegend .lsw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
  .ghover { position: absolute; left: 8px; bottom: 8px; background: rgba(20,23,30,.88); border: 1px solid var(--border);
    border-radius: 8px; padding: 4px 9px; font-size: 12px; color: var(--text); pointer-events: none; }
  .gmsg { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); font-size: 12.5px; pointer-events: none; text-align: center; padding: 0 20px; }
  .lo { color: var(--faint); font-size: 12px; }
  .acts { display: flex; align-items: center; gap: 10px; justify-content: flex-end; padding-top: 10px; border-top: 1px solid var(--border-soft); }
  .acts .lo { margin-right: auto; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

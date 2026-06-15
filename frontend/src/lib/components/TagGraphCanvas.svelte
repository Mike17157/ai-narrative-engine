<script>
  import { tick, onDestroy } from 'svelte';
  import { get } from '$lib/api.js';

  // Self-contained force-directed view of the tag similarity graph around `seed`. Fetches
  // /tags/graph itself; lays out across a world 2× the viewport (pan by dragging the background or
  // moving the cursor to an edge); click a non-seed node to pick it. Calls onpick(tag).
  let { seed = '', kind = 'clothing', onpick } = $props();

  const COLORS = {
    hair:'#e0b04a', eyes:'#4ab0e0', skin:'#e08a6a', body:'#e06aa0', ears_tail:'#b06ae0', face:'#6ae0b0',
    swimwear:'#4ad6e0', piercing:'#e04a6a', makeup:'#e04ab0', dress:'#9a6ae0', top:'#5a8cf0',
    bottom:'#46c08a', outerwear:'#e0883a', legwear:'#c0c04a', footwear:'#a07a5a', headwear:'#e0c04a',
    sleeves:'#7ac0e0', accessories:'#8fa0b5', other:'#5a626f'
  };
  const fmtCount = (n) => (n >= 1000 ? Math.round(n / 1000) + 'k' : '' + (n || 0));

  let canvasEl = $state(null);
  let gnodes = [], gedges = [], gadj = new Map(), ghover = null, gdrag = null, gdragged = false;
  let galpha = 1, graf = null, gctx = null, gW = 0, gH = 0, gDPR = 1;
  let gloading = $state(false);
  let gempty = $state(false);
  let glegend = $state([]);
  let hoverInfo = $state(null);

  // camera: world is WORLD× the viewport each way; pan to navigate.
  const WORLD = 2;
  let WW = 0, WH = 0;
  let panX = 0, panY = 0, panning = false, panSX = 0, panSY = 0, panPX = 0, panPY = 0;
  let autoVX = 0, autoVY = 0;
  function centerPan() { panX = (gW - WW) / 2; panY = (gH - WH) / 2; }
  function clampPan() { panX = Math.min(0, Math.max(gW - WW, panX)); panY = Math.min(0, Math.max(gH - WH, panY)); }

  // (re)load whenever the seed or kind changes
  $effect(() => { const s = seed, k = kind; void k; if (s) loadGraph(s); else stopGraph(); });

  async function loadGraph(s) {
    stopGraph(); gloading = true; gempty = false;
    try {
      const d = await get('/tags/graph?kind=' + kind + '&tags=' + encodeURIComponent(s));
      await buildGraph(d);
    } catch { gnodes = []; gempty = true; }
    gloading = false;
  }
  function ensureSized() {
    // wait until the wrap actually has a non-zero box (it mounts inside a flex layout / dialog
    // animation, so the first measure can be 0×0 — which would scatter every node to the corner)
    return new Promise((res) => { let t = 0;
      const ck = () => { const r = canvasEl?.parentElement?.getBoundingClientRect();
        if ((r && r.width > 4 && r.height > 4) || t++ > 30) res(); else requestAnimationFrame(ck); };
      ck(); });
  }
  let ro = null;
  function observe() {
    if (ro || !canvasEl?.parentElement) return;
    ro = new ResizeObserver(() => { sizeCanvas(); centerPan(); galpha = Math.max(galpha, 0.3); });
    ro.observe(canvasEl.parentElement);
  }
  async function buildGraph(d) {
    await tick();                       // ensure the canvas is in the DOM
    if (!canvasEl) return;
    await ensureSized();                // …and that it has a real size before we lay out
    sizeCanvas();
    observe();
    const ns = d?.nodes || [];
    if (!ns.length) { gnodes = []; gedges = []; gempty = true; return; }
    gempty = false;
    const maxpc = Math.max(1, ...ns.map(n => n.post_count || 0));
    gnodes = ns.map((n, i) => { const ang = i * 2.399, rad = 70 + i * 9;
      return { ...n, r: 4 + 8 * Math.sqrt((n.post_count || 1) / maxpc) + (n.seed ? 5 : 0),
        x: WW / 2 + Math.cos(ang) * rad, y: WH / 2 + Math.sin(ang) * rad, vx: 0, vy: 0,
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
    WW = gW * WORLD; WH = gH * WORLD;
    canvasEl.width = gW * gDPR; canvasEl.height = gH * gDPR; gctx = canvasEl.getContext('2d'); gctx.setTransform(gDPR, 0, 0, gDPR, 0, 0); }
  function step() {
    const REP = 2600, SPRING = 0.018, GRAV = 0.012, DAMP = 0.9, LEN = 100, VMAX = 22;
    for (let i = 0; i < gnodes.length; i++) { const a = gnodes[i];
      for (let j = i + 1; j < gnodes.length; j++) { const b = gnodes[j];
        let dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 0.01, d = Math.sqrt(d2);
        let f = REP / d2, fx = f * dx / d, fy = f * dy / d; a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy; } }
    gedges.forEach(e => { let dx = e.b.x - e.a.x, dy = e.b.y - e.a.y, d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      let f = SPRING * (d - LEN) * (0.4 + e.w * 3), fx = f * dx / d, fy = f * dy / d;
      e.a.vx += fx; e.a.vy += fy; e.b.vx -= fx; e.b.vy -= fy; });
    gnodes.forEach(n => { if (n === gdrag) return;
      n.vx += (WW / 2 - n.x) * GRAV; n.vy += (WH / 2 - n.y) * GRAV; n.vx *= DAMP; n.vy *= DAMP;
      const sp = Math.hypot(n.vx, n.vy); if (sp > VMAX) { n.vx *= VMAX / sp; n.vy *= VMAX / sp; }
      n.x += n.vx * galpha; n.y += n.vy * galpha;
      n.x = Math.max(n.r, Math.min(WW - n.r, n.x)); n.y = Math.max(n.r, Math.min(WH - n.r, n.y)); });
    galpha *= 0.985;
  }
  function draw() {
    if (!gctx) return;
    gctx.clearRect(0, 0, gW, gH);
    gctx.save();
    gctx.translate(panX, panY);
    const hi = ghover ? gadj.get(ghover.id) : null;
    gedges.forEach(e => { const on = ghover && (e.a === ghover || e.b === ghover);
      gctx.strokeStyle = on ? 'rgba(150,180,255,.55)' : 'rgba(120,130,150,' + (0.05 + e.w * 0.5) + ')';
      gctx.lineWidth = on ? 1.6 : Math.max(0.4, e.w * 4);
      gctx.beginPath(); gctx.moveTo(e.a.x, e.a.y); gctx.lineTo(e.b.x, e.b.y); gctx.stroke(); });
    gnodes.forEach(n => { const dim = ghover && n !== ghover && !(hi && hi.has(n.id));
      gctx.globalAlpha = dim ? 0.3 : 1;
      gctx.beginPath(); gctx.arc(n.x, n.y, n.r, 0, 7); gctx.fillStyle = n.col; gctx.fill();
      if (n.seed) { gctx.lineWidth = 2.5; gctx.strokeStyle = '#fff'; gctx.stroke(); }
      const emph = n.seed || n === ghover || (hi && hi.has(n.id));
      gctx.globalAlpha = dim ? 0.4 : (emph ? 1 : 0.82);
      gctx.fillStyle = emph ? '#ffffff' : '#cfd4dc';
      gctx.font = (n.seed ? '600 12px' : (emph ? '11px' : '10px')) + ' system-ui,sans-serif';
      gctx.fillText(n.id, n.x + n.r + 3, n.y + 3);
      gctx.globalAlpha = 1; });
    gctx.restore();
  }
  function loop() {
    if (gnodes.length) {
      if ((autoVX || autoVY) && !gdrag && !panning) { panX += autoVX; panY += autoVY; clampPan(); }
      if (galpha > 0.03 || gdrag) step();
      draw();
    }
    graf = requestAnimationFrame(loop);
  }
  function gat(mx, my) { const wx = mx - panX, wy = my - panY;
    let best = null, bd = 1e9; gnodes.forEach(n => { let d = (n.x - wx) ** 2 + (n.y - wy) ** 2; if (d < bd && d < (n.r + 5) ** 2) { bd = d; best = n; } }); return best; }
  function gmove(e) { const r = canvasEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    if (gdrag) { gdrag.x = mx - panX; gdrag.y = my - panY; gdrag.vx = gdrag.vy = 0; gdragged = true; galpha = Math.max(galpha, 0.4); autoVX = autoVY = 0; }
    else if (panning) { panX = panPX + (mx - panSX); panY = panPY + (my - panSY); clampPan(); gdragged = true; autoVX = autoVY = 0; }
    else {
      ghover = gat(mx, my); canvasEl.style.cursor = ghover ? 'pointer' : 'grab';
      hoverInfo = ghover ? { id: ghover.id, facet: ghover.facet, post_count: ghover.post_count } : null;
      const M = 44, SP = 12;
      autoVX = mx < M ? SP : (mx > gW - M ? -SP : 0);
      autoVY = my < M ? SP : (my > gH - M ? -SP : 0);
    } }
  function gleave() { ghover = null; hoverInfo = null; autoVX = autoVY = 0; }
  function gdown(e) { const r = canvasEl.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
    const n = gat(mx, my); gdragged = false; autoVX = autoVY = 0;
    if (n) { gdrag = n; }
    else { panning = true; panSX = mx; panSY = my; panPX = panX; panPY = panY; canvasEl.style.cursor = 'grabbing'; } }
  function gup() { gdrag = null; panning = false; if (canvasEl) canvasEl.style.cursor = 'grab'; }
  function gclick(e) { if (gdragged) { gdragged = false; return; }
    const r = canvasEl.getBoundingClientRect(); const n = gat(e.clientX - r.left, e.clientY - r.top);
    if (n && !n.seed) onpick?.(n.id); }
  onDestroy(() => { stopGraph(); ro?.disconnect(); ro = null; });
</script>

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
  {:else if gempty}<div class="gmsg">“{seed}” isn’t in the graph — use search to swap it.</div>{/if}
</div>

<svelte:window onmouseup={gup} />

<style>
  .graphwrap { position: relative; flex: 1; min-height: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--bg); }
  .graphwrap canvas { display: block; width: 100%; height: 100%; }
  .glegend { position: absolute; top: 8px; right: 8px; background: rgba(20,23,30,.82); border: 1px solid var(--border);
    border-radius: 8px; padding: 6px 8px; font-size: 11px; max-width: 150px; max-height: 60%; overflow: auto; pointer-events: none; }
  .glegend .lrow { display: flex; align-items: center; gap: 6px; margin: 2px 0; color: var(--muted); }
  .glegend .lsw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
  .ghover { position: absolute; left: 8px; bottom: 8px; background: rgba(20,23,30,.88); border: 1px solid var(--border);
    border-radius: 8px; padding: 4px 9px; font-size: 12px; color: var(--text); pointer-events: none; }
  .gmsg { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); font-size: 12.5px; pointer-events: none; text-align: center; padding: 0 20px; }
</style>

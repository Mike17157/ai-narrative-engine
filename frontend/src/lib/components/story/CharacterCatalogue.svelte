<script>
  // Character catalogue: a circular character-select carousel. Rotate through the whole cast; the
  // centered character stands against the chosen location backdrop and AUTO-CYCLES through their
  // rendered emotion sprites (personality-tinged — see the expression generator). An outfit rail
  // swaps which outfit's sprites show, with quick-add for a standard set (Casual/Swimsuit/Nude…).
  // 3D coverflow is pure CSS so the cards stay live/interactive. See [[character-catalogue]].
  import { get, post } from '$lib/api.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';

  let { storyKey, cast = [], locations = [], onChanged = () => {}, onCharacter = () => {}, onAsk = () => {} } = $props();

  // ── Character rotation ───────────────────────────────────────────────────────
  let center = $state(0);
  $effect(() => { if (cast.length && center > cast.length - 1) center = 0; });
  let charKey = $derived(cast[center]?.character || '');
  $effect(() => { onCharacter(charKey); });   // tell the parent who's on stage (for the outfit agent)
  let charName = $derived(cast[center]?.name || charKey);
  function go(d) { if (cast.length) center = (center + d + cast.length) % cast.length; }
  function onKey(e) { if (e.key === 'ArrowLeft') go(-1); else if (e.key === 'ArrowRight') go(1); }

  let locId = $state('');
  let bgUrl = $derived(locations.find((l) => l.id === locId)?.background || '');

  // ── Wardrobe (fetched for the WHOLE cast so every card shows a transparent cutout sprite, not a
  //    rectangular reference). `fetched` is a plain set so refetching inside the effect can't loop. ─
  let portraits = $state({});          // { charKey: portrait payload }
  const fetched = new Set();
  let outfitIdx = $state(0);
  let bust = $state(0);
  let busy = $state(false);
  let err = $state('');

  async function loadAll(members) {
    for (const c of members) {
      if (fetched.has(c.character)) continue;
      fetched.add(c.character);
      try { const d = await get(`/characters/${c.character}/portraits`); portraits = { ...portraits, [c.character]: d }; }
      catch { /* leave unfetched */ }
    }
  }
  $effect(() => { loadAll(cast); });
  // After the agent builds/edits outfits, the parent calls this to pull fresh portraits.
  export function refresh() { fetched.clear(); portraits = {}; bust++; return loadAll(cast); }
  $effect(() => { charKey; outfitIdx = 0; });        // reset outfit when switching character

  let data = $derived(portraits[charKey] || null);
  let outfits = $derived(data?.outfits || []);
  let outfit = $derived(outfits[outfitIdx] || null);
  // A character's representative transparent cutout (first outfit's rembg base) for their card.
  const spriteOf = (k) => portraits[k]?.outfits?.[0]?.base || null;

  // ── Emotion auto-rotation ────────────────────────────────────────────────────
  // Cycle through the emotions that actually have a rendered sprite for this outfit.
  let cycle = $derived((outfit?.expression_set || []).filter((e) => e.url));
  let emoIdx = $state(0);
  $effect(() => { outfit; cycle.length; emoIdx = 0; });          // reset when outfit changes
  $effect(() => {
    const n = cycle.length;
    if (n < 2) return;
    const t = setInterval(() => { emoIdx = (emoIdx + 1) % n; }, 2200);
    return () => clearInterval(t);
  });
  let shownEmo = $derived(cycle[emoIdx] || null);
  let shownImg = $derived((shownEmo?.url || outfit?.base) ? `${shownEmo?.url || outfit.base}?b=${bust}` : null);
  let shownLabel = $derived(shownEmo?.label || '');

  // Card image: centered → the cycling sprite; others → their cutout sprite (fall back to the
  // reference only if they have no rendered sprite yet).
  const cardImg = (c, i) =>
    (i === center ? (shownImg || spriteOf(charKey) || c.img) : (spriteOf(c.character) || c.img)) || null;

  // ── Coverflow geometry + drag/swipe (wrap-around so rotation feels circular) ───
  const ANGLE = 50, SPREAD = 205, DEPTH = 270;
  // Drag to rotate (mouse + touch): `drag` px drives a live follow via the continuous `pos`; release
  // snaps to the nearest card. A tap (tiny movement) falls through to the card's click-to-select.
  let drag = $state(0);
  let dragging = $state(false);
  let pos = $derived(center - drag / SPREAD);
  let startX = 0, moved = 0, downIdx = -1;
  function pdown(e) {
    dragging = true; startX = e.clientX; moved = 0;
    // remember the card pressed — pointer capture redirects the later click to .flow, so we resolve
    // tap-to-select here instead of via the card's onclick.
    const card = e.target?.closest?.('.card');
    downIdx = card ? Number(card.dataset.i) : -1;
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* noop */ }
  }
  function pmove(e) { if (!dragging) return; drag = e.clientX - startX; moved = Math.max(moved, Math.abs(drag)); }
  function pend() {
    if (!dragging) return;
    dragging = false;
    if (cast.length) {
      if (moved <= 6) {                                  // a TAP → select the pressed card
        if (downIdx >= 0 && downIdx !== center) center = downIdx;
      } else {                                           // a DRAG → snap to the nearest card
        const steps = Math.round(-drag / SPREAD);
        center = ((center + steps) % cast.length + cast.length) % cast.length;
      }
    }
    drag = 0; downIdx = -1;
  }
  function cardStyle(i) {
    const n = cast.length || 1;
    let off = i - pos;                                              // continuous → smooth drag
    if (off > n / 2) off -= n; else if (off < -n / 2) off += n;   // shortest way round
    const a = Math.abs(off);
    const tx = off * SPREAD;
    const ry = Math.max(-ANGLE, Math.min(ANGLE, -off * ANGLE));
    const tz = -a * DEPTH;
    const sc = Math.max(0.58, 1 - a * 0.15);
    const op = Math.max(0.45, 1 - a * 0.32);
    return `transform: translate(-50%,-50%) translateX(${tx}px) translateZ(${tz}px) rotateY(${ry}deg) scale(${sc}); opacity:${op}; z-index:${Math.round(100 - a)};`;
  }

  // ── Regenerate the centered outfit's base; add a standard outfit ──────────────
  async function regen() {
    if (!outfit || busy) return;
    busy = true; err = '';
    try {
      const rc = await post(`/characters/${charKey}/portraits/outfit/${outfit.id}/recompose`, {});
      if (!rc.ok) { err = rc.data?.error || 'recompose failed'; return; }
      const job = startJob('Outfit image render', charName, 'catalogue', 1);
      const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${outfit.id}/candidate`, {}, {}, job);
      if (r.ok && r.data?.image) {
        const sv = await post(`/characters/${charKey}/portraits/outfit/${outfit.id}/base`, { data: r.data.image });
        if (sv.data?.url) { outfit.base = sv.data.url; bust++; onChanged(); }
        job.done = 1; job.status = 'done';
      } else { err = r.data?.error || 'render failed'; job.status = 'error'; }
    } finally { busy = false; }
  }
  // Outfits aren't a fixed list — you build them by TALKING to the outfit agent (the bottom dock).
  // This just seeds it with a starting message; the first outfit is keyed to the opening scene.
  function seedOutfit() {
    const verb = outfits.length ? 'Add another outfit for' : "Design a first outfit for";
    onAsk(`${verb} ${charName}${outfits.length ? '' : ", based on the story's opening scene"}.`);
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="cat">
  <!-- Backdrop picker -->
  <div class="bar">
    <span class="blbl">Backdrop</span>
    <button class="chip" class:on={locId === ''} onclick={() => locId = ''}>⬚ Studio</button>
    {#each locations as l (l.id)}
      <button class="chip" class:on={locId === l.id} onclick={() => locId = l.id} title={l.description || l.name}>
        {#if l.background}<img src={l.background} alt={l.name} />{/if}{l.name}
      </button>
    {/each}
  </div>

  <!-- Stage: backdrop + 3D character coverflow -->
  <div class="stage" class:studio={!bgUrl} style={bgUrl ? `background-image:url(${bgUrl})` : ''}>
    {#if !cast.length}
      <div class="empty">No cast yet.</div>
    {:else}
      <div class="flow" class:dragging onpointerdown={pdown} onpointermove={pmove}
           onpointerup={pend} onpointercancel={pend}>
        {#each cast as c, i (c.character)}
          <div class="card" class:center={i === center} style={cardStyle(i)}
               data-i={i} role="button" tabindex="-1" aria-label={c.name}>
            <div class="sprite">
              {#if cardImg(c, i)}<img src={cardImg(c, i)} alt={c.name} />{:else}<div class="noimg">🎭</div>{/if}
              {#if i === center && busy}<div class="cardspin"><span class="spin"></span></div>{/if}
            </div>
            <div class="namep">
              <span class="nm">{c.primary ? '★ ' : ''}{c.name}</span>
              {#if i === center && shownLabel}<span class="emo">{shownLabel}</span>{/if}
            </div>
          </div>
        {/each}
      </div>
      <button class="nav prev" onclick={() => go(-1)} aria-label="Previous">‹</button>
      <button class="nav next" onclick={() => go(1)} aria-label="Next">›</button>
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
  </div>

  <!-- Outfit rail for the centered character -->
  {#if charKey}
    <div class="rail">
      {#each outfits as o, i (o.id)}
        <button class="orow" class:on={i === outfitIdx} onclick={() => outfitIdx = i} title={o.name}>
          {#if o.base}<img src={`${o.base}?b=${bust}`} alt="" />{:else}<span class="oph">·</span>{/if}
          <span class="onm">{o.name}</span>
        </button>
      {:else}
        <span class="hint">No outfits yet — ask the agent below to design one from the opening scene.</span>
      {/each}
      <span class="sp"></span>
      {#if outfit}
        <button class="plan" onclick={() => onAsk(`Compose the "${outfit.name}" outfit prompt for everyone in the cast who has it but hasn't generated it yet.`)}
                title="Have the agent write this outfit's prompt for the rest of the cast who wear it">✦ Apply to cast</button>
      {/if}
      <button class="plan" onclick={seedOutfit} title="Hand off to the outfit agent below">
        {outfits.length ? '✦ Add outfit' : '✦ Outfit from opening scene'}
      </button>
      {#if outfit}
        <button class="regen" disabled={busy} onclick={regen}>{busy ? 'rendering…' : (outfit.base ? '↻ Regenerate' : '🎨 Render')}</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .cat { display: flex; flex-direction: column; height: 100%; min-height: 0; }

  .bar { display: flex; align-items: center; gap: 6px; padding: 8px 14px; flex-wrap: wrap;
         border-bottom: 1px solid var(--border-soft); background: var(--panel); }
  .blbl { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin-right: 4px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px 4px 6px; border-radius: 999px;
          background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); font-size: 12px;
          cursor: pointer; white-space: nowrap; }
  .chip:hover { color: var(--text); border-color: var(--border); }
  .chip.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--elev)); }
  .chip img { width: 30px; height: 20px; border-radius: 5px; object-fit: cover; flex: none; }

  .stage { position: relative; flex: 1; min-height: 0; overflow: hidden;
           background-size: cover; background-position: center;
           perspective: 1400px; perspective-origin: 50% 44%; display: grid; place-items: center; }
  .stage.studio { background: radial-gradient(120% 90% at 50% 8%, color-mix(in srgb, var(--accent) 9%, var(--bg)), var(--bg) 70%); }
  .stage::after { content: ''; position: absolute; inset: 0; pointer-events: none;
                  background: linear-gradient(180deg, rgba(0,0,0,.16), rgba(0,0,0,.46)); }
  .empty { color: #fff; font-size: 14px; text-shadow: 0 1px 4px rgba(0,0,0,.6); z-index: 2; }

  .flow { position: absolute; inset: 0; transform-style: preserve-3d; z-index: 1;
          cursor: grab; touch-action: pan-y; user-select: none; }
  .flow.dragging { cursor: grabbing; }
  .flow.dragging .card { transition: none; }
  .card { position: absolute; left: 50%; top: 50%; width: 300px; height: 78%;
          transition: transform .45s cubic-bezier(.22,.61,.36,1), opacity .45s; cursor: pointer; }
  .sprite { position: relative; width: 100%; height: 100%; display: grid; place-items: end center; }
  .sprite img { max-width: 100%; max-height: 100%; object-fit: contain; filter: drop-shadow(0 18px 26px rgba(0,0,0,.55)); }
  .noimg { align-self: center; display: grid; place-items: center; font-size: 72px; opacity: .45;
           filter: drop-shadow(0 10px 18px rgba(0,0,0,.5)); }
  .cardspin { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.32); }

  .namep { position: absolute; bottom: 2px; left: 50%; transform: translateX(-50%);
           display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .card:not(.center) .namep { opacity: .5; }
  .nm { font-size: 13px; font-weight: 700; color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.7);
        background: rgba(0,0,0,.42); padding: 3px 11px; border-radius: 999px; white-space: nowrap; }
  .emo { font-size: 11px; font-weight: 600; color: #0b0e14; background: var(--accent); padding: 2px 9px;
         border-radius: 999px; box-shadow: 0 2px 8px rgba(0,0,0,.4); }

  .nav { position: absolute; top: 50%; transform: translateY(-50%); z-index: 10; width: 42px; height: 42px;
         border-radius: 50%; font-size: 24px; line-height: 1; background: rgba(20,22,31,.7);
         border: 1px solid rgba(255,255,255,.18); color: #fff; cursor: pointer; }
  .nav:hover { background: rgba(20,22,31,.92); }
  .nav.prev { left: 18px; } .nav.next { right: 18px; }
  .err { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 10;
         background: var(--bad, #b54); color: #fff; font-size: 12px; padding: 5px 12px; border-radius: 8px; }

  /* Outfit rail */
  .rail { display: flex; align-items: center; gap: 7px; padding: 9px 14px; flex-wrap: wrap;
          border-top: 1px solid var(--border-soft); background: var(--panel); }
  .orow { display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px 4px 4px; border-radius: 9px;
          background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); font-size: 12.5px;
          cursor: pointer; }
  .orow:hover { color: var(--text); border-color: var(--border); }
  .orow.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--elev)); }
  .orow img { width: 30px; height: 38px; border-radius: 6px; object-fit: cover; flex: none; }
  .oph { width: 30px; height: 38px; border-radius: 6px; display: grid; place-items: center; background: var(--elev-2); color: var(--faint); flex: none; }
  .hint { font-size: 12px; color: var(--faint); font-style: italic; }
  .plan { font-size: 12.5px; font-weight: 600; padding: 7px 13px; border-radius: 9px; background: none;
          border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .plan:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .sp { flex: 1; }
  .regen { font-size: 12.5px; font-weight: 600; padding: 7px 15px; border-radius: 9px; background: var(--accent);
           color: #fff; border: none; cursor: pointer; box-shadow: 0 3px 12px rgba(0,0,0,.3); }
  .regen:disabled { opacity: .7; cursor: default; }

  .spin { width: 22px; height: 22px; border-radius: 50%; border: 3px solid rgba(255,255,255,.3);
          border-top-color: #fff; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

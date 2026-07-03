<script>
  // Character catalogue: a circular character-select carousel. Rotate through the whole cast; the
  // centered character stands against the chosen location backdrop and AUTO-CYCLES through their
  // rendered emotion sprites (personality-tinged — see the expression generator). An outfit rail
  // swaps which outfit's sprites show, with quick-add for a standard set (Casual/Swimsuit/Nude…).
  // 3D coverflow is pure CSS so the cards stay live/interactive. See [[character-catalogue]].
  import { get, post } from '$lib/api.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';
  import GenStream from '$lib/components/shared/GenStream.svelte';
  import BaseStudio from './BaseStudio.svelte';
  import EmblaCarousel from 'embla-carousel';

  let { storyKey, cast = [], locations = [], onChanged = () => {}, onCharacter = () => {} } = $props();

  // ── Character rotation ───────────────────────────────────────────────────────
  let center = $state(0);
  $effect(() => { if (cast.length && center > cast.length - 1) center = 0; });
  let charKey = $derived(cast[center]?.character || '');
  $effect(() => { onCharacter(charKey); });   // tell the parent who's on stage (for the outfit agent)
  let charName = $derived(cast[center]?.name || charKey);
  // SELECTION is plain state (click a card / arrows) — decoupled from scrolling, because with a
  // small cast every card fits on screen and there is nothing to scroll (coupling select to the
  // carousel's snaps froze it). Embla (the standard carousel, core used directly — the svelte
  // wrapper's init event misfires) supplies drag/snap ONLY once the cast overflows the viewport.
  let embla = $state(null);
  function carousel(node) {
    embla = EmblaCarousel(node, { align: 'center', containScroll: 'trimSnaps', skipSnaps: true });
    return { destroy: () => { embla?.destroy(); embla = null; } };
  }
  function select(i) { center = i; embla?.scrollTo(i); }
  function go(d) { if (cast.length) select((center + d + cast.length) % cast.length); }
  function onKey(e) { if (e.key === 'ArrowLeft') go(-1); else if (e.key === 'ArrowRight') go(1); }

  // Backdrop picker — purely visual (outfits are selected outfit-by-outfit at the top bar).
  let locId = $state('');
  let bgUrl = $derived(locations.find((l) => l.id === locId)?.background || '');
  // A character's default look = their EVERYDAY outfit (first non-base/swim), never the swim base.
  function everydayIdx(k) {
    const outs = portraits[k]?.outfits || [];
    const i = outs.findIndex((o) => !/base|swim/i.test(o.name || ''));
    return i >= 0 ? i : 0;
  }

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
  // Switching character resets to their everyday outfit; the TOP outfit bar picks outfit-by-outfit.
  $effect(() => { charKey; portraits; outfitIdx = everydayIdx(charKey); });

  let data = $derived(portraits[charKey] || null);
  let outfits = $derived(data?.outfits || []);
  let outfit = $derived(outfits[outfitIdx] || null);
  // A side card's cutout = that character's everyday outfit base.
  const spriteOf = (k) => portraits[k]?.outfits?.[everydayIdx(k)]?.base || null;

  // ── Selected emotion — YOU pick it (click a card in the strip); no auto-rotation.
  // '' = the outfit's base look. The centered carousel card shows exactly this selection.
  let selEmo = $state('');
  $effect(() => { charKey; outfit; selEmo = ''; });              // reset on character/outfit switch
  let shownEmo = $derived((outfit?.expression_set || []).find((e) => e.emotion === selEmo && e.url) || null);
  let shownImg = $derived((shownEmo?.url || outfit?.base) ? `${shownEmo?.url || outfit.base}?b=${bust}` : null);
  let shownLabel = $derived(shownEmo?.label || '');

  // Card image: centered → the cycling sprite; others → their cutout sprite (fall back to the
  // reference only if they have no rendered sprite yet).
  const cardImg = (c, i) =>
    (i === center ? (shownImg || spriteOf(charKey) || c.img) : (spriteOf(c.character) || c.img)) || null;

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
  // ── DIRECT wardrobe generation — the clear trigger. One click: plan the character's outfits
  // (streamed LLM job, watched live on the stage), persist the plan into the portrait manifest,
  // then auto-render the FIRST outfit's base so a sprite becomes visible without another step.
  let wardrobeJob = $state(null);
  let planning = $state(false);
  async function genWardrobe() {
    if (planning || !charKey) return;
    planning = true; err = '';
    const r = await post(`/stories/${storyKey}/plan-wardrobe`, { character: charKey });
    if (!r.ok) { err = r.data?.error || 'wardrobe planning failed'; planning = false; return; }
    wardrobeJob = r.data.job;
  }
  async function onWardrobePlan(plan) {
    const k = plan?.character || charKey;          // rotation-safe: persist to the planned character
    const sv = await post(`/characters/${k}/portraits/wardrobe`, plan);
    if (!sv.ok) { err = sv.data?.error || 'could not save the wardrobe'; return; }
    await refresh(); onChanged();
    if (k === charKey && outfits.length && !outfits[0].base) { outfitIdx = 0; regen(); }
  }
  function onWardrobeDone() { planning = false; wardrobeJob = null; }

  // ── The selected outfit's EMOTION SET — unique per outfit (its own expression_set), shown as
  // a strip of sprite cards with per-cell render/re-roll + a render-all job. This replaces the
  // old outfits×emotions table: one outfit at a time, its emotions in full.
  let shownEmos = $derived.by(() => {
    const es = outfit?.expression_set || [];
    const inRange = es.filter((e) => e.in_range);
    if (inRange.length) return inRange;
    // older manifests don't flag in_range — trim to the character's personality range instead
    const range = new Set((data?.affect?.range || []).map((r) => (typeof r === 'string' ? r : r.emotion)));
    const ranged = es.filter((e) => range.has(e.emotion));
    return ranged.length ? ranged : es;
  });
  let cellBusy = $state({});
  async function refreshChar(k) {
    try { const d = await get(`/characters/${k}/portraits`); portraits = { ...portraits, [k]: d }; bust++; }
    catch { /* keep the stale payload */ }
  }
  async function renderEmo(emo) {
    const k = charKey, oid = outfit?.id, ck = `${oid}:${emo}`;
    if (!oid || cellBusy[ck]) return;
    cellBusy = { ...cellBusy, [ck]: true }; err = '';
    const job = startJob('Sprite render', `${charName} — ${emo}`, 'catalogue', 1);
    const r = await limitedPost(`/characters/${k}/sprite-candidate`, { outfit_id: oid, emotion: emo }, {}, job);
    if (r.ok && r.data?.image) {
      await post(`/characters/${k}/sprite/select`, { outfit_id: oid, emotion: emo, data: r.data.image });
      job.done = 1; job.status = 'done';
      await refreshChar(k);
    } else { err = r.data?.error || 'sprite render failed'; job.status = 'error'; }
    cellBusy = { ...cellBusy, [ck]: false };
  }
  // ── FIX IDENTITY: backfill a clean, clothing-free canonical appearance so every render
  // leads with a consistent identity anchor (fixes hair drift / outfit-fighting appearance).
  let identBusy = $state(false);
  let identDone = $state(false);
  async function fixIdentity() {
    if (identBusy || !charKey) return;
    identBusy = true; err = '';
    const r = await post(`/characters/${charKey}/normalize-identity`, {});
    identBusy = false;
    if (r.ok) { identDone = true; setTimeout(() => (identDone = false), 4000); }
    else err = r.data?.error || 'identity fix failed';
  }

  // ── STYLE BROADCASTING: distill the image currently ON STAGE (base or selected emotion)
  // into the global style anchor — every character's future renders open with it.
  let styleBusy = $state(false);
  let styleSet = $state(false);
  async function broadcastStyle() {
    const url = (shownEmo?.url || outfit?.base || '').split('?')[0];
    if (!url || styleBusy) return;
    styleBusy = true; err = '';
    const r = await post('/style', { image: url,
      source: `${charName} — ${outfit?.name}${shownEmo ? ' · ' + (shownEmo.label || shownEmo.emotion) : ''}` });
    styleBusy = false;
    if (r.ok) { styleSet = true; setTimeout(() => (styleSet = false), 4000); }
    else err = r.data?.error || 'style broadcast failed';
  }

  // The BASE STUDIO modal — deliberate base regeneration: styles + prompt mutation + candidates.
  let showStudio = $state(false);
  async function onStudioSaved() { await refreshChar(charKey); onChanged(); }

  let emoJob = $state(null);
  async function renderAllEmos() {
    if (!outfit || emoJob) return;
    err = '';
    const r = await post(`/characters/${charKey}/portraits/render-emotions`,
                         { screen: 'catalogue', outfit_id: outfit.id });
    if (r.ok && r.data?.job) emoJob = r.data.job;
    else err = r.data?.error || 'could not start the render';
  }
  function onEmosDone() { emoJob = null; refreshChar(charKey); }
</script>

<svelte:window onkeydown={onKey} />

<div class="cat">
  <!-- OUTFITS — selection is outfit-by-outfit, at the top. Each outfit carries its OWN
       emotion set + sprites (the strip under the stage). -->
  <div class="bar">
    <span class="blbl">Outfits</span>
    {#each outfits as o, i (o.id)}
      <button class="chip ochip" class:on={i === outfitIdx} onclick={() => (outfitIdx = i)} title={o.concept || o.name}>
        {#if o.base}<img class="oimg" src={`${o.base}?b=${bust}`} alt="" />{/if}{o.name}
      </button>
    {:else}
      <span class="hint">{charKey && portraits[charKey] ? 'No outfits yet — generate a wardrobe below.' : ' '}</span>
    {/each}
    <span class="sp"></span>
    {#if outfits.length}
      <button class="plan" onclick={genWardrobe} disabled={planning || !!wardrobeJob}
              title="Plan MORE outfits for this character (additive) — streamed, then render">
        {planning || wardrobeJob ? '🎨 Planning…' : '＋ Plan more outfits'}
      </button>
    {/if}
  </div>

  <!-- Backdrop — visual only -->
  <div class="bar sub">
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
      <div class="viewport" use:carousel>
        <div class="container">
          {#each cast as c, i (c.character)}
            <div class="slide" class:center={i === center}
                 onclick={() => select(i)} onkeydown={(e) => e.key === 'Enter' && select(i)}
                 role="button" tabindex="0" aria-label={`Select ${c.name}`}>
              <div class="sprite">
                {#if cardImg(c, i)}<img src={cardImg(c, i)} alt={c.name} draggable="false" />{:else}<div class="noimg">🎭</div>{/if}
                {#if i === center && busy}<div class="cardspin"><span class="spin"></span></div>{/if}
              </div>
              <div class="namep">
                <span class="nm">{c.primary ? '★ ' : ''}{c.name}</span>
                {#if i === center && shownLabel}<span class="emo">{shownLabel}</span>{/if}
              </div>
            </div>
          {/each}
        </div>
      </div>
      <button class="nav prev" onclick={() => go(-1)} aria-label="Previous">‹</button>
      <button class="nav next" onclick={() => go(1)} aria-label="Next">›</button>
      <!-- No wardrobe yet → THE trigger, front and center on the stage -->
      {#if charKey && portraits[charKey] && !outfits.length && !wardrobeJob}
        <button class="bigcta" onclick={genWardrobe} disabled={planning}>
          🎨 {planning ? 'Planning…' : `Generate ${charName}'s wardrobe`}
        </button>
      {/if}
      {#if wardrobeJob}
        <div class="wjob">
          <GenStream jobId={wardrobeJob} title={`Wardrobe — ${charName}`}
                     onResult={onWardrobePlan} onError={(m) => (err = m)} onDone={onWardrobeDone} />
        </div>
      {/if}
      {#if emoJob}
        <div class="wjob">
          <GenStream jobId={emoJob} title={`${outfit?.name || 'Outfit'} — rendering the emotion set`}
                     onError={(m) => (err = m)} onDone={onEmosDone} />
        </div>
      {/if}
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
  </div>

  <!-- The selected outfit's EMOTION SET — its own sprites, one card per emotion, with
       per-cell render/re-roll and a render-all job. -->
  {#if charKey && outfit}
    <div class="emopane">
    <div class="emohead">
      <b>🎭 {outfit.name}</b>
      <span class="hint">{shownEmos.filter((e) => e.url).length}/{shownEmos.length} rendered · click a card to put it on stage</span>
      <span class="sp"></span>
      <button class="plan" onclick={fixIdentity} disabled={identBusy}
              title="Backfill a clean, clothing-free canonical appearance for this character so every render keeps a consistent identity (fixes hair drift)">
        {identBusy ? '🧬 Fixing…' : identDone ? '✓ Identity fixed' : '🧬 Fix identity'}</button>
      <button class="plan" onclick={broadcastStyle} disabled={styleBusy || (!shownEmo && !outfit?.base)}
              title="Distill THIS image's art style (vision model) and broadcast it as the anchor every character's future renders open with">
        {styleBusy ? '⭐ Distilling…' : styleSet ? '✓ Style set for everyone' : '⭐ Set as cast style'}</button>
      <button class="plan" onclick={renderAllEmos} disabled={!!emoJob}
              title="Render this outfit's whole emotion set (streamed job)">{emoJob ? '✨ Rendering…' : '✨ Render all'}</button>
    </div>
    <div class="emostrip">
      <div class="ecard base" class:missing={!outfit.base} class:on={selEmo === ''}
           role="button" tabindex="0" onclick={() => (selEmo = '')}
           onkeydown={(e) => e.key === 'Enter' && (selEmo = '')} title="Show the base look on stage">
        {#if outfit.base}<img src={`${outfit.base}?b=${bust}`} alt="base" />{:else}<div class="eph">🎨</div>{/if}
        <div class="efoot">
          <span class="elabel">base</span>
          <button class="ebtn" disabled={busy} onclick={(ev) => { ev.stopPropagation(); showStudio = true; }}
                  title="Open the Base Studio — styles, prompt mutation, candidates">{busy ? '…' : '🎛'}</button>
        </div>
      </div>
      {#each shownEmos as e (e.emotion)}
        {@const ck = `${outfit.id}:${e.emotion}`}
        <div class="ecard" class:missing={!e.url} class:on={selEmo === e.emotion}
             role="button" tabindex="0" onclick={() => { if (e.url) selEmo = e.emotion; }}
             onkeydown={(ev) => ev.key === 'Enter' && e.url && (selEmo = e.emotion)}
             title={e.url ? 'Show this emotion on stage' : 'Not rendered yet'}>
          {#if e.url}<img src={`${e.url}?b=${bust}`} alt={e.label || e.emotion} />{:else}<div class="eph">·</div>{/if}
          <div class="efoot">
            <span class="elabel">{e.label || e.emotion}</span>
            <button class="ebtn" disabled={!!cellBusy[ck]} onclick={(ev) => { ev.stopPropagation(); renderEmo(e.emotion); }}
                    title={e.url ? 'Re-roll this sprite' : 'Render this sprite'}>{cellBusy[ck] ? '…' : (e.url ? '↻' : '🎨')}</button>
          </div>
        </div>
      {/each}
    </div>
    </div>
  {/if}

  {#if showStudio && outfit}
    <BaseStudio {charKey} {charName} {outfit} onSaved={onStudioSaved} onClose={() => (showStudio = false)} />
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

  /* Embla (the standard carousel) — viewport clips, container is the flex track, slides snap. */
  .viewport { position: absolute; inset: 0; overflow: hidden; z-index: 1; }
  /* `safe center` centers the row when the whole cast fits; falls back to start on overflow
     so Embla's scroll math stays intact for large casts. */
  .container { display: flex; height: 100%; touch-action: pan-y pinch-zoom; justify-content: safe center; }
  .slide { flex: 0 0 320px; min-width: 0; position: relative; padding: 3.5% 0 10px;
           cursor: pointer; outline: none; user-select: none;
           transform: scale(.8); opacity: .5; transition: transform .3s ease, opacity .3s ease; }
  .slide.center { transform: scale(1); opacity: 1; }
  .slide:not(.center):hover { opacity: .75; }
  .sprite { position: relative; width: 100%; height: 100%; display: grid; place-items: end center; }
  .sprite img { max-width: 100%; max-height: 100%; object-fit: contain; -webkit-user-drag: none;
                filter: drop-shadow(0 18px 26px rgba(0,0,0,.55)); }
  .noimg { align-self: center; display: grid; place-items: center; font-size: 72px; opacity: .45;
           filter: drop-shadow(0 10px 18px rgba(0,0,0,.5)); }
  .cardspin { position: absolute; inset: 0; display: grid; place-items: center; background: rgba(0,0,0,.32); }

  .namep { position: absolute; bottom: 2px; left: 50%; transform: translateX(-50%);
           display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .slide:not(.center) .namep { opacity: .5; }
  .nm { font-size: 13px; font-weight: 700; color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.7);
        background: rgba(0,0,0,.42); padding: 3px 11px; border-radius: 999px; white-space: nowrap; }
  .emo { font-size: 11px; font-weight: 600; color: #0b0e14; background: var(--accent); padding: 2px 9px;
         border-radius: 999px; box-shadow: 0 2px 8px rgba(0,0,0,.4); }

  .nav { position: absolute; top: 50%; margin-top: -21px; z-index: 10; width: 42px; height: 42px;
         padding: 0; display: grid; place-items: center; line-height: 0;
         border-radius: 50%; font-size: 26px; background: rgba(20,22,31,.7);
         border: 1px solid rgba(255,255,255,.18); color: #fff; cursor: pointer; }
  /* centered via margin, NOT transform — the global button:active { transform: translateY(1px) }
     used to REPLACE the translateY(-50%) centering, so the button leapt half its height on press
     and the release landed outside it (the "sometimes clicks don't work"). */
  .nav:hover { background: rgba(20,22,31,.92); }
  .nav.prev { left: 18px; } .nav.next { right: 18px; }
  .err { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); z-index: 10;
         background: var(--bad, #b54); color: #fff; font-size: 12px; padding: 5px 12px; border-radius: 8px; }

  /* Bars: outfits on top (with base thumbs), backdrop beneath (visual only). */
  .bar.sub { padding-top: 5px; }
  .ochip .oimg { width: 26px; height: 34px; border-radius: 5px; object-fit: cover; object-position: top; flex: none; }
  .hint { font-size: 12px; color: var(--faint); font-style: italic; }
  .plan { font-size: 12.5px; font-weight: 600; padding: 7px 13px; border-radius: 9px; background: none;
          border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .plan:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .sp { flex: 1; }

  /* The selected outfit's EMOTION pane — a slim header (name · rendered count · Render all)
     over TWO fixed rows of sprite cards (scrolls horizontally; fixed height so the stage never
     resizes). Clicking a card puts that sprite ON STAGE. */
  .emopane { border-top: 1px solid var(--border-soft); background: var(--panel); }
  .emohead { display: flex; align-items: center; gap: 10px; padding: 7px 14px 0;
             font-size: 12.5px; color: var(--text); }
  .emohead .plan { padding: 5px 12px; font-size: 12px; }
  .emostrip { display: grid; grid-auto-flow: column; grid-template-rows: repeat(3, minmax(0, 1fr));
              gap: 8px; padding: 8px 14px 9px; height: 452px; box-sizing: border-box;
              overflow-x: auto; overflow-y: hidden; }
  .ecard { width: 108px; display: flex; flex-direction: column; border-radius: 10px; overflow: hidden;
           background: var(--elev); border: 1px solid var(--border-soft); cursor: pointer; outline: none; }
  .ecard.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  .ecard.base { grid-row: span 3; width: 168px; }
  .ecard img { flex: 1; min-height: 0; width: 100%; object-fit: cover; object-position: top; }
  .eph { flex: 1; display: grid; place-items: center; font-size: 20px; color: var(--faint);
         background: var(--elev-2); }
  .ecard.missing { opacity: .75; border-style: dashed; }
  .efoot { display: flex; align-items: center; justify-content: space-between; gap: 4px; padding: 3px 6px; }
  .elabel { font-size: 10px; font-weight: 600; color: var(--muted); overflow: hidden;
            text-overflow: ellipsis; white-space: nowrap; }
  .ebtn { flex: none; width: 22px; height: 22px; padding: 0; display: grid; place-items: center;
          border-radius: 6px; background: var(--elev-2); border: 1px solid var(--border-soft);
          color: var(--muted); cursor: pointer; font-size: 11px; }
  .ebtn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }

  .spin { width: 22px; height: 22px; border-radius: 50%; border: 3px solid rgba(255,255,255,.3);
          border-top-color: #fff; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* The wardrobe trigger + its live job stream, floated on the stage */
  .bigcta { position: absolute; bottom: 56px; left: 50%; transform: translateX(-50%); z-index: 10;
            font-size: 14px; font-weight: 700; padding: 11px 22px; border-radius: 999px;
            background: var(--accent); color: #fff; border: none; cursor: pointer;
            box-shadow: 0 6px 24px rgba(0,0,0,.45); }
  .bigcta:hover:not(:disabled) { filter: brightness(1.1); }
  .wjob { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); z-index: 11;
          width: min(560px, 92%); }
</style>

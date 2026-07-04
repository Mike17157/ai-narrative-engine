<script>
  // Character catalogue: a circular character-select carousel. Rotate through the whole cast; the
  // centered character stands on a studio backdrop and shows the selected emotion sprite. An outfit
  // rail swaps which outfit's sprites show, with quick-add for a standard set (Casual/Swimsuit/Nude…).
  // 3D coverflow is pure CSS so the cards stay live/interactive. See [[character-catalogue]].
  import { get, post } from '$lib/api.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';
  import GenStream from '$lib/components/shared/GenStream.svelte';
  import EmblaCarousel from 'embla-carousel';

  let { storyKey, cast = [], onChanged = () => {}, onCharacter = () => {} } = $props();

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
  // The representative sprite for an outfit (base is gone — references never worked): the NEUTRAL
  // emotion sprite, else the first rendered emotion.
  const outfitSprite = (o) => {
    const es = o?.expression_set || [];
    return (es.find((e) => e.emotion === 'neutral' && e.url)?.url) || (es.find((e) => e.url)?.url) || null;
  };
  const spriteOf = (k) => outfitSprite(portraits[k]?.outfits?.[everydayIdx(k)]);

  // ── Selected emotion — YOU pick it (click a card in the strip); no auto-rotation.
  // '' = the neutral look. The centered carousel card shows exactly this selection.
  let selEmo = $state('');
  $effect(() => { charKey; outfit; selEmo = ''; });              // reset on character/outfit switch
  let shownEmo = $derived((outfit?.expression_set || []).find((e) => e.emotion === selEmo && e.url) || null);
  let shownImg = $derived((() => { const u = shownEmo?.url || outfitSprite(outfit); return u ? `${u}?b=${bust}` : null; })());
  let shownLabel = $derived(shownEmo?.label || '');

  // Card image: centered → the selected sprite; others → their neutral sprite (fall back to the
  // reference only if they have no rendered sprite yet).
  const cardImg = (c, i) =>
    (i === center ? (shownImg || spriteOf(charKey) || c.img) : (spriteOf(c.character) || c.img)) || null;
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
    // Auto-render the NEUTRAL sprite so the card isn't empty right after planning.
    if (k === charKey && outfit && !outfitSprite(outfit)) renderEmo('neutral');
  }
  function onWardrobeDone() { planning = false; wardrobeJob = null; }

  // ── The selected outfit's EMOTION SET — unique per outfit (its own expression_set), shown as
  // a strip of sprite cards with per-cell render/re-roll + a render-all job. This replaces the
  // old outfits×emotions table: one outfit at a time, its emotions in full.
  // INTIMACY emotions are hidden by default (the affect generator sometimes leaks them onto SFW
  // characters — the "untracked" emotions). They still exist in data; a mature-mode toggle can
  // surface them later.
  const INTIMACY = new Set(['anticipation','desire','teasing','comfort','relief','ecstasy','arousal',
    'intensity','release','submission','arrogant','condescension','discomfort','humiliation','pain',
    'lustful','pleasure','begging']);
  let shownEmos = $derived.by(() => {
    const es = (outfit?.expression_set || []).filter((e) => !INTIMACY.has(e.emotion));
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
  async function renderEmo(emo, reroll = false) {
    const k = charKey, oid = outfit?.id, ck = `${oid}:${emo}`;
    if (!oid || cellBusy[ck]) return;
    cellBusy = { ...cellBusy, [ck]: true }; err = '';
    const job = startJob('Sprite render', `${charName} — ${emo}`, 'catalogue', 1);
    // First render uses the fixed sprite seed (matches the set); a re-roll randomizes so it differs.
    const r = await limitedPost(`/characters/${k}/sprite-candidate`, { outfit_id: oid, emotion: emo, reroll }, {}, job);
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
    const url = (shownEmo?.url || outfitSprite(outfit) || '').split('?')[0];
    if (!url || styleBusy) return;
    styleBusy = true; err = '';
    const r = await post('/style', { image: url,
      source: `${charName} — ${outfit?.name}${shownEmo ? ' · ' + (shownEmo.label || shownEmo.emotion) : ''}` });
    styleBusy = false;
    if (r.ok) { styleSet = true; setTimeout(() => (styleSet = false), 4000); }
    else err = r.data?.error || 'style broadcast failed';
  }

  let emoJob = $state(null);
  let emoProg = $state({ done: 0, total: 0 });   // live render progress → the divider bar
  async function renderAllEmos() {
    if (!outfit || emoJob) return;
    err = ''; emoProg = { done: 0, total: 0 };
    const r = await post(`/characters/${charKey}/portraits/render-emotions`,
                         { screen: 'catalogue', outfit_id: outfit.id });
    if (r.ok && r.data?.job) emoJob = r.data.job;
    else err = r.data?.error || 'could not start the render';
  }
  // STREAM: each sprite announces itself the moment it's saved → patch that one cell live
  // (no full refetch) so the strip fills in as renders land.
  function onSprite(ev) {
    if (ev?.type === 'progress') return;   // handled by onProgress
    if (ev?.type !== 'sprite' || !ev.url) return;
    const p = portraits[charKey]; if (!p) return;
    const o = (p.outfits || []).find((x) => x.id === ev.outfit); if (!o) return;
    const cell = (o.expression_set || []).find((e) => e.emotion === ev.emotion);
    if (cell) { cell.url = ev.url; o.expressions = { ...(o.expressions || {}), [ev.emotion]: `${ev.emotion}.png` }; }
    portraits = { ...portraits }; bust++;
  }
  function onEmosDone() { emoJob = null; emoProg = { done: 0, total: 0 }; refreshChar(charKey); }

  // ── EMOTION RANGE EDITING — the outfit's emotion SET is register-specific and editable. The
  // POST returns the fresh portrait payload, so the strip cells (driven by in_range) update live.
  let editRange = $state(false);
  let rangeBusy = $state(false);
  // Full pool for the editor: every non-intimacy emotion, with its in/out flag for THIS outfit.
  let poolEmos = $derived((outfit?.expression_set || []).filter((e) => !INTIMACY.has(e.emotion)));
  async function saveRange(body) {
    if (!outfit || rangeBusy) return;
    rangeBusy = true; err = '';
    const r = await post(`/characters/${charKey}/portraits/affect`, { outfit_id: outfit.id, ...body });
    rangeBusy = false;
    if (r.ok && r.data) { portraits = { ...portraits, [charKey]: r.data }; }
    else err = r.data?.error || 'emotion range update failed';
  }
  // Re-curate this outfit's register set from scratch (v4pro reads its attire + persona).
  const recomposeRange = () => saveRange({ compose: true });

  // ── ≣ LAYERS — the image card, inspectable: every layer that stacks into the selected sprite's
  // prompt (style ← Overview, identity/outfit/emotion/pose/face ← Cast, final ← composer), like a
  // workflow you can read. Dry-run endpoint; nothing renders.
  let stack = $state(null);          // { loading, emotion, layers: [{id,label,source,text}] } | null
  async function showLayers() {
    const emo = selEmo || 'neutral';
    stack = { loading: true, emotion: emo, layers: [] };
    const r = await post(`/characters/${charKey}/sprite-stack`, { outfit_id: outfit?.id, emotion: emo });
    if (r.ok && r.data?.layers) stack = { loading: false, emotion: emo, layers: r.data.layers };
    else { stack = null; err = r.data?.error || 'could not build the layer stack'; }
  }
  // Toggle one emotion in/out — cells follow immediately.
  function toggleEmo(key) {
    const cur = new Set(outfit?.range || []);
    cur.has(key) ? cur.delete(key) : cur.add(key);
    return saveRange({ range: [...cur] });
  }
</script>

<svelte:window onkeydown={onKey} />

<div class="cat">
  <!-- OUTFITS — selection is outfit-by-outfit, at the top. Each outfit carries its OWN
       emotion set + sprites (the strip under the stage). -->
  <div class="bar">
    <span class="blbl">Outfits</span>
    {#each outfits as o, i (o.id)}
      <button class="chip ochip" class:on={i === outfitIdx} onclick={() => (outfitIdx = i)} title={o.concept || o.name}>
        {#if outfitSprite(o)}<img class="oimg" src={`${outfitSprite(o)}?b=${bust}`} alt="" />{/if}{o.name}
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

  <!-- Stage: 3D character coverflow on a studio backdrop -->
  <div class="stage studio">
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
        <!-- Plumbing only (bare): progress drives the divider bar, sprite events fill cells live. -->
        <GenStream jobId={emoJob} bare onProgress={(d, t) => (emoProg = { done: d, total: t })}
                   onEvent={onSprite} onError={(m) => (err = m)} onDone={onEmosDone} />
      {/if}
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
  </div>

  <!-- The selected outfit's EMOTION SET — its own sprites, one card per emotion, with
       per-cell render/re-roll and a render-all job. -->
  {#if charKey && outfit}
    <div class="emopane">
    <!-- Render progress rides the divider line between the stage and the emotion grid. -->
    {#if emoJob}
      <div class="progline" title={`${emoProg.done}/${emoProg.total} rendered`}>
        <div class="pfill" style={`width:${emoProg.total ? (emoProg.done / emoProg.total) * 100 : 0}%`}></div>
        <span class="pnum">{emoProg.done}/{emoProg.total || '…'}</span>
      </div>
    {/if}
    <div class="emohead">
      <b>🎭 {outfit.name}</b>
      <span class="hint">{shownEmos.filter((e) => e.url).length}/{shownEmos.length} rendered · click a card to put it on stage</span>
      <span class="sp"></span>
      <button class="plan" onclick={showLayers}
              title="Inspect the image card: every prompt layer that stacks into the selected sprite (style ← Overview, identity/outfit/emotion ← Cast) plus the final composed prompt">
        ≣ Layers</button>
      <button class="plan" onclick={recomposeRange} disabled={rangeBusy}
              title="Re-curate this outfit's emotion set for its register (v4pro reads the attire + persona)">
        {rangeBusy ? '🎭 Curating…' : '🎭 Recompose set'}</button>
      <button class="plan" class:on={editRange} onclick={() => (editRange = !editRange)}
              title="Add or remove individual emotions from this outfit's set">
        {editRange ? '✓ Done editing' : '✎ Edit set'}</button>
      <button class="plan" onclick={fixIdentity} disabled={identBusy}
              title="Backfill a clean, clothing-free canonical appearance for this character so every render keeps a consistent identity (fixes hair drift)">
        {identBusy ? '🧬 Fixing…' : identDone ? '✓ Identity fixed' : '🧬 Fix identity'}</button>
      <button class="plan" onclick={broadcastStyle} disabled={styleBusy || (!shownEmo && !outfitSprite(outfit))}
              title="Distill THIS image's art style (vision model) and broadcast it as the anchor every character's future renders open with">
        {styleBusy ? '⭐ Distilling…' : styleSet ? '✓ Style set for everyone' : '⭐ Set as cast style'}</button>
      <button class="plan" onclick={renderAllEmos} disabled={!!emoJob}
              title="Render this outfit's whole emotion set (streamed job)">{emoJob ? '✨ Rendering…' : '✨ Render all'}</button>
    </div>
    {#if editRange}
      <div class="rangeedit">
        <span class="hint">This outfit's emotion set — click to add / remove. Cells update live.</span>
        <div class="rchips">
          {#each poolEmos as e (e.emotion)}
            <button class="rchip" class:on={e.in_range} disabled={rangeBusy}
                    onclick={() => toggleEmo(e.emotion)} title={e.in_range ? 'In this set — click to remove' : 'Add to this set'}>
              {e.in_range ? '✓ ' : '＋ '}{e.label || e.emotion}
            </button>
          {/each}
        </div>
      </div>
    {/if}
    <div class="emostrip">
      {#each shownEmos as e (e.emotion)}
        {@const ck = `${outfit.id}:${e.emotion}`}
        <div class="ecard" class:missing={!e.url} class:on={selEmo === e.emotion}
             role="button" tabindex="0" onclick={() => { if (e.url) selEmo = e.emotion; }}
             onkeydown={(ev) => ev.key === 'Enter' && e.url && (selEmo = e.emotion)}
             title={e.url ? 'Show this emotion on stage' : 'Not rendered yet'}>
          {#if e.url}<img src={`${e.url}?b=${bust}`} alt={e.label || e.emotion} />{:else}<div class="eph">·</div>{/if}
          <div class="efoot">
            <span class="elabel">{e.label || e.emotion}</span>
            <button class="ebtn" disabled={!!cellBusy[ck]} onclick={(ev) => { ev.stopPropagation(); renderEmo(e.emotion, !!e.url); }}
                    title={e.url ? 'Re-roll this sprite (new seed)' : 'Render this sprite'}>{cellBusy[ck] ? '…' : (e.url ? '↻' : '🎨')}</button>
          </div>
        </div>
      {/each}
    </div>
    </div>
  {/if}
</div>

<!-- ≣ LAYERS — the image card as a readable stack: each layer tagged with the tab that owns it,
     flowing down into the final composed prompt + the face-detailer pass. -->
{#if stack}
  <div class="lay-back" role="button" tabindex="-1" onclick={() => (stack = null)} onkeydown={(e) => e.key === 'Escape' && (stack = null)}>
    <div class="lay-modal" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="lay-head">
        <b>≣ Image card — {charName} · {outfit?.name} · {stack.emotion}</b>
        <span class="sp"></span>
        <button class="lay-x" onclick={() => (stack = null)}>×</button>
      </div>
      {#if stack.loading}
        <div class="lay-loading"><span class="spin"></span> composing the final prompt…</div>
      {:else}
        <div class="lay-stack">
          {#each stack.layers as l, i (l.id)}
            {#if i > 0}<div class="lay-arrow">↓</div>{/if}
            <div class="lay-card" class:final={l.id === 'final'} class:face={l.id === 'face'}>
              <div class="lay-meta">
                <span class="lay-label">{l.label}</span>
                <span class="lay-src src-{l.source}">{l.source}</span>
              </div>
              <div class="lay-text">{l.text || '—'}</div>
            </div>
          {/each}
        </div>
      {/if}
    </div>
  </div>
{/if}

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

  /* Outfit rail (with sprite thumbs). */
  .ochip .oimg { width: 26px; height: 34px; border-radius: 5px; object-fit: cover; object-position: top; flex: none; }
  .hint { font-size: 12px; color: var(--faint); font-style: italic; }
  .plan { font-size: 12.5px; font-weight: 600; padding: 7px 13px; border-radius: 9px; background: none;
          border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .plan:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .sp { flex: 1; }

  /* The selected outfit's EMOTION pane — a slim header (name · rendered count · Render all)
     over TWO fixed rows of sprite cards (scrolls horizontally; fixed height so the stage never
     resizes). Clicking a card puts that sprite ON STAGE. */
  .emopane { border-top: 1px solid var(--border-soft); background: var(--panel); position: relative; }

  /* Render progress bar — sits on the divider line between the stage and the emotion grid. */
  .progline { position: relative; height: 5px; background: var(--elev-2, var(--elev));
              overflow: visible; }
  .pfill { height: 100%; background: var(--accent); transition: width .35s ease;
           box-shadow: 0 0 8px color-mix(in srgb, var(--accent) 60%, transparent); }
  .pnum { position: absolute; top: 6px; right: 12px; font-size: 10.5px; font-weight: 700;
          color: var(--accent); background: var(--panel); padding: 0 5px; border-radius: 4px; }
  .emohead { display: flex; align-items: center; gap: 10px; padding: 7px 14px 0;
             font-size: 12.5px; color: var(--text); }
  .emohead .plan { padding: 5px 12px; font-size: 12px; }
  .emohead .plan.on { background: color-mix(in srgb, var(--accent) 16%, transparent); border-style: solid; }

  /* Emotion-range editor — a wrap of toggle chips; ✓ = in this outfit's set, ＋ = add. */
  .rangeedit { padding: 4px 14px 8px; border-top: 1px dashed var(--border-soft); }
  .rchips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .rchip { font-size: 11.5px; padding: 4px 10px; border-radius: 999px; cursor: pointer;
           background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .rchip:hover:not(:disabled) { color: var(--text); border-color: var(--border); }
  .rchip.on { color: var(--text); border-color: var(--accent);
              background: color-mix(in srgb, var(--accent) 14%, var(--elev)); }
  .rchip:disabled { opacity: .55; cursor: default; }
  .emostrip { display: grid; grid-auto-flow: column; grid-template-rows: repeat(3, minmax(0, 1fr));
              gap: 8px; padding: 8px 14px 9px; height: 452px; box-sizing: border-box;
              overflow-x: auto; overflow-y: hidden; }
  .ecard { width: 108px; display: flex; flex-direction: column; border-radius: 10px; overflow: hidden;
           background: var(--elev); border: 1px solid var(--border-soft); cursor: pointer; outline: none; }
  .ecard.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
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

  /* ≣ Layers modal — the prompt stack, read top-to-bottom like a workflow. */
  .lay-back { position: fixed; inset: 0; z-index: 90; background: rgba(0,0,0,.55);
              display: grid; place-items: center; }
  .lay-modal { width: min(680px, 94vw); max-height: 86vh; display: flex; flex-direction: column;
               background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
               box-shadow: 0 18px 60px rgba(0,0,0,.5); overflow: hidden; }
  .lay-head { display: flex; align-items: center; gap: 10px; padding: 11px 16px; font-size: 13px;
              color: var(--text); border-bottom: 1px solid var(--border-soft);
              background: color-mix(in srgb, var(--accent) 8%, var(--panel)); }
  .lay-x { width: 24px; height: 24px; padding: 0; border-radius: 7px; background: none; border: none;
           color: var(--muted); font-size: 16px; cursor: pointer; }
  .lay-x:hover { color: var(--text); }
  .lay-loading { display: flex; align-items: center; gap: 10px; padding: 28px 18px;
                 font-size: 12.5px; color: var(--muted); }
  .lay-loading .spin { border-color: rgba(109,140,255,.3); border-top-color: var(--accent); }
  .lay-stack { overflow: auto; padding: 14px 18px 18px; display: flex; flex-direction: column; }
  .lay-arrow { text-align: center; color: var(--faint); font-size: 12px; line-height: 1.6; }
  .lay-card { border: 1px solid var(--border-soft); border-radius: 10px; background: var(--elev);
              padding: 8px 11px; }
  .lay-card.final { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--elev)); }
  .lay-card.face { border-style: dashed; }
  .lay-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .lay-label { font-size: 11.5px; font-weight: 700; color: var(--text); }
  .lay-src { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
             padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border-soft); color: var(--faint); }
  .lay-src.src-overview { color: var(--accent); border-color: var(--accent); }
  .lay-src.src-compose { color: #d68f5c; border-color: #d68f5c; }
  .lay-text { font-size: 11.5px; color: var(--muted); line-height: 1.55; white-space: pre-wrap;
              word-break: break-word; }

  /* The wardrobe trigger + its live job stream, floated on the stage */
  .bigcta { position: absolute; bottom: 56px; left: 50%; transform: translateX(-50%); z-index: 10;
            font-size: 14px; font-weight: 700; padding: 11px 22px; border-radius: 999px;
            background: var(--accent); color: #fff; border: none; cursor: pointer;
            box-shadow: 0 6px 24px rgba(0,0,0,.45); }
  .bigcta:hover:not(:disabled) { filter: brightness(1.1); }
  .wjob { position: absolute; top: 14px; left: 50%; transform: translateX(-50%); z-index: 11;
          width: min(560px, 92%); }
</style>

<script>
  import { goto } from '$app/navigation';
  import { get, post } from '$lib/api.js';
  import { startJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import GenStream from '$lib/components/GenStream.svelte';

  // cast: [{ character, name, role, primary, hasRef, height, desc, images:[url] }] · onChanged() reloads.
  let { storyKey, cast = [], onChanged = () => {} } = $props();
  const CANDIDATES = 5;
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);
  const baseKey = (k) => `dashbase:${k}`;

  // ---- carousel: pick a character; the strip shows the cast height-scaled (feet on one floor) ----
  let selIdx = $state(0);
  let cur = $derived(cast[selIdx] || null);
  const FALLBACK = 168;
  const hOf = (c) => Number(c?.height) || FALLBACK;
  let maxH = $derived(Math.max(172, ...cast.filter((c) => c.hasRef).map(hOf)));
  // Scale the WHOLE figure by its height ratio (transform, feet-anchored) — robust against the
  // width/max-width clamp that was collapsing every image to the same height.
  const figScale = (c) => Math.max(0.7, Math.min(1, hOf(c) / maxH)).toFixed(3);
  const refUrl = (c) => abs(`/api/characters/${c.character}/reference`);
  function ftin(cm) { const t = Math.round(cm / 2.54); return `${Math.floor(t / 12)}'${t % 12}"`; }
  // show PAGE characters at once (feet on one floor, height-scaled); arrows slide the window by one
  const PAGE = 4;
  let winStart = $state(0);
  let maxStart = $derived(Math.max(0, cast.length - PAGE));
  let visible = $derived(cast.map((c, i) => ({ c, i })).slice(winStart, winStart + PAGE));
  function slide(n) { winStart = Math.min(maxStart, Math.max(0, winStart + n)); }
  function onKey(e) {
    if (e.target?.tagName === 'TEXTAREA' || e.target?.tagName === 'INPUT') return;
    if (e.key === 'ArrowLeft') slide(-1); else if (e.key === 'ArrowRight') slide(1);
  }
  $effect(() => { if (selIdx >= cast.length) selIdx = Math.max(0, cast.length - 1); });
  $effect(() => { if (winStart > maxStart) winStart = maxStart; });

  // ---- outfits for the selected character (lazy) — the "outfit carousel" ----
  let outfits = $state({});   // char key -> [{ id, name, thumb }]
  async function loadOutfits(key) {
    if (!key || outfits[key]) return;
    outfits[key] = [];
    try {
      const p = await get(`/characters/${key}/portraits`);
      outfits[key] = (p?.outfits || []).map((o) => ({
        id: o.id, name: o.name,
        thumb: o.expressions ? Object.values(o.expressions)[0] : (o.image || null),
      }));
    } catch { outfits[key] = []; }
  }
  $effect(() => { if (cur) loadOutfits(cur.character); });
  const openCharacter = (k) => goto(`/stories/${storyKey}/cast?c=${k}`);

  // ---- base-image style source (default OFF = clean txt2img, no pose copied) ----
  // When ON, render via the style flow seeded from a chosen image. NOTE: img2img-style copies the
  // source's POSE; only an IPAdapter style workflow transfers look without composition.
  let primaryKey = $derived(cast.find((c) => c.primary)?.character || null);
  let primaryHasRef = $derived(!!cast.find((c) => c.character === primaryKey)?.hasRef);
  let styleSources = $derived(cur ? [
    ...(primaryKey && primaryKey !== cur.character && primaryHasRef
        ? [{ id: 'primary', label: 'Primary style', thumb: abs(`/api/characters/${primaryKey}/reference`),
             body: { style_from: primaryKey } }] : []),
    ...((cur.images || []).map((u, i) => ({ id: 'img' + i, label: 'Card art', thumb: abs(u),
                                            body: { style_url: abs(u) } }))),
  ] : []);
  let useStyle = $state(false);
  let stylePick = $state(null);
  $effect(() => { cur; useStyle = false; stylePick = null; });                 // reset per character
  $effect(() => { if (useStyle && !stylePick && styleSources.length) stylePick = styleSources[0].id; });
  const styleBody = () => (useStyle ? styleSources.find((s) => s.id === stylePick)?.body : null);

  // ---- whole-cast jobs (one GenStream at a time) ----------------------------
  let bulkJob = $state(null);
  let bulkTitle = $state('');
  let bulkErr = $state(null);

  async function regenCast() {
    if (!await askConfirm({
      title: 'Regenerate the whole cast?',
      message: 'Deletes every story-bound character and their portraits, then re-derives the cast (protagonist + supporting NPCs) from the storyboard. The imported source card is kept. This cannot be undone.',
      confirmLabel: 'Regenerate cast', danger: true })) return;
    await startBulk('Regenerating cast', `/stories/${storyKey}/regenerate-cast`, {});
  }
  async function planAll() {
    await startBulk('Planning all wardrobes', `/stories/${storyKey}/plan-wardrobe-all`, {});
  }
  let bulkFailed = false;
  async function startBulk(title, url, body) {
    bulkErr = null; bulkJob = null; bulkTitle = title; bulkFailed = false;
    const r = await post(url, body);
    if (r.ok && r.data?.job) bulkJob = r.data.job;
    else { bulkErr = r.data?.error || 'could not start (is the backend restarted?)'; bulkTitle = ''; }
  }
  async function onBulkDone() { bulkJob = null; bulkTitle = ''; if (!bulkFailed) await onChanged(); }

  // ---- bulk base-image candidates (review — never auto-saved) ----------------
  let genningAll = $state(false);
  async function genBase(charKey, body = null) {
    const k = baseKey(charKey); rensure(k).cands = []; rensure(k).busy = true; rensure(k).err = null;
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    const job = startJob('Base image render', nm, `stories/${storyKey}/cast`, CANDIDATES);
    for (let i = 0; i < CANDIDATES; i++) {
      const r = await post(`/characters/${charKey}/base-candidate`, body || {});
      if (r.ok && r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { rensure(k).err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    rensure(k).busy = false;
  }
  async function genBaseAll() {
    genningAll = true;
    const seen = new Set();
    for (const c of cast) {                             // sequential — one GPU; dedupe keys
      if (seen.has(c.character)) continue;
      seen.add(c.character);
      await genBase(c.character);
    }
    genningAll = false;
  }
  async function pickBase(charKey, dataUri) {
    const k = baseKey(charKey); rensure(k).busy = true;
    const r = await post(`/characters/${charKey}/reference/from-data`, { data: dataUri });
    rensure(k).busy = false;
    if (r.data?.ok) { rensure(k).cands = []; await onChanged(); }
    else rensure(k).err = r.data?.error || 'could not set base';
  }

  // ---- per-character full regenerate (persona+role+appearance, base image, wardrobe) --------
  let openRegen = $state('');
  let regenText = $state('');
  let regenJob = $state(null);
  let regenFor = $state('');
  let regenErr = $state(null);

  function toggleRegen(charKey) { openRegen = openRegen === charKey ? '' : charKey; regenText = ''; regenErr = null; }
  async function runRegen(charKey) {
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    if (!await askConfirm({
      title: `Regenerate ${nm}?`,
      message: 'Rewrites this character’s description, role and appearance, re-renders their base image, and rebuilds their wardrobe (clearing the current outfits + sprites). This cannot be undone.',
      confirmLabel: 'Regenerate', danger: true })) return;
    regenErr = null; regenJob = null; regenFor = charKey; regenFailed = false;
    const r = await post(`/stories/${storyKey}/regenerate-character`, { character: charKey, instruction: regenText.trim() });
    if (r.ok && r.data?.job) { regenJob = r.data.job; openRegen = ''; }
    else { regenErr = r.data?.error || 'could not start (is the backend restarted?)'; regenFor = ''; }
  }
  let regenFailed = false;
  function onRegenError(charKey, msg) { regenErr = `${cast.find((c) => c.character === charKey)?.name || charKey}: ${msg}`; regenFailed = true; }
  async function onRegenDone() { regenJob = null; regenFor = ''; if (!regenFailed) await onChanged(); }
</script>

<svelte:window onkeydown={onKey} />

<div class="dash">
  <div class="dhead">
    <h4>Cast &amp; wardrobe <span class="lo">— flip through the cast; open a character to fine-tune</span></h4>
    <div class="bulk">
      <button class="ghost sm dgr" onclick={regenCast} disabled={!!bulkJob}>↻ Regenerate whole cast</button>
      <button class="ghost sm" onclick={genBaseAll} disabled={genningAll || !!bulkJob}>{genningAll ? 'Generating…' : '🎨 Base images for all'}</button>
      <button class="ghost sm" onclick={planAll} disabled={!!bulkJob}>✨ Plan outfits for all</button>
    </div>
  </div>
  {#if bulkErr}<div class="err">⚠ {bulkErr}</div>{/if}
  {#if bulkJob}<GenStream jobId={bulkJob} title={bulkTitle} onError={(m) => { bulkErr = m; bulkFailed = true; }} onDone={onBulkDone} />{/if}
  {#if regenErr}<div class="err">⚠ {regenErr}</div>{/if}

  {#if !cast.length}
    <div class="empty">No cast yet — regenerate the cast to populate it.</div>
  {:else}
    <!-- height-scaled character carousel: arrows + click to select -->
    <div class="carousel">
      <button class="nav" onclick={() => slide(-1)} disabled={winStart === 0} aria-label="Previous">‹</button>
      <div class="strip">
        {#each visible as v (v.c.character)}
          <button class="fig" class:sel={v.i === selIdx} class:primary={v.c.primary}
                  onclick={() => (selIdx = v.i)} title={v.c.name}>
            <div class="chart">
              {#if v.c.hasRef}
                <img src={refUrl(v.c)} alt={v.c.name} style="transform:scale({figScale(v.c)})" />
              {:else}
                <div class="noimg" style="transform:scale({figScale(v.c)})">no base</div>
              {/if}
            </div>
            <span class="cap"><span class="nm">{v.c.name}{#if v.c.primary}<span class="lead">★</span>{/if}</span>
              <span class="cm">{Number(v.c.height) ? `${v.c.height} cm · ${ftin(v.c.height)}` : '—'}</span></span>
          </button>
        {/each}
      </div>
      <button class="nav" onclick={() => slide(1)} disabled={winStart >= maxStart} aria-label="Next">›</button>
      {#if cast.length > PAGE}<div class="pageind">{winStart + 1}–{Math.min(winStart + PAGE, cast.length)} of {cast.length}</div>{/if}
    </div>

    <!-- selected character — description + outfits + every grid-card action -->
    {#if cur}
      <div class="detail" class:primary={cur.primary}>
        <button class="hero" onclick={() => openCharacter(cur.character)} title="Open {cur.name}">
          {#if cur.hasRef}<img src={refUrl(cur)} alt={cur.name} />{:else}<div class="noimg big">no base image</div>{/if}
        </button>

        <div class="info">
          <div class="nm big">{cur.name}{#if cur.primary}<span class="lead">★</span>{/if}
            {#if Number(cur.height)}<span class="h">{cur.height} cm · {ftin(cur.height)}</span>{/if}</div>
          {#if cur.role}<div class="role">{cur.role}</div>{/if}
          {#if cur.desc}<p class="desc">{cur.desc}</p>{/if}

          <!-- outfit carousel -->
          <div class="outfits">
            {#if (outfits[cur.character] || []).length}
              {#each outfits[cur.character] as o (o.id)}
                <button class="outfit" onclick={() => openCharacter(cur.character)} title={o.name}>
                  {#if o.thumb}<img src={abs(o.thumb)} alt={o.name} />{:else}<div class="ono">{o.name}</div>{/if}
                  <span>{o.name}</span>
                </button>
              {/each}
            {:else}
              <span class="noout">no outfits yet — open to add them</span>
            {/if}
          </div>

          <div class="acts">
            <button class="ghost sm" onclick={() => openCharacter(cur.character)}>Open wardrobe →</button>
            <button class="ghost sm" onclick={() => genBase(cur.character, styleBody())} disabled={rget(baseKey(cur.character)).busy || genningAll}>
              {rget(baseKey(cur.character)).busy ? `Rendering ${CANDIDATES}…` : `🎨 Base images (${CANDIDATES})`}</button>
            <button class="ghost sm" onclick={() => toggleRegen(cur.character)}>↻ Regenerate</button>
          </div>

          <!-- base-image source: default txt2img (clean forward pose); optionally match a style image -->
          <div class="stylectl">
            <label class="sw"><input type="checkbox" bind:checked={useStyle} />
              Match art style from an image <span class="lo">(off = txt2img, clean pose)</span></label>
            {#if useStyle}
              {#if styleSources.length}
                <div class="srcs">
                  {#each styleSources as s (s.id)}
                    <button class="src" class:on={stylePick === s.id} onclick={() => (stylePick = s.id)} title={s.label}>
                      <img src={s.thumb} alt={s.label} /><span>{s.label}</span>
                    </button>
                  {/each}
                </div>
                <div class="hint">⚠ The configured style flow (<code>sdxl_img2img</code>) re-noises this image, so it tends to copy its POSE. For style without the pose, point the <em>style</em> role at an IPAdapter workflow (e.g. <code>illustrious_style</code>) in Images → Roles.</div>
              {:else}
                <div class="hint">No source images available for {cur.name}.</div>
              {/if}
            {/if}
          </div>

          {#if openRegen === cur.character}
            <div class="regenbox">
              <textarea class="fld" rows="2" bind:value={regenText}
                placeholder="what to change? (optional — e.g. 'make her older, a rival not a friend'; blank = faithful refresh)"></textarea>
              <button class="go" onclick={() => runRegen(cur.character)}>Regenerate {cur.name}</button>
            </div>
          {/if}
          {#if regenFor === cur.character && regenJob}
            <GenStream jobId={regenJob} title="Regenerating {cur.name}" onError={(m) => onRegenError(cur.character, m)} onDone={onRegenDone} />
          {/if}

          {#if rget(baseKey(cur.character)).err}<div class="err sm">⚠ {rget(baseKey(cur.character)).err}</div>{/if}
          {#if rget(baseKey(cur.character)).cands?.length}
            <div class="cands">
              {#each rget(baseKey(cur.character)).cands as img, i (i)}
                <div class="cand">
                  <ZoomImage src={img} caption={`${cur.name} candidate ${i + 1}`} inline />
                  <button class="use" onclick={() => pickBase(cur.character, img)} disabled={rget(baseKey(cur.character)).busy}>Use this</button>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {/if}
  {/if}
</div>

<style>
  .dash { display: flex; flex-direction: column; gap: 12px; }
  .dhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h4 { margin: 4px 0; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .bulk { display: flex; gap: 6px; flex-wrap: wrap; }
  .dgr { color: var(--bad); border-color: rgba(255,122,122,.4); }
  .dgr:hover:not(:disabled) { background: rgba(255,122,122,.12); color: var(--bad); filter: none; }
  .err { font-size: 12.5px; color: var(--bad); } .err.sm { margin-top: 6px; }
  .empty { font-size: 13px; color: var(--muted); padding: 24px; text-align: center; }

  /* ---- carousel strip (height-scaled, feet on one floor) ---- */
  .carousel { display: flex; flex-wrap: wrap; align-items: stretch; gap: 6px; background: var(--elev); border: 1px solid var(--border);
              border-radius: 12px; padding: 10px 6px; }
  .pageind { flex-basis: 100%; text-align: center; font-size: 10.5px; color: var(--faint); margin-top: 2px; }
  .nav { flex: 0 0 auto; width: 34px; align-self: center; font-size: 22px; line-height: 1; padding: 10px 0;
         border-radius: 9px; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); box-shadow: none; }
  .nav:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); filter: none; }
  .nav:disabled { opacity: .35; }
  .strip { flex: 1; min-width: 0; display: flex; align-items: flex-end; justify-content: center; gap: 10px; padding: 0 6px 2px; }
  .fig { flex: 1 1 0; min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 6px 8px 4px;
         background: none; border: 1px solid transparent; border-radius: 10px; cursor: pointer; box-shadow: none; }
  .fig:hover { background: var(--elev-2); filter: none; }
  .fig.sel { background: var(--panel); border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .chart { width: 100%; height: 210px; display: flex; align-items: flex-end; justify-content: center; border-bottom: 2px solid var(--border); overflow: hidden; }
  .fig.sel .chart { border-bottom-color: var(--accent); }
  /* uniform base size; per-character height comes from the inline transform:scale (origin bottom = feet on the floor) */
  .chart img { height: 96%; width: auto; max-width: 100%; object-fit: contain; object-position: bottom;
               transform-origin: bottom center; filter: drop-shadow(0 4px 12px rgba(0,0,0,.45)); }
  .chart .noimg { width: 70px; height: 90%; display: grid; place-items: center; font-size: 10.5px; color: var(--faint);
                  border: 1px dashed var(--border); border-radius: 8px; transform-origin: bottom center; }
  .cap { text-align: center; line-height: 1.25; }
  .cap .nm { display: block; font-size: 12px; font-weight: 600; color: var(--text); }
  .cap .cm { display: block; font-size: 10.5px; color: var(--accent); font-variant-numeric: tabular-nums; }
  .lead { color: var(--accent); font-size: 10px; margin-left: 3px; }

  /* ---- detail panel ---- */
  .detail { display: grid; grid-template-columns: minmax(180px, 280px) 1fr; gap: 16px;
            background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px; }
  .detail.primary { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .hero { padding: 0; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--bg);
          aspect-ratio: 3/4; box-shadow: none; cursor: pointer; }
  .hero img { width: 100%; height: 100%; object-fit: contain; display: block; }
  .hero:hover { border-color: var(--accent); filter: none; }
  .noimg.big { width: 100%; height: 100%; display: grid; place-items: center; font-size: 12px; color: var(--faint); }
  .info { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .nm.big { font-size: 17px; font-weight: 700; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .nm.big .h { font-size: 11.5px; font-weight: 500; color: var(--accent); }
  .role { font-size: 12px; color: var(--muted); }
  .desc { margin: 0; font-size: 12.5px; line-height: 1.5; color: var(--text); max-height: 7.5em; overflow: auto; white-space: pre-wrap; }

  .outfits { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; }
  .outfit { flex: 0 0 auto; width: 66px; display: flex; flex-direction: column; gap: 3px; padding: 0; background: none;
            border: 0; cursor: pointer; box-shadow: none; }
  .outfit img { width: 66px; height: 88px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); display: block; }
  .outfit:hover img { border-color: var(--accent); }
  .outfit .ono { width: 66px; height: 88px; display: grid; place-items: center; font-size: 9.5px; color: var(--faint);
                 border: 1px dashed var(--border); border-radius: 8px; }
  .outfit span { font-size: 10px; color: var(--muted); text-align: center; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .noout { font-size: 11.5px; color: var(--faint); }

  .acts { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 2px; }
  .stylectl { display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; border: 1px dashed var(--border);
              border-radius: 9px; background: var(--bg); }
  .sw { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text); cursor: pointer; }
  .sw input { width: auto; }
  .sw .lo { color: var(--faint); }
  .srcs { display: flex; gap: 8px; flex-wrap: wrap; }
  .src { padding: 0; display: flex; flex-direction: column; gap: 3px; background: none; border: 0; cursor: pointer; box-shadow: none; }
  .src img { width: 56px; height: 74px; object-fit: cover; border-radius: 7px; border: 2px solid var(--border); display: block; }
  .src.on img { border-color: var(--accent); }
  .src span { font-size: 10px; color: var(--muted); text-align: center; }
  .hint { font-size: 11px; color: var(--faint); line-height: 1.45; }
  .hint code { font-family: ui-monospace, monospace; font-size: 10.5px; color: var(--muted); }
  .regenbox { display: flex; flex-direction: column; gap: 6px; }
  .fld { width: 100%; padding: 7px 9px; font-size: 12.5px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); resize: vertical; line-height: 1.45; font-family: inherit; }
  .fld:focus { border-color: var(--accent); outline: none; }
  .go { font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 8px; background: var(--accent); color: #fff; border: 0; align-self: flex-start; }
  .go:hover { filter: brightness(1.08); }

  .cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(90px, 1fr)); gap: 6px; }
  .cand { display: flex; flex-direction: column; gap: 4px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 7px; }
  .use { font-size: 10.5px; padding: 3px 5px; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); }
  .use:hover { border-color: var(--accent); color: var(--accent); filter: none; }

  @media (max-width: 640px) { .detail { grid-template-columns: 1fr; } }
</style>

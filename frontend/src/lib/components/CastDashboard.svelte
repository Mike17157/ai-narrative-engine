<script>
  import { get, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import GenStream from '$lib/components/GenStream.svelte';
  import Sprites from '$lib/components/Sprites.svelte';
  import AffectScatter from '$lib/components/AffectScatter.svelte';
  import TagInput from '$lib/components/TagInput.svelte';
  import RegenModal from '$lib/components/RegenModal.svelte';

  // cast: [{ character, name, role, primary, hasRef, height, desc, images:[url] }] · onChanged() reloads.
  // ONE surface: flip the carousel to switch character, investigate properties inline (tabs), and
  // run all (re)generation through the gated RegenModal. (The old ?c= Portrait Studio is retired.)
  let { storyKey, cast = [], selectKey = '', onChanged = () => {} } = $props();
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);

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
  // Follow a ?c=<key> deep-link from the side menu: select that member once (manual clicks still win).
  let lastSel = '';
  $effect(() => {
    const k = selectKey; if (!k || k === lastSel) return;
    const i = cast.findIndex((c) => c.character === k);
    if (i >= 0) { selIdx = i; if (i < winStart || i >= winStart + PAGE) winStart = Math.min(maxStart, i); lastSel = k; }
  });

  // ---- inline "investigate" tabs for the selected character ----------------------------------
  // The Outfits & expressions grid no longer lives in a tab — it outgrew the narrow detail panel and
  // now renders full-width BELOW it (see the grid section at the bottom of the template).
  const TABS = [{ id: 'desc', label: 'Description' }, { id: 'base', label: 'Base image' }];
  let tab = $state('desc');
  let spriteBust = $state(0);   // force the inspect grid + reference image to reload after a regen

  // Per-character editable text (name / persona / base prompt) — autosaved (debounced). Never create
  // entries during render: cs() only READS; ensure() (handlers/effects) creates lazily.
  const EMPTY = { name: '', desc: '', prompt: null, nameTouched: false, descTouched: false, promptLoaded: false };
  let edits = $state({});
  function ensure(k) { if (!edits[k]) edits[k] = { ...EMPTY }; return edits[k]; }
  function cs(k) { return edits[k] ?? EMPTY; }

  // Seed name + description from the card (reactively, until the user edits them) and lazily fetch
  // the exact base-image prompt — for the selected character.
  $effect(() => {
    const c = cur; if (!c) return;
    const s = ensure(c.character);
    if (!s.nameTouched && s.name !== (c.name || '')) s.name = c.name || '';
    if (!s.descTouched && s.desc !== (c.desc || '')) s.desc = c.desc || '';
    if (!s.promptLoaded) {
      s.promptLoaded = true;
      get(`/characters/${c.character}/base-prompt`).then((d) => {
        if (d && edits[c.character] && edits[c.character].prompt == null) edits[c.character].prompt = d.prompt || '';
      });
    }
  });

  const SAVE_DELAY = 700;
  const timers = {}, snaps = {};
  const debounce = (k, fn) => { clearTimeout(timers[k]); timers[k] = setTimeout(fn, SAVE_DELAY); };
  async function saveName(k) { await post(`/characters/${k}/card`, { name: (cs(k).name || '').trim() }); }
  async function saveDesc(k) { await post(`/characters/${k}/card`, { system: cs(k).desc || '' }); }
  async function savePrompt(k) { await post(`/characters/${k}/card`, { fields: { base_prompt: (cs(k).prompt || '').trim() } }); }

  $effect(() => {
    for (const key of Object.keys(edits)) {
      const s = edits[key];
      { const k = `${key}:name`; if (snaps[k] === undefined) snaps[k] = s.name;
        else if (snaps[k] !== s.name) { if (s.nameTouched) debounce(k, () => saveName(key)); snaps[k] = s.name; } }
      { const k = `${key}:desc`; if (snaps[k] === undefined) snaps[k] = s.desc;
        else if (snaps[k] !== s.desc) { if (s.descTouched) debounce(k, () => saveDesc(key)); snaps[k] = s.desc; } }
      if (s.prompt != null) { const k = `${key}:prompt`; if (snaps[k] === undefined) snaps[k] = s.prompt;
        else if (snaps[k] !== s.prompt) { debounce(k, () => savePrompt(key)); snaps[k] = s.prompt; } }
    }
  });

  // ---- modals -----------------------------------------------------------------
  let modalOpen = $state(false);
  async function onModalChanged() {
    const k = cur?.character;
    if (k && edits[k]) { edits[k].promptLoaded = false; edits[k].prompt = null; }  // re-fetch the rewritten prompt
    spriteBust++;
    await onChanged();
  }

  // ---- whole-cast bulk jobs (one GenStream at a time) ----------------------------------------
  let bulkJob = $state(null);
  let bulkTitle = $state('');
  let bulkErr = $state(null);
  let bulkFailed = false;
  async function regenCast() {
    if (!await askConfirm({
      title: 'Regenerate the whole cast?',
      message: 'Deletes every story-bound character and their portraits, then re-derives the cast (protagonist + supporting NPCs) from the storyboard. The imported source card is kept. This cannot be undone.',
      confirmLabel: 'Regenerate cast', danger: true })) return;
    await startBulk('Regenerating cast', `/stories/${storyKey}/regenerate-cast`, {});
  }
  async function planAll() { await startBulk('Planning all wardrobes', `/stories/${storyKey}/plan-wardrobe-all`, {}); }
  async function startBulk(title, url, body) {
    bulkErr = null; bulkJob = null; bulkTitle = title; bulkFailed = false;
    const r = await post(url, body);
    if (r.ok && r.data?.job) bulkJob = r.data.job;
    else { bulkErr = r.data?.error || 'could not start (is the backend restarted?)'; bulkTitle = ''; }
  }
  let triggerGen = $state(0);
  async function onBulkDone() { bulkJob = null; bulkTitle = ''; if (!bulkFailed) { spriteBust++; triggerGen++; await onChanged(); } }
</script>

<svelte:window onkeydown={onKey} />

<div class="dash">
  <div class="dhead">
    <h4>Cast &amp; wardrobe <span class="lo">— flip through the cast; investigate &amp; regenerate inline</span></h4>
    <div class="bulk">
      <button class="ghost sm dgr" onclick={regenCast} disabled={!!bulkJob}>↻ Regenerate whole cast</button>
      <button class="ghost sm" onclick={planAll} disabled={!!bulkJob}>✨ Plan outfits for all</button>
    </div>
  </div>
  {#if bulkErr}<div class="err">⚠ {bulkErr}</div>{/if}
  {#if bulkJob}<GenStream jobId={bulkJob} title={bulkTitle} onError={(m) => { bulkErr = m; bulkFailed = true; }} onDone={onBulkDone} />{/if}

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

    <!-- selected character — investigate tabs + one Regenerate entry point -->
    {#if cur}
      {@const s = cs(cur.character)}
      <div class="detail" class:primary={cur.primary}>
        <div class="hero-col">
          <div class="hero">
            {#if cur.hasRef}
              <ZoomImage src={refUrl(cur) + `?b=${spriteBust}`} caption={`${cur.name} — base image`} inline />
            {:else}<div class="noimg big">no base image</div>{/if}
          </div>
        </div>

        <div class="info">
          <div class="topline">
            <div class="nm big">{s.name || cur.name}{#if cur.primary}<span class="lead">★</span>{/if}
              {#if Number(cur.height)}<span class="h">{cur.height} cm · {ftin(cur.height)}</span>{/if}
              {#if cur.role}<span class="role">· {cur.role}</span>{/if}</div>
            <button class="regen" onclick={() => (modalOpen = true)}>↻ Regenerate…</button>
          </div>

          <div class="tabs">
            {#each TABS as t (t.id)}
              <button class="tab" class:on={tab === t.id} onclick={() => (tab = t.id)}>{t.label}</button>
            {/each}
          </div>

          {#if tab === 'desc'}
            <label class="fl">Name</label>
            <input class="fld" value={s.name}
              oninput={(e) => { const x = ensure(cur.character); x.name = e.currentTarget.value; x.nameTouched = true; }} />
            <label class="fl">Description <span class="lo">— the persona sent to the chat model</span></label>
            <textarea class="fld ta" use:autosize={s.desc} placeholder="character description / persona…"
              value={s.desc} oninput={(e) => { const x = ensure(cur.character); x.desc = e.currentTarget.value; x.descTouched = true; }}></textarea>
            <p class="hint lo">To rewrite this from a steer (and cascade to the images), use <b>↻ Regenerate…</b>.</p>

          {:else if tab === 'base'}
            {#if cur.hasRef}
              <div class="curbase"><ZoomImage src={refUrl(cur) + `?b=${spriteBust}`} caption={`${cur.name} — base image`} inline /></div>
            {:else}<p class="hint lo">No base image yet — use <b>↻ Regenerate… → Base image</b> to render one.</p>{/if}
            <label class="fl">Image prompt <span class="lo">— exactly what's sent to the image model</span></label>
            <TagInput value={s.prompt ?? ''} kind="appearance"
              placeholder={s.prompt == null ? 'loading prompt…' : 'type a booru tag…'}
              onchange={(v) => ensure(cur.character).prompt = v} />
          {/if}
        </div>
      </div>

    {/if}

    <!-- Inline wardrobe: always visible below the detail panel for the selected character -->
    <div class="wardrobe">
      <div class="w-hdr">
        <span class="w-hdr-title">Outfits &amp; expressions — {cs(cur.character).name || cur.name}</span>
        <AffectScatter charKey={cur.character} refresh={spriteBust} compact />
      </div>
      <Sprites charKey={cur.character} charName={cur.name} hasRef={cur.hasRef} mode="wardrobe"
        refresh={spriteBust} {triggerGen} screen={`stories/${storyKey}/cast`} />
    </div>
  {/if}
</div>

{#if modalOpen && cur}
  <RegenModal storyKey={storyKey} character={cur.character} name={cs(cur.character).name || cur.name} hasRef={cur.hasRef}
    onChanged={onModalChanged} onClose={() => (modalOpen = false)} />
{/if}

<style>
  .dash { display: flex; flex-direction: column; gap: 12px; }
  .dhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h4 { margin: 4px 0; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .bulk { display: flex; gap: 6px; flex-wrap: wrap; }
  .dgr { color: var(--bad); border-color: rgba(255,122,122,.4); }
  .dgr:hover:not(:disabled) { background: rgba(255,122,122,.12); color: var(--bad); filter: none; }
  .err { font-size: 12.5px; color: var(--bad); }
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
  .chart img { height: 96%; width: auto; max-width: 100%; object-fit: contain; object-position: bottom;
               transform-origin: bottom center; filter: drop-shadow(0 4px 12px rgba(0,0,0,.45)); }
  .chart .noimg { width: 70px; height: 90%; display: grid; place-items: center; font-size: 10.5px; color: var(--faint);
                  border: 1px dashed var(--border); border-radius: 8px; transform-origin: bottom center; }
  .cap { text-align: center; line-height: 1.25; }
  .cap .nm { display: block; font-size: 12px; font-weight: 600; color: var(--text); }
  .cap .cm { display: block; font-size: 10.5px; color: var(--accent); font-variant-numeric: tabular-nums; }
  .lead { color: var(--accent); font-size: 10px; margin-left: 3px; }

  /* ---- detail panel ---- */
  .detail { display: grid; grid-template-columns: minmax(180px, 260px) 1fr; gap: 16px;
            background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px; }
  .detail.primary { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .hero-col { display: flex; flex-direction: column; gap: 8px; align-self: start; }
  .hero { border: 1px solid var(--border); border-radius: 10px; overflow: hidden; background: var(--bg);
          aspect-ratio: 3/4; display: grid; place-items: center; }
  .hero :global(.zoom-inline), .hero :global(img) { border-radius: 10px; max-height: 100%; }
  .noimg.big { width: 100%; height: 100%; display: grid; place-items: center; font-size: 12px; color: var(--faint); }
  .info { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
  .topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .nm.big { font-size: 17px; font-weight: 700; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .nm.big .h { font-size: 11.5px; font-weight: 500; color: var(--accent); }
  .nm.big .role { font-size: 12px; font-weight: 500; color: var(--muted); }
  .regen { font-size: 12.5px; font-weight: 600; padding: 7px 13px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; flex: none; }
  .regen:hover { filter: brightness(1.08); }

  .tabs { display: flex; gap: 2px; margin: 6px 0 8px; border-bottom: 1px solid var(--border); }
  .tab { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 12.5px; font-weight: 600;
         padding: 7px 12px; border-radius: 8px 8px 0 0; border-bottom: 2px solid transparent; cursor: pointer; }
  .tab:hover { color: var(--text); background: var(--elev); filter: none; }
  .tab.on { color: #fff; border-bottom-color: var(--accent); }

  .fl { display: block; font-size: 11px; color: var(--muted); margin: 8px 0 4px; text-transform: uppercase; letter-spacing: .3px; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border);
         color: var(--text); resize: none; line-height: 1.5; font-family: inherit; }
  .fld:focus { border-color: var(--accent); outline: none; }
  .ta { min-height: 38px; overflow: hidden; }
  .curbase { width: 170px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border-soft); background: var(--bg); }
  .curbase :global(img), .curbase :global(.zoom-inline) { border-radius: 10px; }
  .hint { margin: 8px 0 0; font-size: 11.5px; }

  /* ---- inline wardrobe ---- */
  .wardrobe { display: flex; flex-direction: column; min-height: 520px; height: 64vh;
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px;
    overflow: hidden; }
  .w-hdr { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 10px 14px;
    border-bottom: 1px solid var(--border-soft); flex-shrink: 0; background: var(--bg); }
  .w-hdr-title { font-size: 13px; font-weight: 680; color: var(--text); flex: 1; }
  .wardrobe :global(.wv) { flex: 1; min-height: 0; }

  @media (max-width: 640px) { .detail { grid-template-columns: 1fr; } }
</style>

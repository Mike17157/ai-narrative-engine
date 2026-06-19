<script>
  import { get, post } from '$lib/api.js';
  import { app, startJob, limitedPost } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import ImgCard from '$lib/components/ImgCard.svelte';
  import GenStream from '$lib/components/GenStream.svelte';

  // The 2-D wardrobe grid: outfits on Y (rows — the consistent flat list), emotions on X (columns —
  // the character's personality-rooted affect.range, V-A ordered). Each cell = one outfit×emotion
  // sprite. The grid scrolls in BOTH directions; a sticky emotion header row + a sticky outfit
  // column stay aligned as you scroll. txt2img — identity comes from the appearance tags.
  //   mode='inspect'  → read-only: zoom any cell; no generation controls (the CastDashboard pane).
  //   mode='generate' → full controls: render outfit images + the emotion range, re-roll cells (the
  //                     RegenModal). charKey · charName.
  let { charKey, charName, hasRef = true, refresh = 0, triggerGen = 0, mode = 'generate',
        screen = 'characters/selected' } = $props();
  const CANDIDATES = 3;   // per-cell redo always generates three
  const gen      = $derived(mode === 'generate');
  const wardrobe = $derived(mode === 'wardrobe');

  // wardrobe mode: which outfit row is selected
  let selOutfit = $state(0);
  // Fixed cell geometry — uniform sizes are what keep the sticky header row + sticky outfit column
  // pixel-aligned with the body as the grid scrolls in 2-D (variable sizes would desync rows/columns).
  const CELL = 104;       // emotion cell width
  const OUTFIT_COL = 168; // sticky outfit-label column width (full-body thumb + name + controls)

  let data = $state(null);     // { affect:{range}, outfits:[{id,name,base,expression_set:[{emotion,label,prompt,url,valence,arousal,in_range}]}] }
  let bust = $state(0);
  let err = $state(null);
  // Show ONLY the character's personality-rooted emotion range (affect.range) on the X axis by
  // default; flip to see/render the full 32-key taxonomy (for an out-of-range emotion you want a
  // sprite for). Outfits stay the consistent flat list on Y.
  let showAll = $state(false);

  async function load() { selOutfit = 0; try { data = await get(`/characters/${charKey}/portraits`); } catch { data = null; } }
  $effect(() => { charKey; refresh; load(); });

  // In inspect mode, watch for renders launched by the RegenModal's generate-mode Sprites for this
  // character. Jobs are tagged by label (charName or "charName — emotion") and kind.
  const RENDER_KINDS = new Set(['Sprite render', 'Outfit image render']);
  let activeRenders = $derived(
    !gen ? (app.localJobs || []).filter((j) =>
      j.status === 'running' && RENDER_KINDS.has(j.kind) &&
      (j.label === charName || j.label.startsWith(charName + ' — '))
    ) : []
  );
  let renderDone  = $derived(activeRenders.reduce((s, j) => s + j.done, 0));
  let renderTotal = $derived(activeRenders.reduce((s, j) => s + j.total, 0));
  // When the last active render for this character finishes, reload so the inspect grid picks up
  // newly saved sprites without waiting for the modal to close.
  let _prevActive = 0;
  $effect(() => {
    const n = activeRenders.length;
    if (_prevActive > 0 && n === 0) { load(); bust++; }
    _prevActive = n;
  });

  // The shared X axis (columns): the character's personality-rooted emotion range (a curated subset
  // of the 32 keys, V-A ordered). Hoisted out of the per-outfit loop — every outfit row shares the
  // SAME columns, which is what makes this a 2-D grid instead of per-outfit strips. When showAll,
  // the full taxonomy fills in (de-emphasized where out of range) so any cell can be rendered.
  let columns = $derived.by(() => {
    const range = data?.affect?.range || [];
    const ref = data?.outfits?.[0]?.expression_set || [];
    if (showAll) {
      const inSet = new Set(range.map((r) => r.emotion));
      return ref.map((e) => ({ emotion: e.emotion, label: e.label,
                               valence: e.valence, arousal: e.arousal, in_range: inSet.has(e.emotion) }));
    }
    return range.map((r) => ({ emotion: r.emotion, label: r.label || r.emotion,
                               valence: r.valence, arousal: r.arousal, in_range: true }));
  });
  // The 15 adult-oriented emotion keys — defined here to split the wardrobe view without
  // a backend round-trip. Mirrors NSFW_KEYS in loom/server/services/emotions.py.
  const NSFW_KEYS = new Set([
    'anticipation', 'desire', 'teasing', 'comfort', 'relief', 'ecstasy',
    'arousal', 'intensity', 'release', 'submission', 'arrogant',
    'condescension', 'discomfort', 'humiliation', 'pain',
  ]);
  // wardrobe mode: the currently selected outfit object + split emotion columns
  let selO    = $derived(data?.outfits?.[selOutfit] ?? null);
  let sfwCols  = $derived(columns.filter((c) => !NSFW_KEYS.has(c.emotion)));
  let nsfwCols = $derived(columns.filter((c) =>  NSFW_KEYS.has(c.emotion)));

  // wardrobe mode: inline editing of outfit name / instruction
  let editName = $state('');
  let editInstr = $state('');
  $effect(() => { editName = selO?.name ?? ''; editInstr = selO?.instruction ?? ''; });
  async function saveOutfitField(field, value) {
    if (!selO) return;
    const r = await post(`/characters/${charKey}/portraits/outfit/${selO.id}`, { [field]: value });
    if (r.data?.outfits) data = r.data;
  }

  // wardrobe mode: batch generation
  let genAllBusy = $state(false);
  async function genAllOutfits() {
    if (!data?.outfits?.length) return;
    genAllBusy = true;
    for (const o of data.outfits) {
      await genOutfit(o.id, 1);              // render 1 candidate
      const oc = ocell(o.id);
      if (oc?.cands?.length) await pickOutfit(o.id, oc.cands[0]); // auto-pick it
    }
    genAllBusy = false;
  }
  async function genAll() {
    await genAllOutfits();
    await renderEmotions({}, 'Rendering all expressions — all outfits');
  }

  // One-shot regen (1 candidate, auto-picked) — used by ImgCard regen buttons.
  async function regenOutfitImg(oid) {
    const k = outfitKey(oid);
    const slot = rensure(k); slot.busy = true; slot.cands = []; err = null;
    const rc = await post(`/characters/${charKey}/portraits/outfit/${oid}/recompose`, {});
    if (!rc.ok) { err = rc.data?.error || 'recompose failed'; slot.busy = false; return; }
    const job = startJob('Outfit image render', charName, screen, 1);
    const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${oid}/candidate`, {}, {}, job);
    if (r.ok && r.data?.image) { job.done = 1; await pickOutfit(oid, r.data.image); }
    else { err = r.data?.error || 'render failed'; job.status = 'error'; }
    if (job.status === 'running') job.status = 'done';
    slot.busy = false;
  }
  async function regenCell(oid, emo) {
    const k = cellKey(oid, emo);
    const slot = rensure(k); slot.busy = true; slot.cands = []; err = null;
    const job = startJob('Sprite render', `${charName} — ${emo}`, screen, 1);
    const r = await limitedPost(`/characters/${charKey}/sprite-candidate`, { outfit_id: oid, emotion: emo }, {}, job);
    if (r.ok && r.data?.image) {
      job.done = 1;
      await post(`/characters/${charKey}/sprite/select`, { outfit_id: oid, emotion: emo, data: r.data.image });
      await load(); bust++;
    } else { err = r.data?.error || 'render failed'; job.status = 'error'; }
    if (job.status === 'running') job.status = 'done';
    slot.busy = false;
  }

  // Auto-start generation when the parent signals it (e.g. after bulk wardrobe planning).
  $effect(() => {
    if (triggerGen > 0 && wardrobe && data?.outfits?.length) genAll();
  });

  // Look up a cell's rendered sprite (url + prompt) by joining the outfit's expression_set on the
  // emotion key. Every outfit carries the same keys (canonical order), so this is a stable join.
  function cellOf(outfit, emotion) {
    const e = (outfit.expression_set || []).find((x) => x.emotion === emotion);
    return e || { url: null, prompt: '', label: emotion };
  }

  // -- upfront batch render (the FULL emotion set, full body) --------------------
  let renderJob = $state(null);
  let renderTitle = $state('');
  async function renderEmotions(body, title) {
    err = null; renderTitle = title;
    const r = await post(`/characters/${charKey}/portraits/render-emotions`, { screen, ...body });
    if (r.ok && r.data?.job) renderJob = r.data.job;
    else err = r.data?.error || 'could not start render';
  }
  const renderAll = () => renderEmotions({}, 'Rendering every outfit — all emotions');
  const renderOutfit = (oid, name) => renderEmotions({ outfit_id: oid }, `Rendering ${name} — all emotions`);
  function onRenderDone() { renderJob = null; load(); bust++; }

  // -- per-cell sprite candidates (the 3-candidate redo) ------------------------
  const cellKey = (oid, emo) => `sprite:${charKey}:${oid}:${emo}`;
  function cell(oid, emo) { return rget(cellKey(oid, emo)); }

  async function genCell(oid, emo, n = CANDIDATES) {
    const k = cellKey(oid, emo);
    const slot = rensure(k); slot.busy = true; slot.cands = []; err = null;
    const job = startJob('Sprite render', `${charName} — ${emo}`, screen, n);
    for (let i = 0; i < n; i++) {
      const r = await limitedPost(`/characters/${charKey}/sprite-candidate`, { outfit_id: oid, emotion: emo }, {}, job);
      if (r.ok && r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    rensure(k).busy = false;
  }
  async function pick(oid, emo, dataUri) {
    const r = await post(`/characters/${charKey}/sprite/select`, { outfit_id: oid, emotion: emo, data: dataUri });
    if (r.data?.url) { rensure(cellKey(oid, emo)).cands = []; await load(); bust++; }
  }

  // -- per-outfit FULL-BODY image — the whole-look reference (3 candidates → pick) --
  const outfitKey = (oid) => `outfitimg:${charKey}:${oid}`;
  function ocell(oid) { return rget(outfitKey(oid)); }
  async function genOutfit(oid, n = CANDIDATES) {
    const k = outfitKey(oid);
    const slot = rensure(k); slot.busy = true; slot.cands = []; err = null;
    // Run the robust 2-step outfit-prompt pipeline FIRST (best-guess → CSV retrieval → refine),
    // so every regeneration rides a fresh, complete, colour-consistent prompt.
    const rc = await post(`/characters/${charKey}/portraits/outfit/${oid}/recompose`, {});
    if (!rc.ok) { err = rc.data?.error || 'could not compose outfit prompt'; slot.busy = false; return; }
    const job = startJob('Outfit image render', charName, screen, n);
    for (let i = 0; i < n; i++) {
      const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${oid}/candidate`, {}, {}, job);
      if (r.ok && r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    rensure(k).busy = false;
  }
  async function pickOutfit(oid, dataUri) {
    const r = await post(`/characters/${charKey}/portraits/outfit/${oid}/base`, { data: dataUri });
    if (r.data?.url) { const o = data.outfits.find((x) => x.id === oid); if (o) o.base = r.data.url; rensure(outfitKey(oid)).cands = []; bust++; }
  }
</script>

{#if data && data.outfits.length}

  {#if wardrobe}
    <!-- Wardrobe view: outfit list on left, selected outfit's expressions on right -->
    <div class="wv">
      <div class="wv-toolbar">
        <button class="ghost xs" onclick={genAllOutfits} disabled={genAllBusy || !!renderJob}
          title="Render one image per outfit (auto-picked)">🎨 Outfit images</button>
        <button class="ghost xs" onclick={() => renderEmotions({}, 'Rendering all expressions')} disabled={genAllBusy || !!renderJob}
          title="Render all emotion sprites for all outfits">🎭 Expressions</button>
        <button class="xs accent-btn" onclick={genAll} disabled={genAllBusy || !!renderJob}
          title="Render outfit images then all expression sprites">⚡ Generate all</button>
        {#if genAllBusy}
          <span class="wv-tbstatus"><span class="spin" aria-hidden="true"></span> Generating outfit images…</span>
        {:else if activeRenders.length}
          <span class="wv-tbstatus"><span class="spin" aria-hidden="true"></span>
            Rendering{renderTotal > 0 ? ` ${renderDone} / ${renderTotal}` : '…'}</span>
        {/if}
        {#if err}<span class="wv-tberr">⚠ {err}</span>{/if}
      </div>
      {#if renderJob}
        <div class="wv-stream">
          <GenStream jobId={renderJob} title={renderTitle} onDone={onRenderDone} onError={(m) => (err = m)} />
        </div>
      {/if}
      <div class="wv-layout">
        <div class="wv-left">
          {#each data.outfits as o, i (o.id)}
            <button class="wv-outfit" class:sel={i === selOutfit} onclick={() => (selOutfit = i)}>
              <div class="wv-thumb">
                <ImgCard src={o.base ? `${o.base}?b=${bust}` : null} caption={o.name}
                  prompt={o.attire_prompt} busy={ocell(o.id)?.busy}
                  onRegen={() => regenOutfitImg(o.id)} />
              </div>
              <span class="wv-oname">{o.name}</span>
            </button>
          {/each}
        </div>
        <div class="wv-right">
          {#if selO}
            <!-- Outfit meta: editable name/instruction + image generation controls -->
            <div class="wv-section-head">Outfit</div>
            <div class="wv-ometa">
              <div class="wv-orow">
                <label class="wv-olabel">Name</label>
                <input class="wv-oinput" value={editName}
                  oninput={(e) => (editName = e.target.value)}
                  onblur={() => saveOutfitField('name', editName)} />
              </div>
              {#if selO.attire_prompt}
                <div class="wv-orow">
                  <label class="wv-olabel">Image prompt</label>
                  <p class="wv-prompt-text">{selO.attire_prompt}</p>
                </div>
              {/if}
              {#if editInstr || true}
                <details class="wv-prompt-details">
                  <summary>Story context</summary>
                  <textarea class="wv-oinput wv-oinstr" rows="3" value={editInstr}
                    oninput={(e) => (editInstr = e.target.value)}
                    onblur={() => saveOutfitField('instruction', editInstr)}
                    placeholder="Describe the scene or story beat this outfit is for…"></textarea>
                </details>
              {/if}
              <div class="wv-oactions">
                <button class="ghost xs" onclick={() => genOutfit(selO.id)} disabled={ocell(selO.id)?.busy}>
                  {ocell(selO.id)?.busy ? '…' : (selO.base ? '↻ New outfit image' : '🎨 Render outfit image')}
                </button>
              </div>
              {#if ocell(selO.id)?.cands?.length}
                <div class="wv-ocands">
                  {#each ocell(selO.id).cands as img, ci (ci)}
                    <div class="wv-ocand">
                      <ZoomImage src={img} caption={`${selO.name} candidate ${ci + 1}`} inline />
                      <button class="usec" onclick={() => pickOutfit(selO.id, img)}>Use</button>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>

            {#if sfwCols.length}
              <div class="wv-section-head">Expressions</div>
              <div class="wv-grid">
                {#each sfwCols as col (col.emotion)}
                  {@const ce = cellOf(selO, col.emotion)}
                  <div class="wv-cell" class:outrange={!col.in_range}>
                    <div class="wv-img">
                      <ImgCard src={ce.url ? `${ce.url}?b=${bust}` : null}
                        caption={`${charName} — ${selO.name} — ${col.label}`}
                        prompt={ce.prompt}
                        busy={cell(selO.id, col.emotion)?.busy}
                        onRegen={() => regenCell(selO.id, col.emotion)} />
                    </div>
                    <span class="wv-elabel">{col.label}</span>
                  </div>
                {/each}
              </div>
            {/if}
            {#if nsfwCols.length}
              <div class="wv-section-head nsfw">Adult</div>
              <div class="wv-grid">
                {#each nsfwCols as col (col.emotion)}
                  {@const ce = cellOf(selO, col.emotion)}
                  <div class="wv-cell" class:outrange={!col.in_range}>
                    <div class="wv-img">
                      <ImgCard src={ce.url ? `${ce.url}?b=${bust}` : null}
                        caption={`${charName} — ${selO.name} — ${col.label}`}
                        prompt={ce.prompt}
                        busy={cell(selO.id, col.emotion)?.busy}
                        onRegen={() => regenCell(selO.id, col.emotion)} />
                    </div>
                    <span class="wv-elabel">{col.label}</span>
                  </div>
                {/each}
              </div>
            {/if}
          {/if}
        </div>
      </div>
    </div>

  {:else}
  <div class="sprites">
    {#if err}<div class="warn">⚠ {err}</div>{/if}

    {#if !gen && activeRenders.length}
      <div class="render-bar">
        <span class="spin" aria-hidden="true"></span>
        Rendering sprites{renderTotal > 0 ? ` — ${renderDone} / ${renderTotal}` : '…'}
      </div>
    {/if}

    {#if gen}
      <div class="head">
        <button class="ghost xs" onclick={renderAll} disabled={!!renderJob}>🎭 Render every emotion · all outfits</button>
        <span class="lo">{columns.length} emotions × {data.outfits.length} outfits — 2-D grid, scroll both ways</span>
        <label class="toggle"><input type="checkbox" bind:checked={showAll} /> full taxonomy ({data.emotions?.length || 0})</label>
      </div>
      {#if renderJob}
        <GenStream jobId={renderJob} title={renderTitle} onDone={onRenderDone} onError={(m) => (err = m)} />
      {/if}
    {/if}

    <!-- The 2-D wardrobe grid. One CSS grid; the body is a 2-D overflow:auto scroll container.
         The first row (emotion headers) is sticky-top; the first column (outfit labels) is
         sticky-left; the corner cell sits above both. Fixed cell sizes keep rows/columns aligned. -->
    <div class="grid-scroll">
      <div class="grid" style={`grid-template-columns: ${OUTFIT_COL}px repeat(${columns.length}, ${CELL}px);`}>
        <!-- corner cell (sticky top AND left, highest z) -->
        <div class="corner" class:gen>
          <span class="lo">outfit ▾ / emotion ▸</span>
        </div>
        <!-- sticky-top emotion header row -->
        {#each columns as col (col.emotion)}
          <div class="ehdr" class:outrange={!col.in_range}>
            <span class="elab">{col.label}</span>
            {#if typeof col.valence === 'number'}
              <span class="va" title="valence / arousal">v{col.valence.toFixed(2)} · a{col.arousal.toFixed(2)}</span>
            {/if}
          </div>
        {/each}

        <!-- one row per outfit: sticky-left outfit cell + its emotion cells -->
        {#each data.outfits as o (o.id)}
          {@const oc = ocell(o.id)}
          <!-- sticky-left outfit label cell (full-body thumb + name + outfit controls) -->
          <div class="ohdr" class:gen>
            <div class="oname">{o.name}</div>
            <div class="obase">
              {#if o.base}
                <ZoomImage src={`${o.base}?b=${bust}`} caption={`${charName} — ${o.name} (full body)`} inline />
              {:else if oc?.busy}
                <div class="ph">…</div>
              {:else}
                <div class="noimg">no image</div>
              {/if}
            </div>
            {#if gen}
              <div class="obtns">
                <button class="ghost xs" onclick={() => genOutfit(o.id)} disabled={oc?.busy}>
                  {oc?.busy ? '…' : (o.base ? '↻ Outfit' : '🎨 Outfit')}
                </button>
                <button class="ghost xs" onclick={() => renderOutfit(o.id, o.name)} disabled={!!renderJob}>🎭 Row</button>
              </div>
              {#if oc?.cands?.length}
                <div class="ocands">
                  {#each oc.cands as img, i (i)}
                    <div class="ocand">
                      <ZoomImage src={img} caption={`${o.name} candidate ${i + 1}`} inline />
                      <button class="usec" onclick={() => pickOutfit(o.id, img)}>Use</button>
                    </div>
                  {/each}
                </div>
              {/if}
            {/if}
          </div>

          <!-- the emotion cells for this outfit row -->
          {#each columns as col (col.emotion)}
            {@const c = cell(o.id, col.emotion)}
            {@const ce = cellOf(o, col.emotion)}
            <div class="cell" class:outrange={!col.in_range}>
              <div class="thumb">
                {#if ce.url}
                  <ZoomImage src={`${ce.url}?b=${bust}`} caption={`${charName} — ${o.name} — ${col.label}`} inline />
                  {#if gen && !c?.busy && !c?.cands?.length}
                    <button class="redo" title="Regenerate (3 candidates)"
                      onclick={(ev) => { ev.stopPropagation(); genCell(o.id, col.emotion); }}>↻</button>
                  {/if}
                {:else if c?.busy}
                  <div class="ph">…</div>
                {:else if gen}
                  <button class="genc" onclick={() => genCell(o.id, col.emotion)} title="Generate {col.label}">＋</button>
                {:else}
                  <div class="ph dim">—</div>
                {/if}
              </div>
              {#if gen && c?.cands?.length}
                <div class="cands">
                  {#each c.cands as img, i (i)}
                    <div class="cand">
                      <ZoomImage src={img} caption={`${o.name} — ${col.label} candidate ${i + 1}`} inline />
                      <button class="usec" onclick={() => pick(o.id, col.emotion, img)}>Use</button>
                    </div>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        {/each}
      </div>
    </div>
  </div>
  {/if}<!-- end wardrobe {:else} -->

{:else}
  <p class="hint lo">{wardrobe || gen ? 'No wardrobe yet — use ✨ Plan wardrobe to generate outfits.' : 'No outfits yet — use ↻ Regenerate to plan a wardrobe.'}</p>
{/if}

<style>
  /* ---- wardrobe view ---- */
  .wv { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .wv-toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 8px 12px; border-bottom: 1px solid var(--border-soft); flex-shrink: 0; background: var(--bg); }
  .accent-btn { background: var(--accent); color: #fff; border: 0; }
  .accent-btn:hover:not(:disabled) { filter: brightness(1.1); }
  .wv-tbstatus { font-size: 11.5px; color: var(--muted); display: flex; align-items: center; gap: 5px; }
  .wv-tberr { font-size: 11.5px; color: var(--bad); }
  .wv-stream { padding: 8px 12px; border-bottom: 1px solid var(--border-soft); flex-shrink: 0; }
  .wv-bar { margin: 0 0 6px; }
  .wv-layout { flex: 1; min-height: 0; display: flex; }
  .wv-left { width: 172px; flex-shrink: 0; overflow-y: auto; border-right: 1px solid var(--border-soft);
    display: flex; flex-direction: column; gap: 4px; padding: 8px; }
  .wv-outfit { display: flex; flex-direction: column; align-items: center; gap: 5px; padding: 7px;
    border-radius: 10px; border: 1px solid transparent; background: none; box-shadow: none;
    cursor: pointer; width: 100%; flex-shrink: 0; }
  .wv-outfit:hover { background: var(--elev); filter: none; }
  .wv-outfit.sel { border-color: var(--accent); background: var(--panel);
    box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .wv-thumb { width: 100%; aspect-ratio: 3/4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .wv-oname { font-size: 11px; font-weight: 600; color: var(--text); text-align: center;
    line-height: 1.3; word-break: break-word; }
  .wv-right { flex: 1; min-width: 0; overflow-y: auto; padding: 14px 16px;
    display: flex; flex-direction: column; gap: 12px; }
  .wv-ometa { display: flex; flex-direction: column; gap: 8px; margin-bottom: 14px; }
  .wv-orow { display: flex; flex-direction: column; gap: 3px; }
  .wv-olabel { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .wv-oinput { width: 100%; font-size: 13px; padding: 6px 8px; }
  .wv-oinstr { resize: vertical; font-family: inherit; line-height: 1.4; }
  .wv-prompt-text { margin: 4px 0 0; line-height: 1.5; color: var(--muted); font-size: 11.5px; }
  .wv-prompt-details { font-size: 11.5px; color: var(--muted); }
  .wv-prompt-details summary { cursor: pointer; color: var(--faint); font-size: 10.5px; user-select: none; margin-bottom: 4px; }
  .wv-oactions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .wv-ocands { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 6px; }
  .wv-ocand { display: flex; flex-direction: column; gap: 4px; width: 140px; }
  .wv-ocand :global(.zoom-inline), .wv-ocand :global(img) { width: 100%; border-radius: 8px; }
  .wv-ocand .usec { font-size: 11px; padding: 3px 10px; }
  .wv-section-head { font-size: 10.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .5px; color: var(--muted); padding-bottom: 6px;
    border-bottom: 1px solid var(--border-soft); }
  .wv-section-head.nsfw { color: #c084fc; border-color: rgba(192,132,252,.25); }
  .wv-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }
  .wv-cell { display: flex; flex-direction: column; gap: 5px; }
  .wv-cell.outrange { opacity: .38; }
  .wv-img { aspect-ratio: 3/4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .wv-elabel { font-size: 11px; font-weight: 600; color: var(--muted); text-align: center; }

  .sprites { margin-top: 10px; }
  .lo { color: var(--faint); font-size: 11.5px; }
  .warn { font-size: 12px; color: #ffd479; margin: 4px 0; }
  .render-bar { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--accent);
    padding: 6px 10px; border-radius: 8px; background: rgba(109,140,255,.10);
    border: 1px solid rgba(109,140,255,.25); margin: 2px 0 8px; }
  .spin { display: inline-block; width: 13px; height: 13px; border-radius: 50%;
    border: 2px solid rgba(109,140,255,.3); border-top-color: var(--accent);
    animation: spin .7s linear infinite; flex: none; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hint { margin: 6px 0 0; font-size: 11.5px; }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 2px 0 8px; }
  .toggle { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--muted); cursor: pointer; user-select: none; }
  .toggle input { accent-color: var(--accent); }

  /* The 2-D scroll container. Max-height lets the grid scroll vertically within the page instead of
     growing unbounded; horizontal overflow scrolls right for more emotions. */
  .grid-scroll {
    overflow: auto; max-height: 70vh;
    border: 1px solid var(--border-soft); border-radius: 12px;
    background: var(--panel);
  }
  /* The grid itself. `align-content: start` keeps rows top-aligned. Sticky on the header row + the
     outfit column + the corner is the CSS-only frozen-header technique (no JS scroll syncing). */
  .grid {
    display: grid; align-content: start;
    background: var(--bg);
  }
  .grid > :global(*) { box-sizing: border-box; }

  /* corner cell — sticky top AND left, highest z-index so it floats over both axes */
  .corner {
    position: sticky; top: 0; left: 0; z-index: 3;
    display: grid; place-items: center;
    background: var(--panel); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border);
  }
  .corner.gen { background: var(--elev-2); }

  /* sticky-top emotion header row */
  .ehdr {
    position: sticky; top: 0; z-index: 2;
    display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
    padding: 6px 4px;
    background: var(--panel); border-right: 1px solid var(--border-soft); border-bottom: 1px solid var(--border);
    text-align: center;
  }
  .elab { font-size: 10.5px; font-weight: 600; color: var(--text); line-height: 1.15; }
  .va { font-size: 8.5px; color: var(--faint); font-variant-numeric: tabular-nums; }

  /* sticky-left outfit label cell (one per row) */
  .ohdr {
    position: sticky; left: 0; z-index: 1;
    display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 8px;
    background: var(--panel); border-right: 1px solid var(--border); border-bottom: 1px solid var(--border-soft);
  }
  .ohdr.gen { background: var(--elev); }
  .oname { font-size: 11.5px; font-weight: 650; color: var(--text); text-align: center; }
  .obase { width: 100%; aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; max-height: 150px;
           border: 1px solid var(--border); background: var(--bg); display: grid; place-items: center; }
  .obase :global(img), .obase :global(.zoom-inline) { border-radius: 8px; }
  .noimg { font-size: 10.5px; color: var(--faint); text-align: center; padding: 0 4px; }
  .obtns { display: flex; flex-direction: column; gap: 4px; align-items: stretch; width: 100%; }
  .ocands { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; }
  .ocand { display: flex; flex-direction: column; gap: 2px; width: 56px; }
  .ocand :global(.zoom-inline), .ocand :global(img) { border-radius: 5px; }

  /* body cells — uniform size so rows/columns align with the sticky headers */
  .cell {
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    padding: 8px 4px;
    border-right: 1px solid var(--border-soft); border-bottom: 1px solid var(--border-soft);
    background: var(--bg);
  }
  .thumb { position: relative; width: 88px; aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden;
           border: 1px solid var(--border); background: var(--panel); display: grid; place-items: center; }
  .thumb :global(img), .thumb :global(.zoom-inline) { border-radius: 8px; }
  .ph { color: var(--faint); font-size: 18px; }
  .ph.dim { color: var(--border); font-size: 16px; }
  .genc { width: 100%; height: 100%; border-radius: 0; box-shadow: none; background: none; border: none; color: var(--muted); font-size: 20px; }
  .genc:hover:not(:disabled) { color: var(--accent); filter: none; background: var(--elev-2); }
  /* reset/regen overlays the sprite, top-right, revealed on hover */
  .redo { position: absolute; top: 3px; right: 3px; z-index: 2; font-size: 11px; line-height: 1;
          padding: 3px 6px; border-radius: 6px; box-shadow: none; color: #fff;
          background: rgba(0, 0, 0, .55); border: 1px solid rgba(255, 255, 255, .25);
          opacity: 0; transition: opacity .12s; }
  .cell:hover .redo { opacity: 1; }
  .redo:hover { color: var(--accent); border-color: var(--accent); background: rgba(0, 0, 0, .78); filter: none; }
  .cands { display: flex; gap: 5px; flex-wrap: wrap; justify-content: center; }
  .cand { display: flex; flex-direction: column; gap: 2px; width: 40px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 5px; }
  .usec { font-size: 9.5px; padding: 1px 0; border-radius: 5px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); width: 100%; }
  .usec:hover { color: var(--accent); border-color: var(--accent); filter: none; }

  /* an out-of-personality-range column (only shown with the 'full taxonomy' toggle) is dimmed */
  .ehdr.outrange, .cell.outrange { opacity: .42; }

  .xs { font-size: 11.5px; padding: 3px 9px; border-radius: 7px; }
</style>
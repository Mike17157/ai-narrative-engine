<script>
  import { get, post } from '$lib/api.js';
  import { startJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import GenStream from '$lib/components/GenStream.svelte';

  // Per-outfit images: a FULL-BODY outfit image + the FIXED canonical emotion taxonomy (~32,
  // Plutchik-grounded). Each outfit is ONE ROW — the full-body image on the left (Y axis = outfits),
  // its expression range scrolling horizontally on the right (X axis = expressions). txt2img —
  // identity comes from the appearance tags.
  //   mode='inspect'  → read-only: zoom any cell; no generation controls (the pane).
  //   mode='generate' → full controls: render outfit images + the emotion range, re-roll cells (the
  //                     RegenModal). charKey · charName.
  let { charKey, charName, hasRef = true, refresh = 0, mode = 'generate',
        screen = 'characters/selected' } = $props();
  const CANDIDATES = 3;   // per-cell redo always generates three
  const gen = $derived(mode === 'generate');

  let data = $state(null);     // { outfits:[{id,name,base,expression_set:[{emotion,label,prompt,url}]}] }
  let bust = $state(0);
  let err = $state(null);

  async function load() { try { data = await get(`/characters/${charKey}/portraits`); } catch { data = null; } }
  $effect(() => { charKey; refresh; load(); });

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
      const r = await post(`/characters/${charKey}/sprite-candidate`, { outfit_id: oid, emotion: emo });
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
      const r = await post(`/characters/${charKey}/portraits/outfit/${oid}/candidate`, {});
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
  <div class="sprites">
    {#if err}<div class="warn">⚠ {err}</div>{/if}

    {#if gen}
      <div class="head">
        <button class="ghost xs" onclick={renderAll} disabled={!!renderJob}>🎭 Render every emotion · all outfits</button>
        <span class="lo">{data.emotions?.length || 0} emotions × {data.outfits.length} outfits — full body, rendered upfront</span>
      </div>
      {#if renderJob}
        <GenStream jobId={renderJob} title={renderTitle} onDone={onRenderDone} onError={(m) => (err = m)} />
      {/if}
    {/if}

    {#each data.outfits as o (o.id)}
      {@const oc = ocell(o.id)}
      <!-- ONE ROW per outfit: outfit image + name (Y), expressions scroll horizontally (X) -->
      <div class="outfit">
        <div class="ohead">
          <div class="oname">{o.name}</div>
          <div class="obase">
            {#if o.base}
              <ZoomImage src={`${o.base}?b=${bust}`} caption={`${charName} — ${o.name} (full body)`} inline />
            {:else if oc?.busy}
              <div class="ph">…</div>
            {:else}
              <div class="noimg">no full-body image</div>
            {/if}
          </div>
          {#if gen}
            <div class="obtns">
              <button class="ghost xs" onclick={() => genOutfit(o.id)} disabled={oc?.busy}>
                {oc?.busy ? 'Rendering…' : (o.base ? '↻ Regenerate outfit image' : '🎨 Generate outfit image')}
              </button>
              <button class="ghost xs" onclick={() => renderOutfit(o.id, o.name)} disabled={!!renderJob}>
                🎭 Render all emotions
              </button>
            </div>
            {#if oc?.cands?.length}
              <div class="ocands">
                {#each oc.cands as img, i (i)}
                  <div class="ocand">
                    <ZoomImage src={img} caption={`${charName} — ${o.name} candidate ${i + 1}`} inline />
                    <button class="usec" onclick={() => pickOutfit(o.id, img)}>Use</button>
                  </div>
                {/each}
              </div>
            {/if}
          {/if}
        </div>

        <div class="emwrap">
          <div class="emlabel">Expressions <span class="lo">— the full emotion range</span></div>
          <div class="row">
            {#each o.expression_set as e (e.emotion)}
              {@const c = cell(o.id, e.emotion)}
              <div class="cell">
                <div class="thumb">
                  {#if e.url}
                    <ZoomImage src={`${e.url}?b=${bust}`} caption={`${charName} — ${o.name} — ${e.label}`} inline />
                    {#if gen && !c?.busy && !c?.cands?.length}
                      <button class="redo" title="Regenerate (3 candidates)"
                        onclick={(ev) => { ev.stopPropagation(); genCell(o.id, e.emotion); }}>↻</button>
                    {/if}
                  {:else if c?.busy}
                    <div class="ph">…</div>
                  {:else if gen}
                    <button class="genc" onclick={() => genCell(o.id, e.emotion)} title="Generate {e.label}">＋</button>
                  {:else}
                    <div class="ph dim">—</div>
                  {/if}
                </div>
                <span class="emo">{e.label}</span>
                {#if gen && c?.cands?.length}
                  <div class="cands">
                    {#each c.cands as img, i (i)}
                      <div class="cand">
                        <ZoomImage src={img} caption={`${charName} — ${o.name} — ${e.label} candidate ${i + 1}`} inline />
                        <button class="usec" onclick={() => pick(o.id, e.emotion, img)}>Use</button>
                      </div>
                    {/each}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        </div>
      </div>
    {/each}
  </div>
{:else}
  <p class="hint lo">{gen ? 'No wardrobe yet — use ✨ Plan wardrobe to generate outfits.' : 'No outfits yet — use ↻ Regenerate to plan a wardrobe.'}</p>
{/if}

<style>
  .sprites { margin-top: 10px; }
  .lo { color: var(--faint); font-size: 11.5px; }
  .warn { font-size: 12px; color: #ffd479; margin: 4px 0; }
  .hint { margin: 6px 0 0; font-size: 11.5px; }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 2px 0 6px; }
  /* one row per outfit: image block (left) + horizontally-scrolling expressions (right) */
  .outfit { display: grid; grid-template-columns: 150px 1fr; gap: 14px; align-items: start;
            margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-soft); }
  .outfit:first-of-type { border-top: 0; padding-top: 0; }
  .ohead { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
  .oname { font-size: 12.5px; font-weight: 650; color: var(--text); }
  .obase { width: 150px; flex: none; aspect-ratio: 3 / 4; border-radius: 9px; overflow: hidden;
           border: 1px solid var(--border); background: var(--bg); display: grid; place-items: center; }
  .obase :global(img), .obase :global(.zoom-inline) { border-radius: 9px; }
  .noimg { font-size: 11px; color: var(--faint); text-align: center; padding: 0 6px; }
  .obtns { display: flex; flex-direction: column; gap: 6px; align-items: stretch; width: 150px; }
  .ocands { display: flex; gap: 8px; flex-wrap: wrap; }
  .ocand { display: flex; flex-direction: column; gap: 3px; width: 64px; }
  .ocand :global(.zoom-inline), .ocand :global(img) { border-radius: 6px; }
  .emwrap { min-width: 0; }
  .emlabel { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; margin: 0 0 6px; }
  /* X axis: a single horizontally-scrolling row of expression cells */
  .row { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 6px; align-items: flex-start; }
  .cell { flex: 0 0 auto; width: 88px; display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .thumb { position: relative; width: 88px; aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); background: var(--bg); display: grid; place-items: center; }
  .ph { color: var(--faint); font-size: 18px; }
  .ph.dim { color: var(--border); font-size: 16px; }
  .genc { width: 100%; height: 100%; border-radius: 0; box-shadow: none; background: none; border: none; color: var(--muted); font-size: 20px; }
  .genc:hover:not(:disabled) { color: var(--accent); filter: none; background: var(--elev-2); }
  .emo { font-size: 10.5px; color: var(--muted); text-align: center; }
  /* reset/regen overlays the sprite, top-right, revealed on hover */
  .redo { position: absolute; top: 3px; right: 3px; z-index: 2; font-size: 11px; line-height: 1;
          padding: 3px 6px; border-radius: 6px; box-shadow: none; color: #fff;
          background: rgba(0, 0, 0, .55); border: 1px solid rgba(255, 255, 255, .25);
          opacity: 0; transition: opacity .12s; }
  .cell:hover .redo { opacity: 1; }
  .redo:hover { color: var(--accent); border-color: var(--accent); background: rgba(0, 0, 0, .78); filter: none; }
  .cands { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; }
  .cand { display: flex; flex-direction: column; gap: 2px; width: 42px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 5px; }
  .usec { font-size: 9.5px; padding: 1px 0; border-radius: 5px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); width: 100%; }
  .usec:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .xs { font-size: 11.5px; padding: 3px 9px; border-radius: 7px; }
</style>

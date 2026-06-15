<script>
  import { get, post, del } from '$lib/api.js';
  import { startJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';

  // Per-outfit images: a FULL-BODY outfit image + this outfit's OWN expression range. Each outfit
  // owns its expressions (the runtime picks the closest by emotion vector similarity). txt2img —
  // identity comes from the appearance tags. charKey · charName.
  let { charKey, charName, hasRef = true, refresh = 0, screen = 'characters/selected' } = $props();
  const CANDIDATES = 3;   // always generate three

  let data = $state(null);     // { outfits:[{id,name,base,expression_set:[{emotion,prompt,url}]}] }
  let bust = $state(0);
  let err = $state(null);

  async function load() { try { data = await get(`/characters/${charKey}/portraits`); } catch { data = null; } }
  $effect(() => { charKey; refresh; load(); });

  // Per-cell sprite candidates live in the shared store so they survive navigation.
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

  // Add / remove an expression for THIS outfit only (unique per outfit).
  let addingFor = $state('');
  let newEmo = $state('');
  function startAdd(oid) { addingFor = oid; newEmo = ''; }
  async function addExpr(oid) {
    const emo = newEmo.trim(); if (!emo) return;
    const r = await post(`/characters/${charKey}/portraits/outfit/${oid}/expression-prompt`, { emotion: emo });
    if (r.ok) { addingFor = ''; newEmo = ''; await load(); }
  }
  async function delExpr(oid, emo) { await del(`/characters/${charKey}/portraits/outfit/${oid}/expression/${emo}`); await load(); }

  // Per-outfit FULL-BODY image — the whole-look reference (3 candidates → pick).
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

    {#each data.outfits as o (o.id)}
      {@const oc = ocell(o.id)}
      <div class="outfit">
        <div class="oname">{o.name}</div>

        <!-- FULL-BODY outfit image — the Regenerate button sits UNDER the image -->
        <div class="ofull">
          <div class="obase">
            {#if o.base}
              <ZoomImage src={`${o.base}?b=${bust}`} caption={`${charName} — ${o.name} (full body)`} inline />
            {:else if oc?.busy}
              <div class="ph">…</div>
            {:else}
              <div class="noimg">no full-body image</div>
            {/if}
          </div>
          <button class="ghost xs ofullbtn" onclick={() => genOutfit(o.id)} disabled={oc?.busy}>
            {oc?.busy ? 'Rendering…' : (o.base ? '↻ Regenerate outfit image' : '🎨 Generate outfit image')}
          </button>
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
        </div>

        <div class="emlabel">Expressions <span class="lo">— this outfit's range (add unique ones)</span></div>
        <div class="grid">
          {#each o.expression_set as e (e.emotion)}
            {@const c = cell(o.id, e.emotion)}
            <div class="cell">
              <div class="thumb">
                {#if e.url}
                  <ZoomImage src={`${e.url}?b=${bust}`} caption={`${charName} — ${o.name} — ${e.emotion}`} inline />
                {:else if c?.busy}
                  <div class="ph">…</div>
                {:else}
                  <button class="genc" onclick={() => genCell(o.id, e.emotion)} title="Generate {e.emotion}">＋</button>
                {/if}
              </div>
              <span class="emo">{e.emotion}</span>
              {#if c?.cands?.length}
                <div class="cands">
                  {#each c.cands as img, i (i)}
                    <div class="cand">
                      <ZoomImage src={img} caption={`${charName} — ${o.name} — ${e.emotion} candidate ${i + 1}`} inline />
                      <button class="usec" onclick={() => pick(o.id, e.emotion, img)}>Use</button>
                    </div>
                  {/each}
                </div>
              {:else if !c?.busy}
                <div class="cellacts">
                  {#if e.url}<button class="redo" onclick={() => genCell(o.id, e.emotion)} title="Regenerate">↻</button>{/if}
                  <button class="del" onclick={() => delExpr(o.id, e.emotion)} title="Remove expression">✕</button>
                </div>
              {/if}
            </div>
          {/each}
          <div class="cell">
            <button class="addexpr" onclick={() => startAdd(o.id)} title="Add an expression">＋<br />expr</button>
          </div>
        </div>
        {#if addingFor === o.id}
          <div class="addrow">
            <input class="fld" placeholder="emotion (e.g. determined)" bind:value={newEmo}
              onkeydown={(ev) => ev.key === 'Enter' && addExpr(o.id)} />
            <button class="ghost xs" onclick={() => addExpr(o.id)}>Add</button>
            <button class="ghost xs" onclick={() => (addingFor = '')}>Cancel</button>
          </div>
        {/if}
        {#if !o.expression_set.length}<p class="hint lo">No expressions yet — add one (＋ expr).</p>{/if}
      </div>
    {/each}
  </div>
{:else}
  <p class="hint lo">No wardrobe yet — use <b>✨ Plan wardrobe</b> above to generate outfits + expressions.</p>
{/if}

<style>
  .sprites { margin-top: 10px; }
  .lo { color: var(--faint); font-size: 11.5px; }
  .warn { font-size: 12px; color: #ffd479; margin: 4px 0; }
  .hint { margin: 6px 0 0; font-size: 11.5px; }
  .outfit { margin-top: 14px; }
  .oname { font-size: 12px; font-weight: 650; color: var(--text); margin-bottom: 4px; }
  /* outfit image stacks: image, then the Regenerate button UNDER it, then candidates */
  .ofull { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; margin: 4px 0 8px; }
  .obase { width: 132px; flex: none; aspect-ratio: 3 / 4; border-radius: 9px; overflow: hidden;
           border: 1px solid var(--border); background: var(--bg); display: grid; place-items: center; }
  .obase :global(img), .obase :global(.zoom-inline) { border-radius: 9px; }
  .noimg { font-size: 11px; color: var(--faint); text-align: center; padding: 0 6px; }
  .ofullbtn { align-self: flex-start; }
  .ocands { display: flex; gap: 8px; flex-wrap: wrap; }
  .ocand { display: flex; flex-direction: column; gap: 3px; width: 72px; }
  .ocand :global(.zoom-inline), .ocand :global(img) { border-radius: 6px; }
  .emlabel { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; margin: 6px 0 4px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
  .cell { display: flex; flex-direction: column; align-items: center; gap: 3px; }
  .thumb { width: 100%; aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); background: var(--bg); display: grid; place-items: center; }
  .ph { color: var(--faint); font-size: 18px; }
  .genc { width: 100%; height: 100%; border-radius: 0; box-shadow: none; background: none; border: none; color: var(--muted); font-size: 20px; }
  .genc:hover:not(:disabled) { color: var(--accent); filter: none; background: var(--elev-2); }
  .addexpr { width: 100%; aspect-ratio: 3 / 4; border-radius: 8px; box-shadow: none; background: var(--elev); border: 1px dashed var(--border);
             color: var(--muted); font-size: 11px; line-height: 1.2; }
  .addexpr:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .emo { font-size: 10.5px; color: var(--muted); text-transform: capitalize; }
  .cellacts { display: flex; gap: 4px; }
  .cands { display: flex; gap: 6px; flex-wrap: wrap; justify-content: center; }
  .cand { display: flex; flex-direction: column; gap: 2px; width: 46px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 5px; }
  .usec { font-size: 9.5px; padding: 1px 0; border-radius: 5px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); width: 100%; }
  .usec:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .redo, .del { font-size: 11px; padding: 1px 7px; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); }
  .redo:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .del:hover { color: var(--bad); border-color: var(--bad); filter: none; }
  .addrow { display: flex; gap: 6px; margin-top: 8px; align-items: center; }
  .addrow .fld { flex: 1; padding: 6px 9px; font-size: 12.5px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .addrow .fld:focus { border-color: var(--accent); outline: none; }
  .xs { font-size: 11.5px; padding: 3px 9px; border-radius: 7px; }
</style>

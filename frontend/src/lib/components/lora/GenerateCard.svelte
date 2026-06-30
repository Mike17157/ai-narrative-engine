<script>
  // The live batch grid: streams generated image variations for the prompts in
  // the pipeline (lora.svelte.js). Click images to keep them, then save a
  // curated LoRA dataset. Renders inline (no separate route) — shows an empty
  // state until a batch is started by PromptSet above.
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ProgressBar from '$lib/components/shared/ProgressBar.svelte';
  import { openLightbox } from '$lib/lightbox.svelte.js';
  import {
    lora, selectedCount, pick, cancelJob, saveSet,
  } from '$lib/lora.svelte.js';

  let baseItems = $derived([{ value: '', label: '(unset)' }, ...((lora.choices.checkpoints || []).map((c) => ({ value: c, label: c })))]);
  let chosen = $derived(selectedCount());
</script>

{#if !lora.rows.length}
  <div class="empty">
    <div class="hint">No batch yet. Configure prompts above and hit Generate — results stream in here, and you can roam the app while it runs.</div>
  </div>
{:else}
  <div class="row" style="justify-content:space-between; align-items:center">
    <div>
      <strong>{chosen}</strong> chosen
      <span class="hint">· click any images to keep them · {lora.progress.done}/{lora.progress.total} generated{lora.busy ? (lora.cancelling ? ' · cancelling…' : ' …') : ''}</span>
      {#if lora.jobStatus === 'cancelled'}<span class="hint" style="color:var(--bad)">· cancelled</span>
      {:else if lora.jobStatus === 'error'}<span class="hint" style="color:var(--bad)">· errored</span>
      {:else if lora.jobStatus === 'done'}<span class="hint" style="color:var(--good)">· complete</span>{/if}
    </div>
    {#if lora.busy}<button class="ghost sm" onclick={cancelJob} disabled={lora.cancelling}>{lora.cancelling ? 'Cancelling…' : 'Cancel'}</button>{/if}
  </div>
  {#if lora.progress.total}<ProgressBar value={lora.progress.done} max={lora.progress.total} margin="10px 0" />{/if}

  <div class="rows">
    {#each lora.rows as r, ri (ri)}
      <div class="lrow" class:done={r.cells.some((c) => c.kept)}>
        <div class="cells">
          {#each r.cells as c, ci (ci)}
            <div class="cell" class:sel={c.kept}>
              {#if c.src}
                <img src={c.src} alt="" onclick={() => pick(ri, ci)} />
                <button class="zoom" title="Enlarge" aria-label="Enlarge"
                  onclick={(e) => { e.stopPropagation(); openLightbox(c.src, r.prompt); }}>⤢</button>
                {#if c.kept}<span class="check" aria-hidden="true">✓</span>{/if}
              {:else if c.error}<span class="cerr" title={c.error}>!</span>
              {:else}<span class="spin"></span>{/if}
            </div>
          {/each}
        </div>
        <div class="cap" title={r.prompt}>{r.prompt}</div>
      </div>
    {/each}
  </div>

  <div class="saver">
    <input bind:value={lora.saveName} placeholder="set name" />
    <Combobox items={baseItems} value={lora.baseModel} placeholder="base model…" onpick={(v) => (lora.baseModel = v)} />
    <button onclick={saveSet} disabled={lora.busy}>Save LoRA Set</button>
  </div>
  {#if lora.saveMsg}<div class:ok={lora.saveMsg.ok} class:err={lora.saveMsg.err} style="font-size:13px;margin-top:8px">{lora.saveMsg.text}</div>{/if}
{/if}

<style>
  .empty { padding: 24px 8px; }
  .row { display: flex; gap: 10px; align-items: center; }
  .rows { display: flex; flex-direction: column; gap: 10px; }
  .lrow { padding: 8px; border: 1px solid var(--border-soft); border-radius: var(--radius); }
  .lrow.done { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  .cells { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
  .cell { position: relative; padding: 0; aspect-ratio: 1; background: var(--elev); border: 2px solid transparent; border-radius: 8px; overflow: hidden; display: grid; place-items: center; }
  .cell:hover { border-color: var(--border); }
  .cell.sel { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }
  .cell img { width: 100%; height: 100%; object-fit: cover; display: block; cursor: pointer; transition: transform .25s ease; }
  .cell:hover img { transform: scale(1.06); }
  .cell .zoom {
    position: absolute; top: 5px; right: 5px; width: 26px; height: 26px; padding: 0;
    display: grid; place-items: center; font-size: 14px; line-height: 1; color: #fff;
    background: rgba(12, 14, 20, .62); border: 1px solid rgba(255, 255, 255, .18);
    border-radius: 7px; opacity: 0; transform: translateY(-3px);
    transition: opacity .15s, transform .15s, background .15s; backdrop-filter: blur(4px);
  }
  .cell:hover .zoom { opacity: 1; transform: none; }
  .cell .zoom:hover { background: rgba(20, 24, 34, .9); }
  .cell .check {
    position: absolute; top: 5px; left: 5px; width: 22px; height: 22px; display: grid; place-items: center;
    font-size: 13px; font-weight: 800; color: #0b0e14; background: var(--accent);
    border-radius: 50%; box-shadow: 0 1px 6px rgba(0, 0, 0, .4);
  }
  .cerr { color: var(--bad); font-weight: 700; }
  .spin { width: 16px; height: 16px; border: 2px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: sp 1s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }
  .cap { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 4px; }
  .saver { display: flex; gap: 10px; align-items: center; margin-top: 16px; position: sticky; bottom: 0; padding: 12px 0; background: var(--bg); border-top: 1px solid var(--border); }
  .saver input { width: 160px; }
</style>

<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { img, saveTestPrompt } from '$lib/images.svelte.js';
  import { loraLib, setCfg, save } from '$lib/lora-library.svelte.js';
  import { lora, loadSets, loadChoices, attach as attachGen } from '$lib/lora.svelte.js';

  import PromptSet from '$lib/components/lora/PromptSet.svelte';
  import GenerateCard from '$lib/components/lora/GenerateCard.svelte';
  import DatasetCard from '$lib/components/lora/DatasetCard.svelte';
  import TrainCard from '$lib/components/lora/TrainCard.svelte';
  import ClassifyCard from '$lib/components/lora/ClassifyCard.svelte';
  import LibraryCard from '$lib/components/lora/LibraryCard.svelte';
  import StackBuilder from '$lib/components/lora/StackBuilder.svelte';
  import TestBench from '$lib/components/lora/TestBench.svelte';

  let classify;
  let datasetCard;
  let trainCard;

  let tab = $state('library');      // 'library' | 'stacks'
  let activeStack = $state('');     // name of the stack selected in StackBuilder

  $effect(() => { saveTestPrompt(img.testPrompt); });

  onMount(async () => {
    try { setCfg(await get('/loras')); } catch { /* none yet */ }
    try { loraLib.choices = await get('/comfy/choices'); } catch { /* comfy off */ }
    try { loraLib.bases = await get('/loras/bases'); } catch { /* none */ }
    try { loraLib.scan = await get('/comfy/models'); } catch { /* scan unavailable */ }
    try { loraLib.families = (await get('/families')).families || []; } catch { /* registry unavailable */ }
    const seed = loraLib.bases.find((b) => b.key === app.activeImage)?.key || loraLib.bases[0]?.key || '';
    classify?.setBaseModel(seed);
    classify?.buildTriage();
    await loadSets();
    await loadChoices();
    const j = await get('/lora/job');
    if (j && j.status && j.status !== 'idle') attachGen();
  });

  let lastSaved = $state(null);
  $effect(() => {
    const m = lora.saveMsg;
    if (m?.ok && m.text && m.text !== lastSaved) {
      lastSaved = m.text;
      datasetCard?.refresh();
      trainCard?.refresh();
    }
  });
</script>

<div class="tprompt">
  <label for="tp">Test prompt <span class="lo">— shared across classify, stack test, and generation</span></label>
  <textarea id="tp" rows="3" bind:value={img.testPrompt} placeholder="1girl, solo, standing…"></textarea>
</div>

<div class="tabs">
  <button class="tab" class:active={tab === 'library'} onclick={() => (tab = 'library')}>Library</button>
  <button class="tab" class:active={tab === 'stacks'} onclick={() => (tab = 'stacks')}>Stacks &amp; Test</button>
</div>

{#if tab === 'library'}
  <ClassifyCard bind:this={classify} />
  <LibraryCard />
  <div class="savebar">
    <button onclick={save} disabled={loraLib.saving}>{loraLib.saving ? 'Saving…' : 'Save library + stacks'}</button>
    {#if loraLib.msg}<span class:ok={loraLib.msg.ok} class:err={loraLib.msg.err} class="m">{loraLib.msg.text}</span>{/if}
  </div>
{:else}
  <div class="stackpane">
    <div class="sleft">
      <StackBuilder onchange={(name) => (activeStack = name)} />
    </div>
    <div class="sright">
      <TestBench stack={activeStack} />
    </div>
  </div>
  <div class="savebar">
    <button onclick={save} disabled={loraLib.saving}>{loraLib.saving ? 'Saving…' : 'Save stacks'}</button>
    {#if loraLib.msg}<span class:ok={loraLib.msg.ok} class:err={loraLib.msg.err} class="m">{loraLib.msg.text}</span>{/if}
  </div>
{/if}

<!-- LoRA production pipeline — collapsed by default; not the primary flow -->
<details class="grp">
  <summary><span class="gsub-ico">⚙</span> Generate a new LoRA <span class="gsub">— prompts → batch → keep → save a dataset</span></summary>
  <div class="gbody">
    <PromptSet />
    <div class="card"><h3>Batch</h3><GenerateCard /></div>
  </div>
</details>
<details class="grp">
  <summary><span class="gsub-ico">⚙</span> Caption &amp; train <span class="gsub">— tag the dataset, then train with kohya or Anima</span></summary>
  <div class="gbody">
    <div class="card"><h3>Dataset</h3><DatasetCard bind:this={datasetCard} /></div>
    <div class="card"><h3>Train</h3><TrainCard bind:this={trainCard} /></div>
  </div>
</details>

<style>
  .tprompt { margin-bottom: 14px; }
  .tprompt label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 5px; }
  .tprompt textarea { width: 100%; resize: vertical; min-height: 56px; font: inherit; line-height: 1.45; }
  .lo { color: var(--faint); }

  .tabs { display: flex; gap: 0; margin-bottom: 18px; border-bottom: 1px solid var(--border-soft); }
  .tab {
    background: none; border: none; border-bottom: 2px solid transparent;
    border-radius: 0; box-shadow: none; margin-bottom: -1px;
    padding: 9px 20px; font-size: 14px; font-weight: 600; color: var(--muted); cursor: pointer;
  }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .stackpane { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .sleft, .sright { min-width: 0; }

  .savebar { display: flex; align-items: center; gap: 12px; margin: 12px 0 18px; }
  .m { font-size: 12.5px; } .ok { color: var(--good); } .err { color: var(--bad); }

  /* pipeline sections — visually de-emphasised */
  .grp { border: 1px solid var(--border-soft); border-radius: 14px; background: var(--panel); margin-bottom: 12px; overflow: hidden; }
  .grp > summary {
    list-style: none; cursor: pointer; display: flex; align-items: center; gap: 10px;
    padding: 12px 16px; font-size: 13px; font-weight: 600; color: var(--muted);
    border-left: 3px solid transparent; user-select: none;
  }
  .grp > summary::-webkit-details-marker { display: none; }
  .grp > summary::before { content: '▸'; font-size: 10px; color: var(--faint); transition: transform .15s; }
  .grp[open] > summary::before { transform: rotate(90deg); }
  .grp[open] > summary { border-left-color: var(--border); color: var(--text); }
  .grp > summary:hover { background: var(--elev); }
  .gsub-ico { font-size: 12px; color: var(--faint); }
  .gsub { font-size: 12px; font-weight: 400; color: var(--faint); }
  .gbody { padding: 0 18px 18px; }
  .card { background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px 16px; margin-bottom: 14px; }
  .card:last-child { margin-bottom: 0; }
  .card h3 { margin: 0 0 10px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; font-weight: 700; }
</style>

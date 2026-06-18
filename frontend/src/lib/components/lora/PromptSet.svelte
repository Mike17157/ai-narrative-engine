<script>
  // The prompt-set builder for LoRA training-image generation. Load/save named
  // prompt sets, fill from a theme via the Prompt Gen LLM, set variations, and
  // start a batch. The batch itself renders in GenerateCard (inline, no route).
  import Combobox from '$lib/components/Combobox.svelte';
  import { app } from '$lib/app.svelte.js';
  import {
    lora, promptList, loadSet, saveAsSet, fillFromTheme, genImages,
  } from '$lib/lora.svelte.js';

  let setItems = $derived(lora.sets.map((s) => ({ value: s, label: s })));
  let count = $derived(promptList().length);
</script>

<div class="hint">Builds a curated image set for LoRA training. Generates with the active workflow ({app.activeImage || '—'}, set under Configure).</div>

<label>Prompt set</label>
<div class="helper">
  <Combobox items={setItems} value={lora.curSet} placeholder="load a saved set…" onpick={loadSet} />
  <input bind:value={lora.newSetName} placeholder="save current as…" style="width:170px" />
  <button class="ghost sm" onclick={saveAsSet} disabled={!count}>Save set</button>
</div>

<label>Image prompts — one per line ({count})</label>
<textarea class="prompts" bind:value={lora.promptsText}
  placeholder={"cyberpunk samurai, neon armor, rainy alley, cinematic lighting\nmagical girl, celestial observatory, ethereal glow\n…"}></textarea>

<div class="helper">
  <span class="hlbl">Fill from a theme (Prompt Gen):</span>
  <input type="number" bind:value={lora.genCount} min="1" style="width:70px" title="count" />
  <input bind:value={lora.genTheme} placeholder="theme…" style="flex:1" />
  <button class="ghost sm" onclick={fillFromTheme} disabled={lora.genBusy}>Generate</button>
</div>
{#if lora.genMsg}<div class:ok={lora.genMsg.ok} class:err={lora.genMsg.err} style="font-size:12.5px;margin-top:6px">{lora.genMsg.text}</div>{/if}

<div class="row" style="margin-top:14px; align-items:flex-end">
  <div><label style="margin-top:0">Variations each</label><input type="number" bind:value={lora.variations} min="1" max="12" style="width:90px" /></div>
  <button onclick={genImages} disabled={!count || !app.activeImage}>
    Generate {count * lora.variations} images
  </button>
</div>

<style>
  .hint { font-size: 12.5px; color: var(--muted); margin-bottom: 10px; }
  textarea.prompts { width: 100%; height: 220px; resize: vertical; font: 13px/1.5 ui-monospace, monospace; }
  .helper { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
  .hlbl { font-size: 12.5px; color: var(--muted); white-space: nowrap; }
  .row { display: flex; gap: 10px; align-items: center; }
  .ok { color: var(--good); } .err { color: var(--bad); }
</style>

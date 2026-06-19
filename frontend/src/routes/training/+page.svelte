<script>
  // Training ▸ Pipeline. The LoRA-making front end: build prompt sets, generate a
  // batch of image variations, and keep the ones you want. Saved sets land in
  // Datasets (next tab) for captioning + training.
  import { img, saveTestPrompt } from '$lib/images.svelte.js';
  import { lora } from '$lib/lora.svelte.js';
  import PromptSet from '$lib/components/lora/PromptSet.svelte';
  import GenerateCard from '$lib/components/lora/GenerateCard.svelte';

  $effect(() => { saveTestPrompt(img.testPrompt); });
</script>

<div class="tprompt">
  <label for="tp">Test prompt <span class="lo">— shared with LoRA stacks / classify</span></label>
  <textarea id="tp" rows="3" bind:value={img.testPrompt} placeholder="1girl, solo, standing…"></textarea>
</div>

<PromptSet />
<div class="card"><h3>Batch</h3><GenerateCard /></div>

{#if lora.saveMsg}<div class:ok={lora.saveMsg.ok} class:err={lora.saveMsg.err} class="savemsg">{lora.saveMsg.text}</div>{/if}

<style>
  .tprompt { margin-bottom: 14px; }
  .tprompt label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 5px; }
  .tprompt textarea { width: 100%; resize: vertical; min-height: 56px; font: inherit; line-height: 1.45; }
  .lo { color: var(--faint); }

  .card { background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px 16px; margin-top: 14px; }
  .card h3 { margin: 0 0 10px; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; font-weight: 700; }
  .savemsg { font-size: 12.5px; margin-top: 12px; }
  .ok { color: var(--good); } .err { color: var(--bad); }
</style>

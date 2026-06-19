<script>
  // Training shell. Boots the LoRA pipeline store (prompt-sets + comfy choices)
  // once for the section and reattaches any in-flight batch job so progress
  // survives navigating between the Pipeline / Datasets / Trainer sub-routes.
  // DatasetCard and TrainCard each reload their own dataset list on mount, so no
  // cross-route refresh bridge is needed here. The subnav is rendered by the
  // root layout; this layout only loads data and passes children through.
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { loadSets, loadChoices, attach as attachGen } from '$lib/lora.svelte.js';

  let { children } = $props();

  onMount(async () => {
    await loadSets();
    await loadChoices();
    const j = await get('/lora/job');
    if (j && j.status && j.status !== 'idle') attachGen();
  });
</script>

<div class="page">
  <div class="col">
    {@render children()}
  </div>
</div>

<style>
  .page { flex: 1; min-height: 0; overflow: auto; }
  .col { max-width: 1100px; margin: 0 auto; padding: 20px 24px; }
</style>

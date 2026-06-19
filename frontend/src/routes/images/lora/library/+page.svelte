<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { img, saveTestPrompt } from '$lib/images.svelte.js';
  import { loraLib } from '$lib/lora-library.svelte.js';
  import GridTester from '$lib/components/lora/GridTester.svelte';

  $effect(() => { saveTestPrompt(img.testPrompt); });

  onMount(async () => {
    try { loraLib.choices = await get('/comfy/choices'); } catch { }
    try { loraLib.bases  = await get('/loras/bases'); } catch { }
    try { loraLib.scan   = await get('/comfy/models'); } catch { }
    try { loraLib.families = (await get('/families')).families || []; } catch { }
  });
</script>

<GridTester />

<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { loadSets, loadChoices, attach } from '$lib/lora.svelte.js';

  let { children } = $props();

  onMount(async () => {
    await loadSets();
    await loadChoices();
    // If a batch exists (running or finished), re-attach so it's live when shown.
    const j = await get('/lora/job');
    if (j && j.status && j.status !== 'idle') attach();
  });

  // Honor a deep-link target (e.g. Settings → "set up trainer" → Train).
  $effect(() => { if (app.nav?.loraView) { const v = app.nav.loraView; app.nav.loraView = null; goto(`/images/lora/${v}`); } });
</script>

{@render children()}

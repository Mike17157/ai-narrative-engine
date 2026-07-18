<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { createStory } from '$lib/stories.svelte.js';
  let busy = $state(false);
  let error = $state('');
  async function begin() {
    if (busy) return;
    busy = true;
    error = '';
    try {
      const key = await createStory();
      await goto(`/stories/${key}`);
    } catch (err) {
      error = err?.message || 'Could not create the story card.';
      busy = false;
    }
  }
  onMount(begin);
</script>
<div class="page"><div class="col narrow">
  <h1>Starting a story…</h1>
  {#if error}
    <p class="err">{error}</p><button onclick={begin} disabled={busy}>Try again</button>
  {:else}
    <p class="lo">Opening your story card.</p>
  {/if}
</div></div>
<style>.narrow{max-width:640px}.lo{color:var(--muted)}.err{color:var(--bad)}</style>

<script>
  import { page } from '$app/stores';
  import { stories, loadStory } from '$lib/stories.svelte.js';

  let { children } = $props();
  let key = $derived($page.params.key);
  let loading = $state(true);

  // Load the story when the key changes; reuse it across its section routes.
  $effect(() => {
    const k = key;
    if (stories.current?.key === k) { loading = false; return; }
    loading = true;
    loadStory(k).then(() => { loading = false; });
  });
</script>

{#if stories.current && stories.current.key === key}
  {@render children()}
{:else if loading}
  <div class="page"><div class="col"><p class="lo">Loading…</p></div></div>
{:else}
  <div class="page"><div class="col"><p class="lo">Story not found.</p></div></div>
{/if}

<style>.lo { color: var(--muted); font-size: 13px; }</style>

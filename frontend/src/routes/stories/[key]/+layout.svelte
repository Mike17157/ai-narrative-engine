<script>
  import { page } from '$app/stores';
  import { loadStory } from '$lib/stories.svelte.js';

  let { children } = $props();
  let key = $derived($page.params.key);

  // Load on key change inside an EFFECT (side effects belong here, not in a $derived —
  // loadStory mutates stores.current, and an impure $derived that mutates state loops
  // until the call stack overflows). The promise lives in $state and drives {#await},
  // Svelte's native async gate, so the view can't desync.
  let storyPromise = $state(Promise.resolve(null));
  let reqKey = '';
  $effect(() => {
    const k = key;
    if (!k || reqKey === k) return;
    reqKey = k;
    storyPromise = loadStory(k);
  });
</script>

{#await storyPromise}
  <div class="page"><div class="col"><p class="lo">Loading…</p></div></div>
{:then story}
  {#if story && story.key === key}
    {@render children()}
  {:else}
    <div class="page"><div class="col"><p class="lo">Story not found.</p></div></div>
  {/if}
{:catch}
  <div class="page"><div class="col"><p class="lo">Couldn’t load this story.</p></div></div>
{/await}

<style>.lo { color: var(--muted); font-size: 13px; }</style>

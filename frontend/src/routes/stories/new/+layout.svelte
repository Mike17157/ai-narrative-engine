<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { stories } from '$lib/stories.svelte.js';

  let { children } = $props();
  let wz = $derived(stories.wizard);

  // Wizard steps are now folder-tree leaves (under "New story"); the in-page step
  // strip is gone. Keep the progress guard: deep-linking past your draft's progress
  // bounces to the furthest reached step.
  const reach = $derived({
    setup: true,
    storyboard: !!wz.board || wz.streaming,
    scenes: !!wz.locations,
    characters: wz.cast !== null && wz.cast !== undefined
  });
  let step = $derived($page.url.pathname.split('/')[3] || 'setup');  // /stories/new/<step>

  $effect(() => {
    if (!step) return;
    if (!reach[step]) {
      const furthest = reach.characters ? 'characters' : reach.scenes ? 'scenes' : reach.storyboard ? 'storyboard' : 'setup';
      goto(`/stories/new/${furthest}`, { replaceState: true });
    }
  });
</script>

{@render children()}

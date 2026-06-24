<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { stories } from '$lib/stories.svelte.js';

  let { children } = $props();
  let wz = $derived(stories.wizard);

  // Lean wizard: Premise → Characters → Outfits → Scenes. NON-LINEAR — a step is reachable
  // once a cast exists; Premise is always reachable. A step with no cast falls back to Premise.
  const reach = $derived({
    premise: true,
    characters: !!wz.castKeys?.length,
    outfits: !!wz.castKeys?.length,
    scenes: !!wz.castKeys?.length,
  });
  let step = $derived($page.url.pathname.split('/')[3] || 'premise');  // /stories/new/<step>

  $effect(() => {
    if (!step) return;
    if (reach[step] === false) goto('/stories/new/premise', { replaceState: true });
  });
</script>

{@render children()}

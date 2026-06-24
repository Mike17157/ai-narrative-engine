<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { stories } from '$lib/stories.svelte.js';

  let { children } = $props();
  let wz = $derived(stories.wizard);

  // Wizard steps are folder-tree leaves (under "New story"). The wizard is NON-LINEAR:
  // a step is reachable if its own INPUT exists (so you can jump back and re-run any
  // step). The overview hub and the always-available setup/spine steps are exempt; a
  // step whose prerequisite is genuinely absent falls back to setup.
  const reach = $derived({
    overview: true,
    setup: true,
    spine: true,                                       // generates from just a character
    storyboard: !!wz.board || !!wz.spine || wz.streaming,
    scenes: !!wz.locations || !!wz.board,              // can re-extract from the board
    characters: (wz.cast !== null && wz.cast !== undefined) || !!wz.board
  });
  let step = $derived($page.url.pathname.split('/')[3] || 'setup');  // /stories/new/<step>

  $effect(() => {
    if (!step) return;
    if (reach[step] === false) goto('/stories/new/setup', { replaceState: true });
  });
</script>

{@render children()}

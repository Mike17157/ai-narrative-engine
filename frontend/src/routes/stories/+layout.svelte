<script>
  import { onMount } from 'svelte';
  import { stories, loadStories, loadModels } from '$lib/stories.svelte.js';
  import { loadChars } from '$lib/characters.svelte.js';
  import { isStoryHostDesktop } from '$lib/story-host-client';

  let { children } = $props();
  const leanStoryMode = import.meta.env.VITE_LEAN_STORY === '1';
  // The subnav (Library / wizard steps / story picker) is rendered by the root
  // layout's horizontal bar from storiesTree() (lib/nav.svelte.js).
  // Story-generation config lives in Settings; the wizard is launched from the
  // Library. This layout only loads data.
  onMount(() => {
    loadStories();
    if (!leanStoryMode && !isStoryHostDesktop()) loadChars();
    // The lean app publishes the Story Agent's safe model card through the
    // Architect contract, not the full global models/configuration surface.
    if (!leanStoryMode && !isStoryHostDesktop()) loadModels();
  });
</script>

{@render children()}

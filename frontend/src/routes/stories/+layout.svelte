<script>
  import { onMount } from 'svelte';
  import { stories, loadStories, loadModels } from '$lib/stories.svelte.js';
  import { loadChars } from '$lib/characters.svelte.js';

  let { children } = $props();
  // The subnav (Library / wizard steps / story picker) is rendered by the root
  // layout's horizontal bar from storiesTree() (lib/nav.svelte.js).
  // Story-generation config lives in Settings; the wizard is launched from the
  // Library. This layout only loads data.
  onMount(() => { loadStories(); loadChars(); loadModels(); });
</script>

{@render children()}

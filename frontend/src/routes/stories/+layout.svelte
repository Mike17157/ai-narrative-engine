<script>
  import { onMount } from 'svelte';
  import { stories, loadStories, loadModels } from '$lib/stories.svelte.js';
  import { loadChars } from '$lib/characters.svelte.js';

  let { children } = $props();
  // The folder tree (Library + each open story's sections) is rendered by the root
  // layout's TreeNav. Story-generation config lives in Settings; the wizard is launched
  // from the Library. This layout only loads data.
  onMount(() => { loadStories(); loadChars(); loadModels(); });
</script>

{@render children()}

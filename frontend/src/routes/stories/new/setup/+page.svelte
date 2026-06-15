<script>
  import { chars } from '$lib/characters.svelte.js';
  import StoryWizard from '$lib/components/StoryWizard.svelte';
  let charItems = $derived(chars.list.filter((c) => !c.story).map((c) => ({ value: c.key, label: c.name || c.key })));
</script>

<div class="page"><div class="col"><StoryWizard step="setup" {charItems} /></div></div>

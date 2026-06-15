<script>
  import { onMount } from 'svelte';
  import { chars } from '$lib/characters.svelte.js';
  import { stories, editStory } from '$lib/stories.svelte.js';
  import StoryEditor from '$lib/components/StoryEditor.svelte';

  let charItems = $derived(chars.list.filter((c) => !c.story).map((c) => ({ value: c.key, label: c.name || c.key })));
  onMount(() => { editStory(); });   // clone the loaded story into the edit buffer
</script>

<div class="page"><div class="col"><StoryEditor {charItems} /></div></div>

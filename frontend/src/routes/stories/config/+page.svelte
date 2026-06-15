<script>
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import StoryConfig from '$lib/components/StoryConfig.svelte';

  let charItems = $derived(chars.list.map((c) => ({ value: c.key, label: c.name || c.key })));
  let modelItems = $derived(stories.textModels.map((m) => ({ value: m.id, label: m.name || m.id, vision: !!m.vision })));
</script>

<div class="page"><div class="col"><StoryConfig {charItems} {modelItems} /></div></div>

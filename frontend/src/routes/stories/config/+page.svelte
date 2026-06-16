<script>
  import { page } from '$app/state';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import StoryConfig from '$lib/components/StoryConfig.svelte';
  import ImageRoleConfig from '$lib/components/ImageRoleConfig.svelte';

  let charItems = $derived(chars.list.map((c) => ({ value: c.key, label: c.name || c.key })));
  let modelItems = $derived(stories.textModels.map((m) => ({ value: m.id, label: m.name || m.id, vision: !!m.vision })));

  // Imaging sections pick a ComfyUI image WORKFLOW (image_roles.json); the rest are text
  // generation stages (story_builder.json). The side menu sets ?section.
  const IMAGE_SECTIONS = new Set(['base', 'style', 'sprite', 'scene', 'chat']);
  let section = $derived(page.url.searchParams.get('section') || 'storyboard');
</script>

<div class="page"><div class="col">
  {#if IMAGE_SECTIONS.has(section)}
    <ImageRoleConfig role={section} />
  {:else}
    <StoryConfig {section} {charItems} {modelItems} />
  {/if}
</div></div>

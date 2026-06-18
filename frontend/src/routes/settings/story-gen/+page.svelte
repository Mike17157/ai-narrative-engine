<script>
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import StoryConfig from '$lib/components/StoryConfig.svelte';
  import ImageRoleConfig from '$lib/components/ImageRoleConfig.svelte';

  // Relocated from /stories/config. This is GLOBAL config (story_builder.json +
  // image_roles.json), not per-story — so it lives under Settings. Imaging sections
  // pick a ComfyUI image workflow (image_roles.json, the former Images ▸ Roles too);
  // the rest are text generation stages (story_builder.json).
  onMount(() => { if (!stories.textModels.length) stories.loadModels?.(); });

  const IMAGE_SECTIONS = new Set(['base', 'style', 'sprite', 'scene', 'chat']);
  let section = $derived(page.url.searchParams.get('section') || 'storyboard');

  let charItems = $derived(chars.list.map((c) => ({ value: c.key, label: c.name || c.key })));
  let modelItems = $derived(stories.textModels.map((m) => ({ value: m.id, label: m.name || m.id, vision: !!m.vision })));
</script>

<div class="pane">
  {#if IMAGE_SECTIONS.has(section)}
    <ImageRoleConfig role={section} />
  {:else}
    <StoryConfig {section} {charItems} {modelItems} />
  {/if}
</div>

<style>
  .pane {
    flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: auto;
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg);
    padding: 18px; margin: 18px 20px 18px 4px;
  }
  .pane :global(label) { text-transform: none; letter-spacing: 0; font-size: 12px; }
</style>

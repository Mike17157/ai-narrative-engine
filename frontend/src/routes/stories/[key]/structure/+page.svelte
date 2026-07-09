<script>
  // The one Author surface for a story — the full editor (document · plot · relationships · map).
  // Folded in from the retired Overview page (see [[overview-is-editor]]). "From scratch" is the same
  // surface, just starting empty: POST /stories/new mints a blank story and you build it by
  // conversation here (the old genesis wizard was removed).
  import StoryWorkspace from '$lib/components/story/StoryWorkspace.svelte';
</script>

<StoryWorkspace />

<script>
  // Backward-compatible adapter: existing callers can keep handing the
  // control graph to this component, while newer callers can add the public
  // `story` card for richer character, arc, and scene kernels.
  import StoryAtlas from './StoryAtlas.svelte';

  let {
    graph = null,
    story = null,
    kernels = null,
    selected = '',
    compact = $bindable(true),
    onselect = null
  } = $props();
</script>

<StoryAtlas {story} {graph} {kernels} {selected} bind:compact {onselect} />

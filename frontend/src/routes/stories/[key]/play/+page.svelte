<script>
  import { stories } from '$lib/stories.svelte.js';
  import Player from '$lib/components/Player.svelte';
  let st = $derived(stories.current);
</script>

<div class="page"><div class="col wide"><Player storyKey={st.key} /></div></div>

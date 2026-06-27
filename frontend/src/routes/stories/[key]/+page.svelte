<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // /stories/[key] has no content of its own — land on Overview, the main story
  // page + edit-in-place editor (see [[overview-is-editor]]).
  onMount(() => goto(`/stories/${$page.params.key}/overview`, { replaceState: true }));
</script>

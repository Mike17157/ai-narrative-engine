<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // /stories/[key] has no content of its own — land on Iterate, the primary surface.
  onMount(() => goto(`/stories/${$page.params.key}/workshop`, { replaceState: true }));
</script>

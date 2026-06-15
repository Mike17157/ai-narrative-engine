<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // /stories/[key] has no content of its own — land on the overview.
  onMount(() => goto(`/stories/${$page.params.key}/overview`, { replaceState: true }));
</script>

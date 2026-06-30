<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // Overview is RETIRED — its full editor moved into the Structure surface (one Author surface).
  // Kept as a redirect so old links/bookmarks still land. See [[overview-is-editor]].
  onMount(() => goto(`/stories/${$page.params.key}/structure`, { replaceState: true }));
</script>

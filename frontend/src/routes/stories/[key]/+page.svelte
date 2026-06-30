<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // /stories/[key] has no content of its own — land on Structure, the one Author surface
  // (the full editor; the Overview route was retired). See [[overview-is-editor]].
  onMount(() => goto(`/stories/${$page.params.key}/structure`, { replaceState: true }));
</script>

<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  // Legacy overview bookmark: the unified Story Card now owns authoring.
  onMount(() => goto(`/stories/${$page.params.key}`, { replaceState: true }));
</script>

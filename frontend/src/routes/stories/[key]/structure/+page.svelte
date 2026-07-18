<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';

  // Legacy editor bookmark: the Story Card is now the one authoring home.
  onMount(() => goto(`/stories/${$page.params.key}`, { replaceState: true }));
</script>

<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/stories/new/premise', { replaceState: true }));
</script>

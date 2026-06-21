<script>
  // Library hub index — land on Presets.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/library/presets', { replaceState: true }));
</script>

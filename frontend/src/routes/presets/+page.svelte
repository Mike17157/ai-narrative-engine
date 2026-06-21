<script>
  // Moved into the Library hub. Redirect old links/bookmarks.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  onMount(() => goto('/library/presets', { replaceState: true }));
</script>

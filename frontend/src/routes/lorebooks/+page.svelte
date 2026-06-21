<script>
  // Moved into the Library hub. Redirect old links/bookmarks, preserving query (?rating, ?import).
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  onMount(() => goto('/library/lorebooks' + (page.url.search || ''), { replaceState: true }));
</script>

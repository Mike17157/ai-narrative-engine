<script>
  // Settings shell. The subnav (System / Connections / Models / Story-gen) is
  // rendered by the root layout's horizontal bar from settingsTree()
  // (lib/nav.svelte.js); this layout just passes children through.
  let { children } = $props();
</script>

{@render children()}

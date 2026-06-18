<script>
  // The LoRA section is a single surface (the Library page); there are no
  // sub-routes to share state across anymore. The Library page owns the loads
  // + job reattach, so this layout is now a plain passthrough.
  let { children } = $props();
</script>

{@render children()}

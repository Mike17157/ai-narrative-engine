<script>
  // Header dropdown shell: an absolutely-positioned panel anchored under a header
  // button (top:42px / right:0), with the shared chrome + drop-in animation. The
  // host supplies the contents; this owns the box. Used by DetailsMenu/ActivityMenu.
  let { width = '300px', children } = $props();
</script>

<div class="menu" role="menu" style="width:{width}" onclick={(e) => e.stopPropagation()}>
  {@render children?.()}
</div>

<style>
  .menu {
    position: absolute; top: 42px; right: 0; z-index: 60;
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, .5); padding: 8px; animation: drop .12s ease;
  }
  @keyframes drop { from { opacity: 0; transform: translateY(-6px); } }
</style>

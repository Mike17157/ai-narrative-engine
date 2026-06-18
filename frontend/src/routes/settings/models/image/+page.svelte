<script>
  import ImageModelPanel from '$lib/components/ImageModelPanel.svelte';
</script>

<div class="pane"><ImageModelPanel /></div>

<style>
  .pane {
    flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: auto;
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg);
    padding: 18px; margin: 18px 20px 18px 4px;
  }
  .pane :global(label) { text-transform: none; letter-spacing: 0; font-size: 12px; flex: none; }
  .pane :global(.hint) { flex: none; }
</style>

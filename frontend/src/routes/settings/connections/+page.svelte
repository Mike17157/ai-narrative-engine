<script>
  // Settings ▸ Connections. The single home for credentials/profiles: the
  // language API (chat/story text) and the image backend (ComfyUI/RunPod). This
  // is the source of truth for *which connection is active*; the Models page
  // picks *which model/workflow* to use off an active connection.
  import ConnectionPanel from '$lib/components/ConnectionPanel.svelte';
</script>

<div class="screen">
  <section class="card">
    <div class="card-head">
      <h3>Language API</h3>
      <span class="card-sub">chat · story generation · all text work</span>
    </div>
    <ConnectionPanel kind="text" />
  </section>

  <section class="card">
    <div class="card-head">
      <h3>Image backend</h3>
      <span class="card-sub">ComfyUI · RunPod</span>
    </div>
    <ConnectionPanel kind="image" />
  </section>
</div>

<style>
  .screen { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; padding: 18px 20px 18px 4px; }
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 20px 22px; display: flex; flex-direction: column; }
  .card-head { display: flex; align-items: baseline; gap: 10px; padding-bottom: 14px; border-bottom: 1px solid var(--border-soft); margin-bottom: 14px; }
  h3 { margin: 0; font-size: 14px; font-weight: 660; color: var(--text); }
  .card-sub { font-size: 11.5px; color: var(--muted); }
  .card :global(label) { text-transform: none; letter-spacing: 0; font-size: 12px; }
</style>

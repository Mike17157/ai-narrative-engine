<script>
  import { app } from '$lib/app.svelte.js';
  import DropdownMenu from '$lib/components/shared/DropdownMenu.svelte';

  let textModel = $derived(app.health?.active_chat_model || app.conns?.active?.text || '—');
  let imageModel = $derived(app.health?.active_image_model || app.activeImage || '—');
</script>

<DropdownMenu width="300px">
  <div class="mhd">Active models</div>
  <div class="row">
    <span class="lbl">TextGen</span>
    <span class="val" title={textModel}>{textModel}</span>
  </div>
  <div class="row">
    <span class="lbl">ImageGen</span>
    <span class="val" title={imageModel}>{imageModel}</span>
  </div>
</DropdownMenu>

<style>
  .mhd { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 6px 8px 8px; }
  .row { display: flex; align-items: baseline; gap: 12px; padding: 7px 8px; }
  .row + .row { border-top: 1px solid var(--border-soft); }
  .lbl { font-size: 12px; color: var(--muted); width: 64px; flex: none; }
  .val { font-size: 12.5px; color: var(--text); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
</style>

<script>
  import { app } from '../app.svelte.js';

  let textModel = $derived(app.health?.active_chat_model || app.conns?.active?.text || '—');
  let imageModel = $derived(app.health?.active_image_model || app.activeImage || '—');
  let imgPrompt = $derived(app.health?.promptgen_model || '(uses chat model)');
</script>

<div class="menu" role="menu" onclick={(e) => e.stopPropagation()}>
  <div class="mhd">Active models</div>
  <div class="row">
    <span class="lbl">TextGen</span>
    <span class="val" title={textModel}>{textModel}</span>
  </div>
  <div class="row">
    <span class="lbl">ImgPrompt</span>
    <span class="val" title={imgPrompt}>{imgPrompt}</span>
  </div>
  <div class="row">
    <span class="lbl">ImageGen</span>
    <span class="val" title={imageModel}>{imageModel}</span>
  </div>
</div>

<style>
  .menu {
    position: absolute; top: 42px; right: 0; z-index: 60; width: 300px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: 0 16px 40px rgba(0, 0, 0, .5); padding: 8px; animation: drop .12s ease;
  }
  @keyframes drop { from { opacity: 0; transform: translateY(-6px); } }
  .mhd { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 6px 8px 8px; }
  .row { display: flex; align-items: baseline; gap: 12px; padding: 7px 8px; }
  .row + .row { border-top: 1px solid var(--border-soft); }
  .lbl { font-size: 12px; color: var(--muted); width: 64px; flex: none; }
  .val { font-size: 12.5px; color: var(--text); min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
</style>

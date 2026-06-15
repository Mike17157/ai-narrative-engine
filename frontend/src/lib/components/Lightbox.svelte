<script>
  import { lightbox, closeLightbox } from '$lib/lightbox.svelte.js';
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && closeLightbox()} />

{#if lightbox.src}
  <div class="lightbox" role="presentation" onclick={closeLightbox}>
    <figure>
      <img src={lightbox.src} alt="" />
      {#if lightbox.cap}<figcaption>{lightbox.cap}</figcaption>{/if}
    </figure>
    <div class="lhint">click anywhere or press Esc to close</div>
  </div>
{/if}

<style>
  .lightbox {
    position: fixed; inset: 0; z-index: 80; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 14px; padding: 4vh 4vw; cursor: zoom-out;
    background: rgba(6, 8, 12, .82); backdrop-filter: blur(10px) saturate(1.1);
    animation: lbfade .16s ease;
  }
  .lightbox figure { margin: 0; max-width: 100%; max-height: 84vh; display: flex; flex-direction: column; align-items: center; gap: 12px; animation: lbpop .2s cubic-bezier(.2, .9, .3, 1.2); }
  .lightbox img {
    max-width: 100%; max-height: 78vh; object-fit: contain; border-radius: 14px;
    box-shadow: 0 30px 80px rgba(0, 0, 0, .6), 0 0 0 1px rgba(255, 255, 255, .06);
  }
  .lightbox figcaption {
    font: 12.5px/1.5 ui-monospace, monospace; color: #cdd6e4; text-align: center;
    background: rgba(20, 24, 34, .7); border: 1px solid var(--border); border-radius: 999px;
    padding: 6px 14px; max-width: 80ch; backdrop-filter: blur(4px);
  }
  .lhint { font-size: 12px; color: rgba(205, 214, 228, .55); letter-spacing: .2px; }
  @keyframes lbfade { from { opacity: 0; } }
  @keyframes lbpop { from { opacity: 0; transform: scale(.94); } }
</style>

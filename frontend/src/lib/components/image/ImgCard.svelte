<script>
  // Standardised image slot: fills its container, hover shows ⤢ expand + ↻ regen buttons.
  // When src is absent (not yet generated), shows a neutral placeholder and a prominent regen
  // button so the user can kick off generation immediately.
  // The parent element defines size/aspect-ratio; this component just fills it.
  import { openLightbox } from '$lib/lightbox.svelte.js';
  let { src = null, caption = '', prompt = '', onRegen = null, busy = false } = $props();
</script>

<div class="ic" class:busy class:empty={!src && !busy}>
  {#if busy}
    <div class="ic-spin"><span class="spin" aria-hidden="true"></span></div>
  {:else if src}
    <img class="ic-img" {src} alt={caption} onclick={() => openLightbox(src, caption)} />
  {:else}
    <div class="ic-ph"></div>
  {/if}

  <div class="ic-btns" class:prominent={!src && !busy && !!onRegen}>
    {#if src && !busy}
      <button class="ic-b" title="Expand" onclick={(e) => { e.stopPropagation(); openLightbox(src, caption); }}>⤢</button>
    {/if}
    {#if onRegen}
      <button class="ic-b ic-regen" title={prompt || 'Regenerate'} onclick={onRegen} disabled={busy}>↻</button>
    {/if}
  </div>
</div>

<style>
  .ic { position: relative; width: 100%; height: 100%; border-radius: inherit; overflow: hidden; }
  .ic-img { width: 100%; height: 100%; object-fit: cover; display: block; cursor: zoom-in; }
  .ic-ph { width: 100%; height: 100%; background: var(--elev); }
  .ic-spin { width: 100%; height: 100%; display: grid; place-items: center; background: var(--elev); }

  /* Buttons: always in the corner, reveal on parent hover */
  .ic-btns {
    position: absolute; bottom: 4px; right: 4px;
    display: flex; gap: 3px;
    opacity: 0; transition: opacity .12s;
  }
  .ic:hover .ic-btns { opacity: 1; }

  /* When the slot is empty, show the regen button centred and always-visible */
  .ic-btns.prominent {
    inset: 0; bottom: unset; right: unset;
    display: grid; place-items: center;
    opacity: 1;
  }

  .ic-b {
    width: 24px; height: 24px; padding: 0; border-radius: 6px; font-size: 12px; line-height: 1;
    background: rgba(10, 12, 20, .72); border: 1px solid rgba(255,255,255,.14); color: #fff;
    backdrop-filter: blur(4px); cursor: pointer;
    display: grid; place-items: center;
  }
  .ic-b:hover:not(:disabled) { background: rgba(30, 38, 60, .92); }
  .ic-b:disabled { opacity: .45; cursor: not-allowed; }

  /* Prominent (empty-slot) regen button is slightly larger */
  .ic-btns.prominent .ic-regen { width: 32px; height: 32px; font-size: 15px; }
</style>

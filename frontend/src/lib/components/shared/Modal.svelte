<script>
  // Shared modal shell: owns the backdrop, the dialog box chrome (panel / border /
  // radius / shadow), the fade+pop animations, backdrop-click-to-close and the
  // Escape key. Each modal supplies only its own inner markup via the default
  // snippet — this replaces ~15 lines of copy-pasted boilerplate (and the divergent
  // z-index / backdrop colours / missing-Escape bugs) that used to live in every one.
  //
  //   <Modal {onClose} width="520px">…padded content…</Modal>           // simple dialog
  //   <Modal {onClose} flush maxHeight="90vh">…own .dhead/.body…</Modal>  // framed/scrollable
  let {
    open = true,
    onClose = null,
    width = '520px',          // max dialog width
    maxHeight = null,         // cap; box grows to content then scrolls (use with flush)
    height = null,            // FIXED height; box stays this tall regardless of content
                              // (e.g. tabbed modals, so switching panes doesn't resize it)
    flush = false,            // true → no padding, flex-column (modal owns its own header/body)
    closeOnBackdrop = true,
    zIndex = 200,
    children,
  } = $props();

  function backdrop() { if (closeOnBackdrop) onClose?.(); }
</script>

<svelte:window onkeydown={(e) => { if (open && e.key === 'Escape') onClose?.(); }} />

{#if open}
  <div class="overlay" style="z-index:{zIndex}" onclick={backdrop} role="presentation">
    <div
      class="dlg" class:flush class:fixed={!!height}
      style="--mw:{width}{(height || maxHeight) ? `;--mh:${height || maxHeight}` : ''}"
      role="dialog" aria-modal="true"
      onclick={(e) => e.stopPropagation()}
    >
      {@render children?.()}
    </div>
  </div>
{/if}

<style>
  .overlay {
    position: fixed; inset: 0;
    background: rgba(6, 8, 12, .62);
    display: grid; place-items: center;
    padding: 24px; backdrop-filter: blur(2px);
    animation: fade .12s ease;
  }
  .dlg {
    width: min(92vw, var(--mw, 520px));
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-lg, 14px);
    box-shadow: var(--shadow, 0 18px 50px rgba(0, 0, 0, .55));
    padding: 18px 18px 16px;
    animation: pop .13s ease;
  }
  /* Framed modals manage their own header/body/scroll — give them a flex column
     capped at --mh and clip the corners. */
  .dlg.flush {
    padding: 0;
    max-height: var(--mh, 90vh);
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  /* Fixed-height: stay this tall no matter which pane/tab is showing. */
  .dlg.flush.fixed {
    max-height: none;
    height: var(--mh, 90vh);
  }
  /* Entrance is opacity-only on purpose: an animated transform on the dialog can
     stick at its start frame when composited over the overlay's backdrop-filter,
     leaving the box shifted/scaled and throwing click targets off by a few px. */
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; } }
</style>

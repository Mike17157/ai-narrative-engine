<script>
  // Header label for a parallel timeline.
  // vertical=true → bar at top, text below (column header in top→bottom layout)
  // vertical=false → bar on left, text to right (row header in left→right layout)
  let { data } = $props();
  const vertical = data.vertical ?? true;
</script>

<div class="tl-head" class:vertical style={`--tl-color:${data.color};--tl-bg:${data.bg}`}>
  <div class="tl-bar"></div>
  <div class="tl-text">
    <span class="tl-name">{data.name || 'Timeline'}</span>
    {#if data.premise}
      <span class="tl-premise">{data.premise}</span>
    {/if}
  </div>
</div>

<style>
  .tl-head {
    width: 100%; height: 100%;
    box-sizing: border-box;
    background: var(--tl-bg, rgba(109,180,255,.07));
    border: 1px solid color-mix(in srgb, var(--tl-color) 30%, transparent);
    border-radius: 9px;
    overflow: hidden;
  }

  /* ── Vertical (column header, bar at top) ── */
  .tl-head.vertical {
    display: flex; flex-direction: column; align-items: stretch;
  }
  .tl-head.vertical .tl-bar {
    height: 4px; width: 100%;
    background: var(--tl-color, #6db4ff);
    flex: none;
  }
  .tl-head.vertical .tl-text {
    flex: 1; padding: 8px 10px;
    display: flex; flex-direction: column; justify-content: center; gap: 4px;
  }

  /* ── Horizontal (row header, bar on left — legacy) ── */
  .tl-head:not(.vertical) {
    display: flex; align-items: stretch;
    border-right: none;
    border-radius: 9px 0 0 9px;
  }
  .tl-head:not(.vertical) .tl-bar {
    width: 4px; flex: none;
    background: var(--tl-color, #6db4ff);
    border-radius: 9px 0 0 9px;
  }
  .tl-head:not(.vertical) .tl-text {
    flex: 1; min-width: 0;
    padding: 10px 9px;
    display: flex; flex-direction: column; justify-content: center; gap: 5px;
  }

  .tl-name {
    font-size: 11px; font-weight: 800;
    text-transform: uppercase; letter-spacing: .5px;
    color: var(--tl-color, #6db4ff);
    line-height: 1.2;
  }
  .tl-premise {
    font-size: 10.5px; color: var(--muted, #8a92b0);
    font-style: italic; line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
    overflow: hidden;
  }
</style>

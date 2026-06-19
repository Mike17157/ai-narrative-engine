<script>
  // The lane container behind an arc's beats on the whole-story canvas. Shows the
  // arc name + dramatic-function; clicking it (via Svelte Flow node-click) opens
  // the arc-details modal. Non-interactive otherwise — no handles, not draggable.
  let { data, width, height } = $props();
  // xyflow passes width/height only after DOM measurement; use ELK-computed
  // dimensions from data as the reliable source so the lane renders on first frame.
  let w = $derived(data.laneW ?? width ?? 300);
  let h = $derived(data.laneH ?? height ?? 120);
</script>

<div class="lane" class:empty={data.empty} style={`width:${w}px;height:${h}px`}>
  <div class="lane-head">
    <span class="lane-name">{data.name}</span>
    {#if data.dramatic_function}<span class="df">{data.dramatic_function}</span>{/if}
  </div>
  {#if data.empty}
    <div class="lane-empty">Not expanded yet — switch to List view to expand</div>
  {/if}
</div>

<style>
  .lane {
    box-sizing: border-box;
    border: 1px dashed var(--border, rgba(255,255,255,.12));
    border-radius: 14px;
    background: rgba(255,255,255,.015);
    padding: 8px 12px;
    cursor: pointer;
  }
  .lane:hover { border-color: var(--accent, #6d8cff); background: rgba(109,140,255,.04); }
  .lane.empty { display: flex; flex-direction: column; }
  .lane-head { display: flex; align-items: center; gap: 8px; }
  .lane-name { font-size: 12.5px; font-weight: 700; color: var(--text, #e0e4f0); }
  .df {
    font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
    background: rgba(109,140,255,.14); border: 1px solid rgba(109,140,255,.28); color: var(--accent, #6d8cff);
  }
  .lane-empty { flex: 1; display: grid; place-items: center; color: var(--faint, #555b78); font-size: 12px; }
</style>

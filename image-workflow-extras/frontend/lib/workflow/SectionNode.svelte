<script>
  import { Handle, Position } from '@xyflow/svelte';
  import { typeColor } from '$lib/workflow_graph.js';

  let { data } = $props();

  // SH/SR must stay in sync with the constants in workflow_graph.js toSectionGraph.
  // Handles are placed directly on the component root (no intermediate position:relative
  // wrapper) so that `top` is relative to the SvelteFlow node container, matching ELK ports.
  const SH = 36; // header height
  const SR = 30; // row height
  function hy(i) { return SH + i * SR + SR / 2; } // handle y from node top
</script>

<!-- Handles MUST be at the component root level — not inside any child div that
     has position:relative — otherwise their top is measured from the child, not
     the node container, and they drift one header-height below the ELK port. -->
{#each data.inputs as inp, i}
  <Handle
    type="target"
    position={Position.Left}
    id={inp.handleId}
    style="top:{hy(i)}px; background:{typeColor(inp.type)}; width:10px; height:10px; border:2px solid var(--bg);"
  />
{/each}
{#each data.outputs as out, i}
  <Handle
    type="source"
    position={Position.Right}
    id={out.handleId}
    style="top:{hy(i)}px; background:{typeColor(out.type)}; width:10px; height:10px; border:2px solid var(--bg);"
  />
{/each}

<div class="sn" style="--col:{data.color}">
  <div class="sh">
    <span class="dot"></span>
    {data.name}
  </div>

  <!-- Row grid: input label on left, output label on right -->
  {#each Array(Math.max(data.inputs.length, data.outputs.length, 1)) as _, i}
    <div class="row" style="height:{SR}px">
      <span class="lbl" style="color:{typeColor(data.inputs[i]?.type ?? '')}">{data.inputs[i]?.label ?? ''}</span>
      <span class="lbl r" style="color:{typeColor(data.outputs[i]?.type ?? '')}">{data.outputs[i]?.label ?? ''}</span>
    </div>
  {/each}
</div>

<style>
  /* No position:relative here — let the SvelteFlow wrapper be the Handle anchor */
  .sn {
    background: var(--panel);
    border: 1.5px solid var(--col);
    border-radius: 10px;
    overflow: hidden;
    min-width: 200px;
  }
  .sh {
    display: flex; align-items: center; gap: 8px;
    padding: 0 12px; height: 36px;
    background: color-mix(in srgb, var(--col) 22%, var(--elev));
    border-bottom: 1px solid color-mix(in srgb, var(--col) 40%, transparent);
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px; color: var(--text);
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--col); flex: none; }
  .row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 18px; gap: 8px;
  }
  .lbl {
    font-size: 11px; font-family: ui-monospace, monospace; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 48%;
  }
  .lbl.r { text-align: right; }
</style>

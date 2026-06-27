<script>
  // A map-graph node: a location (or an AREA that contains locations). Parent→child edges run
  // top→bottom. data._onClick(data) optional.
  import { Handle, Position } from '@xyflow/svelte';
  let { data } = $props();
  const H = 'opacity:0;border:0;width:1px;height:1px;min-width:0;min-height:0;';
</script>

<div class="lnode" class:area={data.isArea} class:start={data.start} onclick={() => data._onClick?.(data)}>
  <Handle type="target" position={Position.Top} id="t" style={H} />
  <Handle type="source" position={Position.Bottom} id="b" style={H} />
  {#if data.bg}<img class="lbg" src={data.bg} alt="" />{/if}
  <span class="dot"></span>
  <span class="lname">{data.name}</span>
  {#if data.isArea}<span class="abadge">area</span>{/if}
</div>

<style>
  .lnode {
    width: 168px; box-sizing: border-box; cursor: default; position: relative;
    background: var(--panel, #1a1d27); border: 1px solid var(--border-soft, rgba(255,255,255,.1));
    border-radius: 9px; padding: 9px 11px; display: flex; align-items: center; gap: 8px;
    box-shadow: 0 2px 10px rgba(0,0,0,.35); overflow: hidden;
  }
  .lnode.area { border-color: color-mix(in srgb, var(--accent, #6d8cff) 45%, transparent); font-weight: 700; }
  .lnode.start { box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #6d8cff) 40%, transparent); }
  .lbg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; opacity: .22; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--accent, #6d8cff); flex: none; position: relative; }
  .lname { font-size: 12.5px; color: var(--text, #e0e4f0); position: relative;
           overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .abadge { margin-left: auto; font-size: 9px; text-transform: uppercase; letter-spacing: .4px;
            color: var(--accent, #6d8cff); position: relative; }
</style>

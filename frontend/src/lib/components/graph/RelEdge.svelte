<script>
  // A FLOATING relationship edge (the Svelte Flow node-to-node pattern): instead of attaching to a
  // fixed handle, it roots at the point where the center-to-center line crosses each card's boundary
  // — so the line starts at one card's edge and the ARROW lands on the other card's edge. Reciprocal
  // bonds (A→B + B→A) bow to opposite sides (same `data.bow` sign — the reversed direction flips the
  // perpendicular) with labels set apart along each edge. See [[overview-is-editor]].
  import { BaseEdge, EdgeLabel, useInternalNode } from '@xyflow/svelte';

  let { id, source, target, markerEnd, style, data, label } = $props();
  const sN = useInternalNode(source);
  const tN = useInternalNode(target);

  // Where the ray from `node` centre toward (ox,oy) exits node's rectangle.
  function boundary(node, ox, oy) {
    const w = node.measured?.width || 132, h = node.measured?.height || 120;
    const cx = node.internals.positionAbsolute.x + w / 2;
    const cy = node.internals.positionAbsolute.y + h / 2;
    const dx = ox - cx, dy = oy - cy;
    if (!dx && !dy) return { x: cx, y: cy };
    const s = Math.min(dx ? (w / 2) / Math.abs(dx) : Infinity, dy ? (h / 2) / Math.abs(dy) : Infinity);
    return { x: cx + dx * s, y: cy + dy * s };
  }
  const centre = (n) => ({
    x: n.internals.positionAbsolute.x + (n.measured?.width || 132) / 2,
    y: n.internals.positionAbsolute.y + (n.measured?.height || 120) / 2,
  });

  let geom = $derived.by(() => {
    const s = sN.current, t = tN.current;
    if (!s || !t) return null;
    const sc = centre(s), tc = centre(t);
    const sp = boundary(s, tc.x, tc.y);    // leave source card toward target
    const tp = boundary(t, sc.x, sc.y);    // arrive at target card edge
    const dx = tp.x - sp.x, dy = tp.y - sp.y, len = Math.hypot(dx, dy) || 1;
    const off = (data?.bow || 0) * 80;
    const mx = (sp.x + tp.x) / 2 + (-dy / len) * off;
    const my = (sp.y + tp.y) / 2 + (dx / len) * off;
    const lt = 0.38;                        // label ~38% along (reciprocals → different spots)
    return {
      path: `M ${sp.x},${sp.y} Q ${mx},${my} ${tp.x},${tp.y}`,
      lx: sp.x + (tp.x - sp.x) * lt + (-dy / len) * off * 0.5,
      ly: sp.y + (tp.y - sp.y) * lt + (dx / len) * off * 0.5,
    };
  });
</script>

{#if geom}
  <BaseEdge {id} path={geom.path} {markerEnd} {style} />
  {#if label}
    <EdgeLabel x={geom.lx} y={geom.ly} transparent>
      <div class="rel-elabel">{label}</div>
    </EdgeLabel>
  {/if}
{/if}

<style>
  .rel-elabel {
    pointer-events: none; font-size: 10.5px; line-height: 1; white-space: nowrap;
    padding: 3px 8px; border-radius: 7px;
    background: rgba(18,21,30,.92); color: rgba(232,234,242,.95);
    border: 1px solid rgba(255,255,255,.1);
  }
</style>

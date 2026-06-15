<script>
  import { BaseEdge } from '@xyflow/svelte';

  // Draws the wire along ELK's orthogonal route (start → bend points → end),
  // which is computed to thread between node boxes. Falls back to a straight
  // line between handles if ELK gave no route. Corners are lightly rounded.
  let { sourceX, sourceY, targetX, targetY, markerEnd, style, data } = $props();

  function shorten(corner, toward, r) {
    const dx = toward.x - corner.x, dy = toward.y - corner.y;
    const len = Math.hypot(dx, dy) || 1;
    const t = Math.min(r, len / 2) / len;
    return { x: corner.x + dx * t, y: corner.y + dy * t };
  }

  function rounded(pts, r = 7) {
    if (pts.length < 2) return '';
    let d = `M ${pts[0].x},${pts[0].y}`;
    for (let i = 1; i < pts.length - 1; i++) {
      const a = shorten(pts[i], pts[i - 1], r);
      const b = shorten(pts[i], pts[i + 1], r);
      d += ` L ${a.x},${a.y} Q ${pts[i].x},${pts[i].y} ${b.x},${b.y}`;
    }
    const last = pts[pts.length - 1];
    d += ` L ${last.x},${last.y}`;
    return d;
  }

  let path = $derived.by(() => {
    const pts = data?.points?.length ? data.points : [{ x: sourceX, y: sourceY }, { x: targetX, y: targetY }];
    return rounded(pts);
  });
</script>

<BaseEdge {path} {markerEnd} {style} />

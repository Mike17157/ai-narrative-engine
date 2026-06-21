<script>
  // A numeric field you scrub by dragging up/down (like Blender/AE), instead of
  // fighting tiny spinner arrows. Click without dragging focuses it for typing;
  // ↑/↓ keys still nudge by `step`. Hold Shift while dragging/nudging for coarse
  // (×10) jumps. All pointer events are kept off the parent (so dragging it never
  // pans a graph pane underneath).
  let {
    value = $bindable(),
    step = 0.01,
    min = null,
    max = null,
    coarse = 10,        // Shift multiplier (bigger jumps)
    pxPerStep = 2,      // drag distance (px) per `step`
    precision = 2,
    title = '',
    class: cls = ''
  } = $props();

  let el;
  let drag = $state(null); // { startY, startVal, moved, id }

  const clamp = (v) => {
    if (min !== null && v < min) v = min;
    if (max !== null && v > max) v = max;
    return v;
  };
  const round = (v) => { const f = 10 ** precision; return Math.round(v * f) / f; };
  const fmt = (v) => { const n = round(+v || 0); return Object.is(n, -0) ? '0' : String(n); };

  function down(e) {
    if (e.button !== 0) return;
    e.stopPropagation();                           // don't let the graph pane pan
    drag = { startY: e.clientY, startVal: +value || 0, moved: false, id: e.pointerId };
    el.setPointerCapture(e.pointerId);
  }
  function move(e) {
    if (!drag) return;
    e.stopPropagation();
    const dy = drag.startY - e.clientY;           // up = increase
    if (!drag.moved && Math.abs(dy) < 3) return;  // tolerate a jittery click
    drag.moved = true;
    e.preventDefault();                            // suppress text selection
    const mult = e.shiftKey ? coarse : 1;
    value = clamp(round(drag.startVal + Math.round(dy / pxPerStep) * step * mult));
  }
  function up(e) {
    if (!drag) return;
    e.stopPropagation();
    const wasDrag = drag.moved;
    try { el.releasePointerCapture(drag.id); } catch { /* already released */ }
    drag = null;
    if (!wasDrag) { el.focus(); el.select(); }     // plain click → edit
  }
  function key(e) {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    e.preventDefault();
    const mult = (e.shiftKey ? coarse : 1) * (e.key === 'ArrowUp' ? 1 : -1);
    value = clamp(round((+value || 0) + step * mult));
  }
  function commit(e) {
    const v = parseFloat(e.target.value);
    value = Number.isFinite(v) ? clamp(round(v)) : (+value || 0);
    e.target.value = fmt(value); // normalize what's shown
  }
</script>

<input
  bind:this={el}
  class="scrub {cls}"
  class:dragging={!!drag?.moved}
  type="text"
  inputmode="decimal"
  value={fmt(value)}
  {title}
  onpointerdown={down}
  onpointermove={move}
  onpointerup={up}
  onkeydown={key}
  onchange={commit}
  onblur={commit}
/>

<style>
  .scrub {
    cursor: ns-resize;
    text-align: center;
    -moz-appearance: textfield;
    touch-action: none;        /* let us own vertical drags on touch */
  }
  .scrub.dragging { cursor: ns-resize; user-select: none; background: var(--elev); }
  .scrub:focus { cursor: text; }
</style>

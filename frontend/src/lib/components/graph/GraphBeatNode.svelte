<script>
  // An editable developmental beat. The POINT of each node is the INFLECTION — the
  // internal shift — with the internal state going in → coming out. `what_happened`
  // is the external event that serves as the lever. Drag from a source handle
  // (bottom / right) to another node's target handle to mark how the change proceeds:
  // multiple out = the arc diverges (a decision); multiple in = routes converge.
  import { Handle, Position } from '@xyflow/svelte';

  let { data, selected } = $props();
  const H = 'width:9px;height:9px;background:var(--accent,#6d8cff);border:2px solid var(--base,#11131c);';
</script>

<div class="gb" class:selected onclick={() => data._onSelect?.(data.id)}>
  <Handle type="target" position={Position.Top}  id="in-top"  style={H} />
  <Handle type="target" position={Position.Left} id="in-left" style={H} />
  <Handle type="source" position={Position.Bottom} id="out-bottom" style={H} />
  <Handle type="source" position={Position.Right}  id="out-right"  style={H} />

  <div class="gb-title">{data.title || 'Beat'}</div>
  {#if data.inflection}
    <div class="gb-inflect">↻ {data.inflection}</div>
  {/if}
  <div class="gb-sim">
    {#if data.start}<div class="gb-row"><span class="gl s">in</span><span class="gt">{data.start}</span></div>{/if}
    {#if data.end}<div class="gb-row"><span class="gl e">out</span><span class="gt">{data.end}</span></div>{/if}
    {#if data.what_happened}<div class="gb-row"><span class="gl h">lever</span><span class="gt dim">{data.what_happened}</span></div>{/if}
    {#if !data.inflection && !data.start && !data.end && !data.what_happened}
      <div class="gb-empty">click to describe this beat's shift…</div>
    {/if}
  </div>
  {#if data._diverges}<span class="badge div" title="The arc diverges here">⑂ decision</span>{/if}
  {#if data._converges}<span class="badge con" title="Routes converge here">⑃ converge</span>{/if}
</div>

<style>
  .gb {
    width: 230px; box-sizing: border-box;
    background: var(--panel, #1a1d27);
    border: 1.5px solid var(--border-soft, rgba(255,255,255,.1));
    border-radius: 11px; padding: 9px 11px 10px;
    cursor: pointer; box-shadow: 0 2px 12px rgba(0,0,0,.4);
    display: flex; flex-direction: column; gap: 6px; position: relative;
    transition: border-color .15s, box-shadow .15s;
  }
  .gb:hover { border-color: var(--accent, #6d8cff); }
  .gb.selected { border-color: var(--accent, #6d8cff); box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.4)), 0 2px 12px rgba(0,0,0,.4); }
  .gb-title { font-size: 13px; font-weight: 700; color: var(--text, #e0e4f0); line-height: 1.3; }
  .gb-inflect { font-size: 11.5px; font-weight: 600; color: var(--accent, #6d8cff); line-height: 1.35; }
  .gb-sim { display: flex; flex-direction: column; gap: 4px; }
  .gb-row { display: flex; gap: 6px; align-items: baseline; }
  .gl { font-size: 8.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; flex: none; width: 40px; padding-top: 1px; }
  .gl.s { color: rgba(109,140,255,.9); }
  .gl.h { color: rgba(255,200,90,.9); }
  .gl.e { color: rgba(100,210,130,.9); }
  .gt { font-size: 11.5px; color: var(--muted, #8a92b0); line-height: 1.4; }
  .gt.dim { color: var(--faint, #6a7290); font-style: italic; }
  .gb-empty { font-size: 11px; font-style: italic; color: var(--faint, #555b78); }
  .badge { position: absolute; top: -9px; font-size: 9px; font-weight: 700; padding: 1px 7px; border-radius: 999px; white-space: nowrap; }
  .badge.div { right: 8px; color: rgba(255,200,90,.95); background: rgba(40,34,16,.95); border: 1px solid rgba(255,200,90,.4); }
  .badge.con { left: 8px; color: rgba(100,210,130,.95); background: rgba(16,34,22,.95); border: 1px solid rgba(100,210,130,.4); }
</style>

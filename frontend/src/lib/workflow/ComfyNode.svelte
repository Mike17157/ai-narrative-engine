<script>
  import { Handle, Position } from '@xyflow/svelte';
  import { img, dropModelOnNode, nodeTakesModel, clipCompatWarning, toggleBypass, deleteNode, toggleEmbedding, hasEmbedding, familyFilteredOptions } from '$lib/images.svelte.js';
  import { dims, slotTop, typeColor } from '$lib/workflow_graph.js';
  import ScrubInput from '$lib/components/ScrubInput.svelte';

  // LoRA strength / weight widgets get the drag-scrubber; seed/steps/cfg/dims keep
  // a plain field so their (very different) ranges aren't forced into 0.05 steps.
  const isStrength = (name) => /strength|weight/i.test(name);

  let { id, data, selected } = $props(); // `selected` comes from Svelte Flow
  let node = $derived(img.workflow?.[id]);

  function onBypass(e) { e.stopPropagation(); toggleBypass(id); }
  function onDelete(e) { e.stopPropagation(); deleteNode(id); img.layoutNonce++; }
  let clipWarn = $derived(
    ['CLIPLoader', 'DualCLIPLoader'].includes(data.classType)
      ? clipCompatWarning(img.workflow, node?.inputs?.clip_name ?? node?.inputs?.clip_name1)
      : null
  );
  let prefs = $derived(img.nodeSizes[id] || {}); // this block's size override
  let d = $derived(dims(prefs));
  let takesModel = $derived(nodeTakesModel(data.classType));
  let bypassed = $derived(!!node?._meta?.bypassed);
  let dragOver = $state(false);

  function onDrop(e) {
    if (!e.dataTransfer?.files?.length) return;  // not a file drop — let it bubble
    e.preventDefault(); e.stopPropagation();     // claim it from the canvas import handler
    dragOver = false;
    if (takesModel) dropModelOnNode(id, e.dataTransfer.files[0]);
  }
  function onDragOver(e) {
    if (!takesModel || !e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault(); e.stopPropagation();
    dragOver = true;
  }

  function fmt(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'object') return Array.isArray(v) ? `[${v.length}]` : '{…}';
    const s = String(v);
    return s.length > 20 ? s.slice(0, 19) + '…' : s;
  }

  // Grow a textarea to fit its text (live, no relayout → keeps focus).
  function autosize(el) {
    const fit = () => { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; };
    requestAnimationFrame(fit);
    el.addEventListener('input', fit);
    return { destroy() { el.removeEventListener('input', fit); } };
  }
  // On blur, ask the graph to re-flow so neighbours re-space around the new size.
  const relayout = () => { img.layoutNonce++; };
</script>

<div class="cwrap" style="width:{d.W}px">
  {#if selected}
    <div class="nodetools nodrag nopan">
      <button class="nt" class:on={bypassed} onclick={onBypass}
        title="Bypass — skip this node at render (reversible)">{bypassed ? '◌' : '◍'}</button>
      <button class="nt del" onclick={onDelete} title="Delete this node">🗑</button>
    </div>
  {/if}
  <div class="cnode" class:dragover={dragOver} class:bypassed
    ondragover={onDragOver} ondragleave={() => (dragOver = false)} ondrop={onDrop}>
  {#if dragOver}<div class="dropbadge">drop model → {data.title}</div>{/if}
  {#if bypassed}<div class="bypassbadge">bypassed</div>{/if}
  <!-- connection inputs (left) + outputs (right), one handle per slot row -->
  {#each data.connInputs as inp, i (inp.name)}
    <Handle type="target" position={Position.Left} id={inp.name}
      style="top:{slotTop(i, prefs)}px; background:{typeColor(inp.type)}" title="{inp.name}{inp.type ? ` · ${inp.type}` : ''}" />
  {/each}
  {#each data.outputs as o, i (o.slot)}
    <Handle type="source" position={Position.Right} id={'out-' + o.slot}
      style="top:{slotTop(i, prefs)}px; background:{typeColor(o.type)}" title="{o.name}{o.type ? ` · ${o.type}` : ''}" />
  {/each}

  <div class="hd" style="height:{d.HEADER}px" title={data.classType}>{data.title}</div>

  {#if clipWarn}<div class="clipwarn nodrag" title={clipWarn}>⚠ CLIP may not match the model</div>{/if}

  {#if data.slotRows}
    <div class="slots">
      {#each Array(data.slotRows) as _, i}
        <div class="slotrow" style="height:{d.SLOT}px">
          <span class="inlbl">{data.connInputs[i]?.name ?? ''}</span>
          <span class="outlbl">{data.outputs[i]?.name ?? ''}</span>
        </div>
      {/each}
    </div>
  {/if}

  {#if node && data.widgets.length}
    <div class="widgets">
      {#each data.widgets as w (w.name)}
        {#if w.multiline}
          <div class="wrow multi">
            <span class="k">{w.name}</span>
            {#if w.embeds && img.embeddings?.length}
              <div class="embchips nodrag nopan">
                {#each img.embeddings as emb (emb)}
                  <button class="emb" class:on={hasEmbedding(id, w.name, emb)}
                    onclick={() => toggleEmbedding(id, w.name, emb)} title="Toggle embedding:{emb}">{emb}</button>
                {/each}
              </div>
            {/if}
            <textarea class="nodrag nopan ta" use:autosize bind:value={node.inputs[w.name]} onblur={relayout}></textarea>
          </div>
        {:else}
          <div class="wrow" style="height:{w.h}px">
            <span class="k" title={w.name}>{w.name}</span>
            {#if w.options}
              {@const opts = familyFilteredOptions(w.name, w.options)}
              <select class="nodrag nopan f" bind:value={node.inputs[w.name]}>
                {#if node.inputs[w.name] != null && node.inputs[w.name] !== '' && !opts.includes(node.inputs[w.name])}
                  <option value={node.inputs[w.name]}>{node.inputs[w.name]} (added)</option>
                {/if}
                {#each opts as opt}<option value={opt}>{opt}</option>{/each}
              </select>
            {:else if typeof w.value === 'boolean'}
              <input class="nodrag nopan chk" type="checkbox" bind:checked={node.inputs[w.name]} />
            {:else if typeof w.value === 'number'}
              {#if isStrength(w.name)}
                <ScrubInput class="nodrag nopan f" step={0.01} bind:value={node.inputs[w.name]} title="drag ↕ or click to type" />
              {:else}
                <input class="nodrag nopan f" type="number" step="any" bind:value={node.inputs[w.name]} />
              {/if}
            {:else if typeof w.value === 'string'}
              <input class="nodrag nopan f" bind:value={node.inputs[w.name]} />
            {:else}
              <span class="v" title={String(w.value)}>{fmt(w.value)}</span>
            {/if}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
  </div>
</div>

<style>
  .cwrap { position: relative; }
  .cnode {
    width: 100%;
    background: var(--elev); border: 1px solid var(--border); border-radius: 10px;
    overflow: hidden; box-shadow: 0 4px 14px rgba(0, 0, 0, .3); font-size: 13px; position: relative;
  }
  .cnode.dragover { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }
  .cnode.bypassed { opacity: .5; filter: grayscale(.7); border-style: dashed; }
  .bypassbadge {
    position: absolute; top: 4px; right: 6px; z-index: 6; font-size: 9.5px; font-weight: 700;
    letter-spacing: .4px; text-transform: uppercase; color: #ffd479; pointer-events: none;
  }
  /* ComfyUI-style toolbar that floats just outside the node's top-right when selected */
  .nodetools {
    position: absolute; bottom: calc(100% + 6px); right: 0; z-index: 10; display: flex; gap: 5px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 9px;
    padding: 4px; box-shadow: 0 6px 18px rgba(0, 0, 0, .45);
  }
  .nt {
    width: 22px; height: 22px; padding: 0; display: grid; place-items: center; font-size: 12px; line-height: 1;
    border-radius: 6px; box-shadow: none; border: 1px solid rgba(255, 255, 255, .18); background: rgba(20, 24, 34, .7); color: #fff;
  }
  .nt:hover { color: var(--text); background: var(--elev-2); filter: none; }
  .nt.on { color: #ffd479; border-color: rgba(255, 212, 121, .5); background: rgba(255, 212, 121, .14); }
  .nt.del:hover { color: var(--bad); border-color: rgba(255, 122, 122, .5); background: rgba(255, 122, 122, .14); }
  .dropbadge {
    position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; text-align: center;
    background: rgba(124, 109, 255, .18); color: #fff; font-weight: 600; font-size: 11.5px; padding: 6px;
    border-radius: 10px; pointer-events: none;
  }
  .hd {
    display: flex; align-items: center; padding: 0 14px; font-weight: 650; color: #fff; font-size: 14px;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  /* connection slot rows: input label hugs the left handle, output the right */
  .slotrow { display: flex; align-items: center; justify-content: space-between; padding: 0 12px; }
  .inlbl, .outlbl { font-size: 12.5px; color: #cdd6e4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .outlbl { text-align: right; color: #b6c2da; }

  .clipwarn { font-size: 12px; color: #e6b800; background: rgba(230,184,0,.12); padding: 4px 12px; border-bottom: 1px solid var(--border-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .widgets { border-top: 1px solid var(--border-soft); }
  .wrow { display: flex; gap: 8px; padding: 0 12px; align-items: center; }
  .wrow.multi { flex-direction: column; align-items: stretch; gap: 3px; padding: 5px 12px; }
  .k { color: var(--muted); flex: none; max-width: 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wrow.multi .k { max-width: none; }
  .v { color: var(--text); margin-left: auto; font-family: ui-monospace, monospace; font-size: 12.5px; min-width: 0;
       overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .f {
    margin-left: auto; flex: 1; min-width: 0; padding: 4px 8px; font-size: 13px; border-radius: 6px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: ui-monospace, monospace;
  }
  .ta {
    width: 100%; resize: none; overflow: hidden; min-height: 52px; padding: 6px 8px; font-size: 13px; border-radius: 6px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    font-family: ui-monospace, monospace; line-height: 1.45;
  }
  .f:focus, .ta:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  /* the drag-scrubber lives in a child component, so pierce scoping for its look */
  .wrow :global(.scrub) {
    margin-left: auto; flex: 1; min-width: 0; padding: 4px 8px; font-size: 13px; border-radius: 6px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: ui-monospace, monospace;
  }
  .wrow :global(.scrub:focus) { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .chk { margin-left: auto; width: auto; }
  .embchips { display: flex; flex-wrap: wrap; gap: 4px; }
  .emb {
    font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; box-shadow: none;
    border: 1px solid var(--border); background: var(--bg); color: var(--muted); font-family: ui-monospace, monospace;
  }
  .emb:hover { color: var(--text); background: var(--elev-2); filter: none; }
  .emb.on { color: #0b0e14; background: var(--accent); border-color: transparent; }
  :global(.cnode .svelte-flow__handle) { width: 13px; height: 13px; border: 2px solid #0b0e14; }
</style>

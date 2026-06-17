<script>
  import { onMount, untrack } from 'svelte';
  import { SvelteFlow, SvelteFlowProvider, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import { app, refreshModels } from '$lib/app.svelte.js';
  import { img, saveWorkflow, loadObjectInfo, connectLink, deleteNode, deleteLink, addNode, selectWorkflow, openTest, chainLora, loadGraphFamilies } from '$lib/images.svelte.js';
  import { post } from '$lib/api.js';
  import { toGraph, layoutGraph } from '$lib/workflow_graph.js';
  import ComfyNode from '$lib/workflow/ComfyNode.svelte';
  import NodeTree from '$lib/workflow/NodeTree.svelte';
  import RoutedEdge from '$lib/workflow/RoutedEdge.svelte';
  import JsonEditor from '$lib/components/JsonEditor.svelte';

  const nodeTypes = { comfy: ComfyNode };
  const edgeTypes = { routed: RoutedEdge };
  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let view = $state('graph'); // 'graph' | 'json' — JSON is an optional view, not its own tab

  // Selected block (drives the bottom resize bar). Kept across rebuilds.
  let selectedId = $state(null);
  let selected = $derived(selectedId ? nodes.find((n) => n.id === selectedId) : null);
  let selSize = $derived(img.nodeSizes[selectedId] || { nodeW: 230 });

  // ELK layout is async; guard against stale results from rapid rebuilds.
  let rebuildSeq = 0;
  async function rebuild() {
    const seq = ++rebuildSeq;
    const g = toGraph(img.workflow, img.objectInfo, img.nodeSizes);
    const laid = await layoutGraph(g.nodes, g.edges, img.nodeSizes);
    if (seq !== rebuildSeq) return;
    nodes = selectedId ? laid.nodes.map((n) => (n.id === selectedId ? { ...n, selected: true } : n)) : laid.nodes;
    edges = laid.edges;
  }

  // Resize the selected block: live (ComfyNode reacts), debounced relayout.
  let scaleTimer;
  function setSize(field, v) {
    if (!selectedId) return;
    const cur = img.nodeSizes[selectedId] || { nodeW: 230 };
    img.nodeSizes = { ...img.nodeSizes, [selectedId]: { ...cur, [field]: v } };
    clearTimeout(scaleTimer);
    scaleTimer = setTimeout(rebuild, 130);
  }

  // Node schema arrives async; rebuild once it lands so slot names/widgets apply.
  // Family data scopes each workflow's model dropdowns to its own family (strict).
  onMount(async () => { await loadObjectInfo(); loadGraphFamilies(); rebuild(); });

  // Workflow picker grouped into family "systems" (Illustrious / Pony / Anima …).
  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other');
  let wfGroups = $derived.by(() => {
    const order = [], by = {};
    for (const m of app.models.image || []) {
      const g = famCap(m.family);
      if (!(g in by)) { by[g] = []; order.push(g); }
      by[g].push(m);
    }
    return order.map((g) => ({ label: g, models: by[g] }));
  });

  // Drop a ComfyUI API-format workflow JSON → load it + check its models.
  let dropCheck = $state(null);   // { refs, missing }
  let dropDl = $state({});        // catalog rel -> pct | 'done' | 'err'
  async function onDropWorkflow(e) {
    e.preventDefault();
    const f = e.dataTransfer?.files?.[0];
    if (!f) return;
    let data;
    try { data = JSON.parse(await f.text()); }
    catch { img.msg = { err: true, text: 'Not valid JSON.' }; return; }
    if (data && Array.isArray(data.nodes)) {
      img.msg = { err: true, text: 'UI-format workflow — in ComfyUI use “Save (API Format)”, then drop that file.' };
      return;
    }
    const vals = Object.values(data || {});
    if (!vals.length || !vals.every((v) => v && typeof v === 'object' && v.class_type)) {
      img.msg = { err: true, text: 'Unrecognized JSON — needs ComfyUI API format.' };
      return;
    }
    img.workflow = data; selectedId = null; img.layoutNonce = (img.layoutNonce || 0) + 1;
    rebuild();
    const r = await post('/workflow/check', { json: data });
    dropCheck = r.data || null;
    const miss = dropCheck?.missing?.length || 0;
    img.msg = miss ? { text: `Loaded ${f.name} — ${miss} model(s) missing (see panel)` }
                   : { ok: true, text: `Loaded ${f.name} — all ${vals.length} nodes, models present ✓` };
  }
  async function dlMissing(m) {
    const rel = m.catalog?.rel;
    if (!rel || typeof dropDl[rel] === 'number') return;
    dropDl[rel] = 0;
    try {
      const res = await fetch('/api/comfy/catalog/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: m.catalog.url, rel })
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') dropDl[rel] = ev.total ? Math.round((ev.done / ev.total) * 100) : -1;
          else if (ev.type === 'done') { dropDl[rel] = 'done'; m.installed = true; }
          else if (ev.type === 'error') dropDl[rel] = 'err';
        }
      }
    } catch { dropDl[rel] = 'err'; }
  }

  // Topology edits write to the live workflow, then we rebuild from it.
  function onConnect(c) {
    if (c.target && c.targetHandle) {
      const slot = Number(String(c.sourceHandle || 'out-0').slice(4)) || 0;
      connectLink(c.target, c.targetHandle, c.source, slot);
    }
    rebuild();
  }
  function onDelete(payload) {
    for (const n of payload?.nodes || []) deleteNode(n.id);
    for (const e of payload?.edges || []) deleteLink(e.target, e.targetHandle || e.label);
    rebuild();
  }

  // Two output/input types are compatible if equal, or either side is wildcard.
  function compatible(outType, inType) {
    if (!outType || !inType) return true; // unknown → permit
    return outType === inType || inType === '*' || outType === '*';
  }

  // Reject type-mismatched wires (also dims invalid targets while dragging).
  function isValid(conn) {
    const srcClass = img.workflow?.[conn.source]?.class_type;
    const slot = Number(String(conn.sourceHandle).slice(4)) || 0;
    const outType = img.objectInfo?.[srcClass]?.outputs?.[slot]?.type;
    const di = img.objectInfo?.[img.workflow?.[conn.target]?.class_type]?.inputs?.find((x) => x.name === conn.targetHandle);
    return compatible(outType, di?.type);
  }

  // Dropping a wire on empty canvas opens a contextual search: wire an existing
  // open input, or spawn a compatible node (auto-wired).
  let drop = $state(null); // { x, y, srcId, slot, outType }
  let dropQ = $state('');
  let dropEl = $state();

  function onConnectEnd(event, cs) {
    if (!cs || cs.toHandle) return; // landed on a handle → onConnect handled it
    const fromId = cs.fromHandle?.nodeId ?? cs.fromNode?.id;
    const fromHid = cs.fromHandle?.id;
    if (!fromId || cs.fromHandle?.type !== 'source' || !fromHid) return;
    const slot = Number(String(fromHid).slice(4)) || 0;
    const outType = img.objectInfo?.[img.workflow?.[fromId]?.class_type]?.outputs?.[slot]?.type || '';
    const pt = event.changedTouches?.[0] ?? event;
    drop = { x: Math.min(pt.clientX, window.innerWidth - 360), y: Math.min(pt.clientY, window.innerHeight - 360), srcId: fromId, slot, outType };
    dropQ = '';
    queueMicrotask(() => dropEl?.querySelector('input')?.focus());
  }

  // existing open inputs compatible with the dragged output
  let dropInputs = $derived.by(() => {
    if (!drop) return [];
    const term = dropQ.trim().toLowerCase();
    const res = [];
    for (const n of nodes) {
      if (n.id === drop.srcId) continue;
      const def = img.objectInfo?.[img.workflow?.[n.id]?.class_type];
      if (!def) continue;
      const cur = img.workflow[n.id]?.inputs || {};
      for (const di of def.inputs) {
        if (di.widget || !compatible(drop.outType, di.type)) continue;
        const v = cur[di.name];
        if (Array.isArray(v) && v.length === 2) continue;
        const label = `${n.data?.title || n.id} · ${di.name}`;
        if (term && !label.toLowerCase().includes(term)) continue;
        res.push({ nodeId: n.id, input: di.name, label });
      }
    }
    return res.slice(0, 40);
  });
  // node types with a compatible input (adding one auto-wires it)
  let dropNodes = $derived.by(() => {
    if (!drop) return [];
    const term = dropQ.trim().toLowerCase();
    const res = [];
    for (const [cls, def] of Object.entries(img.objectInfo || {})) {
      const di = def.inputs?.find((x) => !x.widget && compatible(drop.outType, x.type));
      if (!di) continue;
      if (term && !cls.toLowerCase().includes(term) && !(def.category || '').toLowerCase().includes(term)) continue;
      res.push({ cls, input: di.name, category: def.category || '' });
    }
    return res.slice(0, 60);
  });

  function pickInput(t) { connectLink(t.nodeId, t.input, drop.srcId, drop.slot); drop = null; rebuild(); }
  function pickNode(t) { const id = addNode(t.cls); connectLink(id, t.input, drop.srcId, drop.slot); drop = null; rebuild(); }

  $effect(() => {
    if (!drop) return;
    const onDoc = (e) => { if (dropEl && !dropEl.contains(e.target)) drop = null; };
    const t = setTimeout(() => document.addEventListener('mousedown', onDoc), 0);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', onDoc); };
  });

  // --- add-node palette ---
  let paletteOpen = $state(false);
  let q = $state('');
  let paletteEl = $state();
  let matches = $derived.by(() => {
    const all = Object.entries(img.objectInfo || {});
    const term = q.trim().toLowerCase();
    const list = term
      ? all.filter(([k, d]) => k.toLowerCase().includes(term) || (d.category || '').toLowerCase().includes(term))
      : all;
    return list.slice(0, 80).map(([k, d]) => ({ key: k, category: d.category || '' }));
  });
  // Duplicate the selected workflow into a new, independently-editable model entry.
  // Inline name field (NOT window.prompt — that silently returns null in embedded browsers).
  let dupName = $state(null);   // null = closed; string = editing
  let dupEl = $state();
  function startDuplicate() {
    if (!app.activeImage) return;
    dupName = `${app.activeImage}_copy`;
    queueMicrotask(() => dupEl?.querySelector('input')?.focus());
  }
  async function confirmDuplicate() {
    const name = (dupName || '').trim();
    if (!name) return;
    const r = await post('/workflow/duplicate', { model: app.activeImage, name });
    if (r.ok && r.data?.key) {
      dupName = null;
      await refreshModels();
      selectWorkflow(r.data.key);
      img.msg = { ok: true, text: `Duplicated → ${r.data.key}` };
    } else {
      img.msg = { err: true, text: r.data?.error || 'duplicate failed' };
    }
  }

  function openPalette() { paletteOpen = true; q = ''; queueMicrotask(() => paletteEl?.querySelector('input')?.focus()); }
  function pick(classType) { addNode(classType); paletteOpen = false; rebuild(); }
  $effect(() => {
    function onDoc(e) { if (paletteEl && !paletteEl.contains(e.target)) paletteOpen = false; }
    document.addEventListener('click', onDoc);
    return () => document.removeEventListener('click', onDoc);
  });

  // Rebuild + auto-layout only when the *workflow itself* changes (a new object
  // from loadWorkflow). Field edits mutate the same object, so untrack() keeps
  // them from re-tracking — typing in a node never rebuilds/relays the graph.
  $effect(() => {
    img.workflow; // track the reference (new workflow loaded)
    img.layoutNonce; // …and explicit relayout requests (e.g. a prompt box grew)
    untrack(rebuild);
  });
</script>

<div class="hint">
  The active ComfyUI workflow — auto-laid-out, fields editable inline. The workflow
  selected here is the main image model used in chat. Switch to <b>JSON</b> for the raw graph.
</div>

<div class="gbar">
  <select class="wfsel" value={app.activeImage} onchange={(e) => selectWorkflow(e.currentTarget.value)} title="Image model / workflow, grouped by family — the one used in chat">
    {#each wfGroups as g (g.label)}
      <optgroup label={g.label}>
        {#each g.models as m (m.key)}<option value={m.key}>{m.key}</option>{/each}
      </optgroup>
    {/each}
  </select>
  {#if img.workflow}
    <div class="seg" role="tablist">
      <button class:on={view === 'graph'} onclick={() => (view = 'graph')}>Graph</button>
      <button class:on={view === 'json'} onclick={() => (view = 'json')}>JSON</button>
    </div>
    <span class="spacer"></span>
    <div class="actions">
      {#if view === 'graph'}
        <div class="palette-wrap" bind:this={paletteEl}>
          <button class="tbtn" onclick={(e) => { e.stopPropagation(); paletteOpen ? (paletteOpen = false) : openPalette(); }}>＋ <span>Add node</span></button>
          {#if paletteOpen}
            <div class="palette" onclick={(e) => e.stopPropagation()}>
              <input placeholder="Search {Object.keys(img.objectInfo || {}).length} node types…" bind:value={q} />
              <div class="plist">
                {#each matches as m (m.key)}
                  <button class="prow" onclick={() => pick(m.key)}>
                    <span class="pk">{m.key}</span>
                    {#if m.category}<span class="pc">{m.category}</span>{/if}
                  </button>
                {:else}
                  <div class="pempty">no matches</div>
                {/each}
              </div>
            </div>
          {/if}
        </div>
        <button class="tbtn" onclick={rebuild} title="Re-run auto-layout">⟲ <span>Re-layout</span></button>
      {/if}
      {#if dupName !== null}
        <span class="dupform" bind:this={dupEl}>
          <input class="dupin" bind:value={dupName} placeholder="new workflow name"
            onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmDuplicate(); } else if (e.key === 'Escape') dupName = null; }} />
          <button class="tbtn" onclick={confirmDuplicate} title="Create the copy">✓</button>
          <button class="tbtn" onclick={() => (dupName = null)} title="Cancel">✕</button>
        </span>
      {:else}
        <button class="tbtn" onclick={startDuplicate} title="Duplicate this workflow into a new editable copy">⧉ <span>Duplicate</span></button>
      {/if}
      <button class="tbtn" onclick={() => saveWorkflow()} title="Save the workflow file">💾 <span>Save</span></button>
      <button class="tbtn primary" onclick={openTest} disabled={img.test?.phase === 'running'}
        title={`Render a test grid with random booru tags:\n${img.testPrompt}`}>
        {img.test?.phase === 'running' ? '⏳ Testing…' : '▶ Test'}
      </button>
    </div>
  {/if}
</div>

{#if img.loading}
  <div class="hint" style="margin-top:14px">Loading workflow…</div>
{:else if !img.workflow}
  <div class="hint" style="margin-top:14px">No workflow file for this selection — pick one above.</div>
{:else if view === 'json'}
  <div class="jsonview">
    <JsonEditor workflow={img.workflow} onsave={(j) => saveWorkflow(j)} />
  </div>
{:else}
  <div class="ghint">drag handle→handle · select + Delete · edit inline · drop a workflow .json to import + check models</div>
  {#if dropCheck}
    <div class="wfcheck">
      {#if dropCheck.missing.length}
        <div class="wchead">Imported workflow needs {dropCheck.missing.length} model(s):</div>
        {#each dropCheck.missing as m (m.kind + m.name)}
          <div class="wcrow">
            <span class="wk">{m.kind}</span>
            <span class="wn" title={m.name}>{m.name}</span>
            {#if m.installed || dropDl[m.catalog?.rel] === 'done'}<span class="ok">✓ installed</span>
            {:else if m.catalog}
              {#if typeof dropDl[m.catalog.rel] === 'number'}<span class="dlpct">{dropDl[m.catalog.rel] < 0 ? '…' : dropDl[m.catalog.rel] + '%'}</span>
              {:else if dropDl[m.catalog.rel] === 'err'}<span class="err">failed</span>
              {:else}<button class="ghost sm" onclick={() => dlMissing(m)}>Download</button>{/if}
            {:else}<span class="err" title="not in catalog">not in catalog</span>{/if}
          </div>
        {/each}
      {:else}
        <div class="wchead ok">✓ all referenced models are installed</div>
      {/if}
      <button class="wcclose" onclick={() => (dropCheck = null)} aria-label="Dismiss">✕</button>
    </div>
  {/if}
  <SvelteFlowProvider>
    <div class="ge" ondragover={(e) => e.preventDefault()} ondrop={onDropWorkflow}>
      <NodeTree {nodes} onmutate={rebuild} onadd={openPalette} onchainlora={chainLora} />
      <div class="flowwrap">
        <SvelteFlow bind:nodes bind:edges {nodeTypes} {edgeTypes} colorMode="dark" fitView
          nodesDraggable={true} minZoom={0.15}
          deleteKeyCode={['Delete', 'Backspace']}
          isValidConnection={isValid}
          onnodeclick={(_e, node) => (selectedId = node?.id ?? null)}
          onpaneclick={() => (selectedId = null)}
          onconnect={onConnect} onconnectend={onConnectEnd} ondelete={onDelete}>
          <Background />
          <Controls />
        </SvelteFlow>

        {#if selected}
          <div class="boxbar">
            <span class="bn" title={selected.data?.title}>{selected.data?.title}</span>
            <label class="scl">Width <input type="range" min="160" max="460" step="5" value={selSize.nodeW} oninput={(e) => setSize('nodeW', +e.currentTarget.value)} /></label>
          </div>
        {/if}
      </div>
    </div>
  </SvelteFlowProvider>
{/if}

<svelte:window onkeydown={(e) => e.key === 'Escape' && (drop = null)} />

{#if drop}
  <div class="dropmenu" bind:this={dropEl} style="left:{drop.x}px; top:{drop.y}px" onclick={(e) => e.stopPropagation()}>
    <div class="dhd">Connect <b style="color:#fff">{drop.outType || 'output'}</b> →</div>
    <input placeholder="Search inputs / nodes…" bind:value={dropQ} />
    <div class="dlist">
      {#if dropInputs.length}
        <div class="dsec">Open inputs</div>
        {#each dropInputs as t (t.nodeId + t.input)}
          <button class="prow" onclick={() => pickInput(t)}><span class="pk">{t.label}</span></button>
        {/each}
      {/if}
      <div class="dsec">Add node</div>
      {#each dropNodes as t (t.cls)}
        <button class="prow" onclick={() => pickNode(t)}>
          <span class="pk">{t.cls}</span>
          <span class="pc">→ {t.input}{t.category ? ` · ${t.category}` : ''}</span>
        </button>
      {:else}
        <div class="pempty">no compatible nodes</div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .gbar { display: flex; gap: 10px; align-items: center; margin-top: 14px; flex-wrap: wrap; }
  .spacer { flex: 1; }
  .wfsel { width: auto; max-width: 280px; padding: 7px 11px; font-weight: 560; font-size: 13px; }
  .ghint { font-size: 12px; color: var(--faint); margin: 10px 0 0; }

  /* segmented Graph / JSON view toggle */
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 9px; overflow: hidden; }
  .seg button {
    border: 0; border-radius: 0; box-shadow: none; background: var(--elev); color: var(--muted);
    padding: 7px 16px; font-size: 13px; font-weight: 600;
  }
  .seg button:hover { color: var(--text); background: var(--elev-2); filter: none; }
  .seg button.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1.5px var(--accent); }

  /* action toolbar (top-right) */
  .actions { display: inline-flex; gap: 6px; align-items: center; }
  .dupform { display: inline-flex; gap: 4px; align-items: center; }
  .dupin { width: 180px; padding: 6px 10px; font-size: 13px; border-radius: 9px; border: 1px solid var(--accent); background: var(--elev); }
  .tbtn {
    display: inline-flex; align-items: center; gap: 6px; box-shadow: none;
    padding: 7px 13px; font-size: 13px; font-weight: 600; border-radius: 9px;
    border: 1px solid var(--border); background: var(--elev); color: var(--text);
  }
  .tbtn:hover:not(:disabled) { background: var(--elev-2); filter: none; }
  .tbtn:disabled { opacity: .55; cursor: default; }
  .tbtn.primary { background: var(--accent); border-color: transparent; color: #fff; }
  .tbtn.primary:hover:not(:disabled) { filter: brightness(1.08); background: var(--accent); }

  .jsonview { margin-top: 12px; }
  .wfcheck { position: relative; margin-top: 10px; border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--panel); }
  .wchead { font-size: 12.5px; font-weight: 600; margin-bottom: 6px; }
  .wchead.ok, .wcrow .ok { color: var(--good); }
  .wcrow { display: grid; grid-template-columns: 90px 1fr 110px; gap: 10px; align-items: center; font-size: 12px; padding: 3px 0; }
  .wk { color: var(--muted); }
  .wn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: ui-monospace, monospace; }
  .wcrow .ok, .wcrow .err, .dlpct, .wcrow button { justify-self: end; }
  .dlpct { color: var(--accent); font-size: 11.5px; font-family: ui-monospace, monospace; }
  .wcclose { position: absolute; top: 8px; right: 8px; background: none; box-shadow: none; color: var(--faint); padding: 2px 6px; }
  .scl { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted); }
  .scl input[type="range"] { width: 90px; accent-color: var(--accent); }

  .palette-wrap { position: relative; }
  .palette {
    position: absolute; z-index: 30; top: calc(100% + 6px); right: 0; width: 340px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: var(--shadow); overflow: hidden;
  }
  .palette input { border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; background: var(--elev); }
  .palette input:focus { box-shadow: none; border-color: var(--border-soft); }
  .plist { max-height: 50vh; overflow: auto; padding: 4px; }
  .prow {
    width: 100%; display: flex; align-items: baseline; gap: 8px; text-align: left; padding: 7px 9px;
    background: none; box-shadow: none; color: var(--text); border-radius: 7px;
  }
  .prow:hover { background: var(--elev-2); filter: none; }
  .pk { font-size: 13px; font-weight: 560; }
  .pc { margin-left: auto; font-size: 11px; color: var(--muted); white-space: nowrap; }
  .pempty { padding: 12px; color: var(--muted); font-size: 13px; }

  /* contextual drop menu (drag a wire onto empty canvas) */
  .dropmenu {
    position: fixed; z-index: 60; width: 340px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: var(--shadow); overflow: hidden;
  }
  .dhd { padding: 8px 12px; font-size: 12px; color: var(--muted); border-bottom: 1px solid var(--border-soft); }
  .dropmenu input { border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; background: var(--elev); }
  .dropmenu input:focus { box-shadow: none; border-color: var(--border-soft); }
  .dlist { max-height: 46vh; overflow: auto; padding: 4px; }
  .dsec { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 8px 9px 4px; }
  .ge { display: grid; grid-template-columns: 320px 1fr; gap: 12px; margin-top: 10px; }
  .flowwrap {
    position: relative; height: 74vh; border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden; background: var(--bg);
  }
  /* floating resize bar — only while a block is selected */
  .boxbar {
    position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%); z-index: 5;
    display: flex; align-items: center; gap: 18px; padding: 9px 16px;
    background: rgba(20, 24, 34, .92); border: 1px solid var(--border); border-radius: 999px;
    box-shadow: 0 12px 34px rgba(0, 0, 0, .5); backdrop-filter: blur(8px);
  }
  .boxbar .bn { font-size: 12px; color: var(--text); font-weight: 600; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* selected edge: thicken + white so it's clear what Delete will remove */
  .flowwrap :global(.svelte-flow__edge.selected .svelte-flow__edge-path) {
    stroke: #fff !important; stroke-width: 3 !important; opacity: 1 !important;
  }
</style>

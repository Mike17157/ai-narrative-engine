<script>
  // The story graph as the Console's canvas: the spine core (logline + wound/lie/truth)
  // as an editable header, then an editable DAG of beat-nodes. The workshop model
  // proposes/revises this graph as you converse; you also drag to connect nodes
  // (divergence = a decision, convergence = different routes to the same beat),
  // add/remove beats, and edit each beat's start / what-happened / end.
  //
  // Edits are pushed up via onChange so the host can feed the graph back to the model.
  // The host bumps `seq` whenever the MODEL sends a fresh graph, forcing a rebuild.
  import { SvelteFlow, SvelteFlowProvider, Background, Controls, addEdge } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import GraphBeatNode from '$lib/components/graph/GraphBeatNode.svelte';
  import { autosize } from '$lib/autosize.js';

  let {
    graph = null,       // { logline, wound, lie, truth, nodes:[{id,title,start,end,what_happened,next,x,y}] }
    seq = 0,            // bump to rebuild from `graph` (model pushed a new one)
    busy = false,
    onChange = null,    // (graph) => void
  } = $props();

  const nodeTypes = { graphBeat: GraphBeatNode };

  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let selectedId = $state(null);
  let core = $state({ logline: '', wound: '', lie: '', truth: '' });
  let lastSeq = -1;
  let uidc = 0;
  function uid() { return `n${(Date.now() % 1e7).toString(36)}${uidc++}`; }

  const E_STYLE = 'stroke:rgba(109,140,255,.7);stroke-width:2';
  const mkEdge = (s, t) => ({
    id: `${s}->${t}`, source: s, target: t,
    sourceHandle: 'out-bottom', targetHandle: 'in-top',
    type: 'smoothstep', deletable: true, style: E_STYLE,
    markerEnd: { type: 'arrowclosed', color: 'rgba(109,140,255,.8)', width: 18, height: 18 },
  });

  // ── Layout: BFS depth → columns (used for nodes that lack saved positions) ──
  function layout(list) {
    const byId = new Map(list.map((n) => [n.id, n]));
    const indeg = new Map(list.map((n) => [n.id, 0]));
    for (const n of list) for (const t of (n.next || [])) if (indeg.has(t)) indeg.set(t, indeg.get(t) + 1);
    const depth = new Map();
    let frontier = list.filter((n) => (indeg.get(n.id) || 0) === 0).map((n) => n.id);
    if (!frontier.length && list.length) frontier = [list[0].id];
    const seen = new Set();
    let d = 0;
    while (frontier.length) {
      const nf = [];
      for (const id of frontier) {
        if (seen.has(id)) continue;
        seen.add(id); depth.set(id, d);
        for (const t of (byId.get(id)?.next || [])) if (!seen.has(t)) nf.push(t);
      }
      frontier = nf; d++;
    }
    for (const n of list) if (!depth.has(n.id)) depth.set(n.id, 0);
    const cols = {};
    for (const n of list) (cols[depth.get(n.id)] ||= []).push(n.id);
    const pos = {};
    for (const [dp, ids] of Object.entries(cols)) ids.forEach((id, i) => (pos[id] = { x: Number(dp) * 290, y: i * 175 }));
    return pos;
  }

  // ── graph (model) → flow ──────────────────────────────────────────────────
  function rebuild() {
    const list = graph?.nodes || [];
    core = {
      logline: graph?.logline || '', wound: graph?.wound || '',
      lie: graph?.lie || '', truth: graph?.truth || '',
    };
    const pos = layout(list);
    const indeg = {};
    for (const n of list) for (const t of (n.next || [])) indeg[t] = (indeg[t] || 0) + 1;
    nodes = list.map((n) => ({
      id: n.id,
      type: 'graphBeat',
      position: (n.x != null && n.y != null) ? { x: n.x, y: n.y } : (pos[n.id] || { x: 0, y: 0 }),
      data: {
        id: n.id, title: n.title || '', inflection: n.inflection || '',
        start: n.start || '', end: n.end || '', what_happened: n.what_happened || '',
        summary: n.summary || '', scene_prompt: n.scene_prompt || '',
        location: n.location || '', characters: n.characters || [],
        _onSelect: select,
        _diverges: (n.next || []).length >= 2, _converges: (indeg[n.id] || 0) >= 2,
      },
    }));
    const es = [];
    for (const n of list) for (const t of (n.next || [])) if (list.some((m) => m.id === t)) es.push(mkEdge(n.id, t));
    edges = es;
  }

  $effect(() => { if (seq !== lastSeq) { lastSeq = seq; rebuild(); } });

  // ── flow → graph (emit edits back to the host) ──────────────────────────────
  function emit() {
    refreshBadges();
    const out = nodes.map((fn) => ({
      id: fn.id,
      title: fn.data.title || '', inflection: fn.data.inflection || '',
      start: fn.data.start || '', end: fn.data.end || '', what_happened: fn.data.what_happened || '',
      summary: fn.data.summary || '', scene_prompt: fn.data.scene_prompt || '',
      location: fn.data.location || '', characters: fn.data.characters || [],
      next: edges.filter((e) => e.source === fn.id).map((e) => e.target),
      x: Math.round(fn.position.x), y: Math.round(fn.position.y),
    }));
    onChange?.({ ...core, nodes: out });
  }

  // Recompute divergence/convergence badges from the current edge set.
  function refreshBadges() {
    const out = {}, ind = {};
    for (const e of edges) { out[e.source] = (out[e.source] || 0) + 1; ind[e.target] = (ind[e.target] || 0) + 1; }
    nodes = nodes.map((n) => ({ ...n, data: { ...n.data, _diverges: (out[n.id] || 0) >= 2, _converges: (ind[n.id] || 0) >= 2 } }));
  }

  function onConnect(conn) {
    if (!conn?.source || !conn?.target || conn.source === conn.target) return;
    if (edges.some((e) => e.source === conn.source && e.target === conn.target)) return;
    edges = addEdge(mkEdge(conn.source, conn.target), edges);
    emit();
  }
  function onFlowDelete() { emit(); }       // bound arrays already updated by SvelteFlow
  function onDragStop() { emit(); }         // persist positions

  function select(id) { selectedId = id; }
  let selNode = $derived(nodes.find((n) => n.id === selectedId) || null);

  function patch(field, value) {
    nodes = nodes.map((n) => (n.id === selectedId ? { ...n, data: { ...n.data, [field]: value } } : n));
    emit();
  }

  function addBeat(fromId = null) {
    const id = uid();
    const base = fromId ? nodes.find((n) => n.id === fromId)?.position : null;
    const position = base ? { x: base.x, y: base.y + 200 } : { x: 40, y: 40 + nodes.length * 30 };
    nodes = [...nodes, { id, type: 'graphBeat', position, data: { id, title: 'New beat', inflection: '', start: '', end: '', what_happened: '', summary: '', scene_prompt: '', location: '', characters: [], _onSelect: select } }];
    if (fromId) edges = addEdge(mkEdge(fromId, id), edges);
    selectedId = id;
    emit();
  }

  function deleteBeat(id) {
    nodes = nodes.filter((n) => n.id !== id);
    edges = edges.filter((e) => e.source !== id && e.target !== id);
    if (selectedId === id) selectedId = null;
    emit();
  }

  let hasGraph = $derived((nodes.length > 0) || !!(core.logline || core.wound || core.lie || core.truth));
</script>

<div class="sgc">
  <!-- Spine core -->
  {#if hasGraph}
    <div class="core">
      <input class="fld logline" bind:value={core.logline} oninput={emit} placeholder="Logline — the one-sentence spine…" />
      <div class="triad">
        <label class="cell wound"><span>Wound</span><input class="fld" bind:value={core.wound} oninput={emit} placeholder="the unhealed hurt" /></label>
        <label class="cell lie"><span>Lie</span><input class="fld" bind:value={core.lie} oninput={emit} placeholder="the false belief" /></label>
        <label class="cell truth"><span>Truth</span><input class="fld" bind:value={core.truth} oninput={emit} placeholder="what they must face" /></label>
      </div>
    </div>
  {/if}

  <div class="bar">
    <span class="bar-title">Arc of change</span>
    {#if busy}<span class="live"><span class="dot"></span>updating…</span>{/if}
    <span class="sp"></span>
    {#if nodes.length}<span class="hint">drag ●→● to branch or merge the arc</span>{/if}
    <button class="mini" onclick={() => addBeat()}>＋ Beat</button>
  </div>

  {#if !hasGraph}
    <div class="empty">
      <p>The arc of change takes shape as you talk.</p>
      <p class="lo">The consultant maps the character's development — each beat an internal shift, with the state going in and out, and the event that forces it — and marks where the arc can diverge or converge. Drag to rewire it; your edits steer the next reply.</p>
    </div>
  {:else}
    <div class="flowwrap">
      <SvelteFlowProvider>
        <SvelteFlow
          bind:nodes
          bind:edges
          {nodeTypes}
          colorMode="dark"
          fitView
          fitViewOptions={{ padding: 0.18 }}
          minZoom={0.15}
          nodesDraggable
          nodesConnectable
          elementsSelectable
          deleteKeyCode={['Delete', 'Backspace']}
          onconnect={onConnect}
          ondelete={onFlowDelete}
          onnodedragstop={onDragStop}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={22} size={1} color="rgba(255,255,255,.04)" />
          <Controls showFitView showZoom position="bottom-right" />
        </SvelteFlow>
      </SvelteFlowProvider>
    </div>

    {#if selNode}
      <div class="insp">
        <div class="insp-head">
          <input class="fld it" value={selNode.data.title} oninput={(e) => patch('title', e.target.value)} placeholder="Beat title" />
          <button class="mini ghost" onclick={() => addBeat(selNode.id)} title="Add a beat after this one">＋ after</button>
          <button class="mini danger" onclick={() => deleteBeat(selNode.id)} title="Delete this beat">✕</button>
        </div>
        <label class="il f">The shift <textarea class="fld ta" rows="1" use:autosize={selNode.data.inflection} value={selNode.data.inflection} oninput={(e) => patch('inflection', e.target.value)} placeholder="the internal inflection this beat forces (e.g. 'first crack in the lie')"></textarea></label>
        <label class="il s">Going in <textarea class="fld ta" rows="1" use:autosize={selNode.data.start} value={selNode.data.start} oninput={(e) => patch('start', e.target.value)} placeholder="internal state entering the beat"></textarea></label>
        <label class="il e">Coming out <textarea class="fld ta" rows="1" use:autosize={selNode.data.end} value={selNode.data.end} oninput={(e) => patch('end', e.target.value)} placeholder="internal state leaving the beat"></textarea></label>
        <label class="il h">The event (lever) <textarea class="fld ta" rows="1" use:autosize={selNode.data.what_happened} value={selNode.data.what_happened} oninput={(e) => patch('what_happened', e.target.value)} placeholder="the external event that forces the shift"></textarea></label>
      </div>
    {:else if nodes.length}
      <p class="pick">Click a beat to edit its start · what happens · end.</p>
    {/if}
  {/if}
</div>

<style>
  .sgc { display: flex; flex-direction: column; gap: 10px; padding: 12px; }

  .core { display: flex; flex-direction: column; gap: 6px; }
  .logline { font-weight: 600; }
  .triad { display: flex; gap: 6px; }
  .cell { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .cell span { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; }
  .cell.wound span { color: rgba(255,140,109,.9); }
  .cell.lie span   { color: rgba(255,200,90,.9); }
  .cell.truth span { color: rgba(100,210,130,.9); }

  .fld { width: 100%; padding: 6px 9px; font-size: 12px; border-radius: 7px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--text); font-family: inherit; box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.18)); }
  .ta { line-height: 1.45; resize: none; overflow: hidden; min-height: 30px; }

  .bar { display: flex; align-items: center; gap: 8px; }
  .bar-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .live { display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; color: var(--accent); }
  .live .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 8px var(--accent); animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .4; transform: scale(.7); } }
  .sp { flex: 1; }
  .hint { font-size: 10.5px; color: var(--faint); font-style: italic; }
  .mini { font-size: 11px; padding: 4px 10px; border-radius: 7px; background: var(--accent); border: 0; color: #fff; cursor: pointer; font-weight: 600; }
  .mini.ghost { background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .mini.ghost:hover { color: var(--text); }
  .mini.danger { background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .mini.danger:hover { color: var(--bad, #ff7a7a); border-color: rgba(255,122,122,.4); }

  .empty { padding: 30px 14px; text-align: center; }
  .empty p { margin: 0 0 6px; font-size: 13px; color: var(--muted); }
  .empty .lo { font-size: 12px; color: var(--faint); line-height: 1.55; }

  .flowwrap { height: 380px; border: 1px solid var(--border-soft); border-radius: 10px; overflow: hidden; background: var(--base, #11131c); }
  :global(.sgc .svelte-flow) { background: transparent !important; }

  .insp { display: flex; flex-direction: column; gap: 6px; padding: 10px; border: 1px solid var(--border-soft); border-radius: 10px; background: var(--bg); }
  .insp-head { display: flex; gap: 6px; align-items: center; }
  .it { flex: 1; font-weight: 700; }
  .il { display: flex; flex-direction: column; gap: 2px; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; }
  .il.f { color: var(--accent, #6d8cff); }
  .il.s { color: rgba(109,140,255,.9); }
  .il.h { color: rgba(255,200,90,.9); }
  .il.e { color: rgba(100,210,130,.9); }
  .pick { font-size: 11.5px; color: var(--faint); text-align: center; margin: 0; padding: 4px; }
</style>

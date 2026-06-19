<script>
  import { onMount } from 'svelte';
  import { SvelteFlow, SvelteFlowProvider, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import ArcBeatNode from '$lib/components/ArcBeatNode.svelte';
  import ArcGroupNode from '$lib/components/ArcGroupNode.svelte';
  import RoutedEdge from '$lib/workflow/RoutedEdge.svelte';
  import { buildStoryGraph } from '$lib/story_graph.js';
  import { storyViewport, setStoryViewport } from '$lib/stories.svelte.js';

  // Whole-story canvas: every arc is a lane, beats inside. A left node-nav lists
  // every beat (grouped by arc) so you can jump/refocus the canvas on one. Click
  // a card → node modal; click a lane → arc modal. Pan/zoom persists per story.
  let { story, storyKey, onSelectNode = null, onSelectArc = null, height = '74vh' } = $props();

  const nodeTypes = { beat: ArcBeatNode, arcGroup: ArcGroupNode };
  const edgeTypes = { routed: RoutedEdge };

  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let busy  = $state(true);
  let activeId = $state(null);

  // Canvas viewport bounds (for centring on a node) + persisted pan/zoom.
  let wrapW = $state(0);
  let wrapH = $state(0);
  let vp = $state(storyViewport(storyKey) || undefined);
  let saveTimer = null;
  $effect(() => {
    const v = vp;
    if (!v) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => setStoryViewport(storyKey, v), 400);
  });

  let rebuildSeq = 0;
  async function rebuild(s) {
    const seq = ++rebuildSeq;
    busy = true;
    try {
      const g = await buildStoryGraph(s);
      if (seq !== rebuildSeq) return;
      nodes = g.nodes;
      edges = g.edges;
    } catch (e) {
      console.error('buildStoryGraph failed:', e);
      if (seq === rebuildSeq) { nodes = []; edges = []; }
    } finally {
      if (seq === rebuildSeq) busy = false;
    }
  }

  onMount(() => { rebuild(story); });
  $effect(() => { if (story) rebuild(story); });

  // xyflow 1.x passes (event, node) — NOT a {detail} CustomEvent.
  function onNodeClick(_e, n) {
    if (!n) return;
    activeId = n.id;
    if (n.type === 'arcGroup') { if (onSelectArc) onSelectArc(n.data.arcIdx); return; }
    if (onSelectNode) onSelectNode(n.data);
  }

  // Centre the canvas on a node (keep the current zoom) by driving the viewport.
  function refocus(n) {
    activeId = n.id;
    if (!wrapW || !wrapH) return;
    const z = vp?.zoom || 1;
    const cx = n.position.x + (n.width || 300) / 2;
    const cy = n.position.y + (n.height || 188) / 2;
    vp = { x: wrapW / 2 - cx * z, y: wrapH / 2 - cy * z, zoom: z };
  }
</script>

<SvelteFlowProvider>
  <div class="graph-shell" style={`height:${height}`}>
    <!-- Left node-nav: jump between beats -->
    <aside class="node-nav">
      <div class="nn-head">Nodes</div>
      <div class="nn-list">
        {#each nodes as n (n.id)}
          {#if n.type === 'arcGroup'}
            <div class="nn-arc">{n.data.name}</div>
          {:else if n.type === 'beat'}
            <button class="nn-item" class:active={activeId === n.id} onclick={() => refocus(n)} title="Refocus on this node">
              <span class="nn-dot"></span>
              <span class="nn-label">{n.data.title || 'Chapter'}</span>
            </button>
          {/if}
        {/each}
        {#if !nodes.length}<div class="nn-empty">No chapters yet.</div>{/if}
      </div>
    </aside>

    <div class="graph-wrap" bind:clientWidth={wrapW} bind:clientHeight={wrapH}>
      {#if busy}
        <div class="graph-busy"><span class="spin"></span></div>
      {:else if !nodes.length}
        <div class="graph-empty">No chapters yet — expand an arc first.</div>
      {:else}
        <SvelteFlow
          {nodes}
          {edges}
          {nodeTypes}
          {edgeTypes}
          bind:viewport={vp}
          fitView={!vp}
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.2}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          onnodeclick={onNodeClick}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
          <Controls showFitView showZoom position="bottom-right" />
        </SvelteFlow>
      {/if}
    </div>
  </div>
</SvelteFlowProvider>

<style>
  .graph-shell {
    display: flex;
    width: 100%;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid var(--border-soft, rgba(255,255,255,.07));
    background: var(--base, #11131c);
  }

  /* Left node-nav */
  .node-nav {
    width: 196px; flex: none;
    display: flex; flex-direction: column;
    border-right: 1px solid var(--border-soft, rgba(255,255,255,.07));
    background: var(--panel, #1a1d27);
  }
  .nn-head {
    font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--faint, #555b78); padding: 10px 12px 6px; flex: none;
  }
  .nn-list { flex: 1; overflow: auto; padding: 0 6px 8px; display: flex; flex-direction: column; gap: 1px; }
  .nn-arc {
    font-size: 10.5px; font-weight: 700; color: var(--text, #e0e4f0);
    padding: 8px 6px 3px; margin-top: 4px; border-top: 1px solid var(--border-soft, rgba(255,255,255,.06));
  }
  .nn-arc:first-child { border-top: none; margin-top: 0; }
  .nn-item {
    display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
    padding: 6px 8px; border-radius: 7px; background: none; border: none; box-shadow: none;
    color: var(--muted, #8a92b0); font: inherit; font-size: 12px; cursor: pointer;
  }
  .nn-item:hover { background: var(--elev, #22263a); color: var(--text, #e0e4f0); }
  .nn-item.active { background: rgba(109,140,255,.14); color: var(--accent, #6d8cff); }
  .nn-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; opacity: .6; }
  .nn-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nn-empty { font-size: 11.5px; color: var(--faint); padding: 8px; }

  .graph-wrap { flex: 1; min-width: 0; height: 100%; position: relative; }

  .graph-busy, .graph-empty {
    position: absolute; inset: 0;
    display: grid; place-items: center;
    color: var(--muted, #8a92b0); font-size: 13px;
  }
  .spin {
    width: 22px; height: 22px; border-radius: 50%;
    border: 2.5px solid rgba(109,140,255,.25); border-top-color: var(--accent, #6d8cff);
    animation: spin .75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Override Svelte Flow defaults to match dark theme */
  :global(.svelte-flow) { background: transparent !important; }
  :global(.svelte-flow__controls) {
    background: var(--panel, #1a1d27) !important;
    border: 1px solid var(--border, rgba(255,255,255,.12)) !important;
    border-radius: 8px !important; box-shadow: none !important;
  }
  :global(.svelte-flow__controls-button) {
    background: transparent !important; border: none !important; color: var(--muted, #8a92b0) !important;
  }
  :global(.svelte-flow__controls-button:hover) {
    background: var(--elev, #22263a) !important; color: var(--text, #e0e4f0) !important;
  }
</style>

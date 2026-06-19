<script>
  import { onMount } from 'svelte';
  import { SvelteFlow, SvelteFlowProvider, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import ArcBeatNode from '$lib/components/ArcBeatNode.svelte';
  import RoutedEdge from '$lib/workflow/RoutedEdge.svelte';
  import { toStoryGraph, layoutStoryGraph } from '$lib/story_graph.js';

  // Props
  let { arc, onRegenNode = null } = $props();

  const nodeTypes = { beat: ArcBeatNode };
  const edgeTypes = { routed: RoutedEdge };

  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let busy  = $state(true);

  let rebuildSeq = 0;
  async function rebuild(a) {
    const seq = ++rebuildSeq;
    busy = true;
    const g = toStoryGraph(a);
    if (!g.nodes.length) { nodes = []; edges = []; busy = false; return; }
    const laid = await layoutStoryGraph(g.nodes, g.edges);
    if (seq !== rebuildSeq) return;
    nodes = laid.nodes;
    edges = laid.edges;
    busy = false;
  }

  onMount(() => { rebuild(arc); });
  $effect(() => { if (arc) rebuild(arc); });

  function onNodeClick(e) {
    const nodeId = e.detail?.node?.id;
    if (nodeId && onRegenNode) onRegenNode(nodeId);
  }
</script>

<SvelteFlowProvider>
  <div class="graph-wrap">
    {#if busy}
      <div class="graph-busy"><span class="spin"></span></div>
    {:else if !nodes.length}
      <div class="graph-empty">No chapters yet — expand this arc first.</div>
    {:else}
      <SvelteFlow
        {nodes}
        {edges}
        {nodeTypes}
        {edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={!!onRegenNode}
        onnodeclick={onNodeClick}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
        <Controls showFitView showZoom={false} position="bottom-right" />
      </SvelteFlow>
    {/if}
  </div>
</SvelteFlowProvider>

<style>
  .graph-wrap {
    width: 100%;
    height: 480px;
    border-radius: 10px;
    overflow: hidden;
    background: var(--base, #11131c);
    border: 1px solid var(--border-soft, rgba(255,255,255,.07));
    position: relative;
  }

  .graph-busy, .graph-empty {
    position: absolute; inset: 0;
    display: grid; place-items: center;
    color: var(--muted, #8a92b0);
    font-size: 13px;
  }

  .spin {
    width: 22px; height: 22px;
    border-radius: 50%;
    border: 2.5px solid rgba(109,140,255,.25);
    border-top-color: var(--accent, #6d8cff);
    animation: spin .75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Override Svelte Flow defaults to match dark theme */
  :global(.svelte-flow) { background: transparent !important; }
  :global(.svelte-flow__controls) {
    background: var(--panel, #1a1d27) !important;
    border: 1px solid var(--border, rgba(255,255,255,.12)) !important;
    border-radius: 8px !important;
    box-shadow: none !important;
  }
  :global(.svelte-flow__controls-button) {
    background: transparent !important;
    border: none !important;
    color: var(--muted, #8a92b0) !important;
  }
  :global(.svelte-flow__controls-button:hover) {
    background: var(--elev, #22263a) !important;
    color: var(--text, #e0e4f0) !important;
  }
</style>

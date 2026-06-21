<script>
  import { onMount } from 'svelte';
  import { SvelteFlow, SvelteFlowProvider, Background, Controls, MiniMap } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import ArcBeatNode from '$lib/components/graph/ArcBeatNode.svelte';
  import ArcGroupNode from '$lib/components/graph/ArcGroupNode.svelte';
  import TimelineHeadNode from '$lib/components/graph/TimelineHeadNode.svelte';
  import { buildStoryGraph, ARC_COLORS } from '$lib/story_graph.js';
  import { storyViewport, setStoryViewport } from '$lib/stories.svelte.js';

  let { story, storyKey, onSelectNode = null, onSelectArc = null, height = '74vh' } = $props();

  const nodeTypes = { beat: ArcBeatNode, arcGroup: ArcGroupNode, timelineHead: TimelineHeadNode };

  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  let busy  = $state(true);
  let activeId = $state(null);

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

  function onBeatClick(data) {
    if (!data) return;
    activeId = `${data.arcId}::${data.nodeId}`;
    onSelectNode?.(data);
  }
  function onArcClick(data) {
    if (!data) return;
    onSelectArc?.(data.arcIdx);
  }

  let rebuildSeq = 0;
  async function rebuild(s) {
    const seq = ++rebuildSeq;
    busy = true;
    try {
      const g = await buildStoryGraph(s);
      if (seq !== rebuildSeq) return;
      nodes = g.nodes.map(n =>
        n.type === 'beat'
          ? { ...n, data: { ...n.data, _onClick: onBeatClick } }
          : n.type === 'arcGroup'
            ? { ...n, data: { ...n.data, _onClick: onArcClick } }
            : n
      );
      edges = g.edges;
    } catch (e) {
      console.error('buildStoryGraph failed:', e);
      if (seq === rebuildSeq) { nodes = []; edges = []; }
    } finally {
      if (seq === rebuildSeq) busy = false;
    }
  }

  onMount(() => rebuild(story));
  $effect(() => { if (story) rebuild(story); });

  // MiniMap coloring — nodes colored by arc, selected node gets white outline
  function minimapNodeColor(node) {
    if (node.type === 'beat') return node.data?.arcColor || ARC_COLORS[0].label;
    if (node.type === 'arcGroup') return 'rgba(255,255,255,.06)';
    return 'rgba(255,255,255,.03)';
  }

  function minimapNodeStroke(node) {
    return node.id === activeId ? '#ffffff' : 'transparent';
  }
</script>

<SvelteFlowProvider>
  <div class="graph-wrap" bind:clientWidth={wrapW} bind:clientHeight={wrapH} style={`height:${height}`}>
    {#if busy}
      <div class="graph-busy"><span class="spin"></span></div>
    {:else if !nodes.length}
      <div class="graph-empty">No chapters yet — expand an arc first.</div>
    {:else}
      <SvelteFlow
        {nodes}
        {edges}
        {nodeTypes}
        colorMode="dark"
        bind:viewport={vp}
        fitView={!vp}
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.08}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
        <Controls showFitView showZoom position="bottom-right" />
        <MiniMap
          position="top-left"
          nodeColor={minimapNodeColor}
          nodeStrokeColor={minimapNodeStroke}
          nodeStrokeWidth={3}
          maskColor="rgba(8,10,18,.65)"
          zoomable
          pannable
        />
      </SvelteFlow>
    {/if}
  </div>
</SvelteFlowProvider>

<style>
  .graph-wrap {
    width: 100%; position: relative;
    border-radius: 10px; overflow: hidden;
    border: 1px solid var(--border-soft, rgba(255,255,255,.07));
    background: var(--base, #11131c);
  }

  .graph-busy, .graph-empty {
    position: absolute; inset: 0; display: grid; place-items: center;
    color: var(--muted, #8a92b0); font-size: 13px;
  }
  .spin {
    width: 22px; height: 22px; border-radius: 50%;
    border: 2.5px solid rgba(109,140,255,.25); border-top-color: var(--accent, #6d8cff);
    animation: spin .75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  :global(.svelte-flow) { background: transparent !important; }

  :global(.svelte-flow__controls) {
    background: var(--panel, #1a1d27) !important;
    border: 1px solid var(--border, rgba(255,255,255,.12)) !important;
    border-radius: 8px !important; box-shadow: none !important;
  }
  :global(.svelte-flow__controls-button) {
    background: transparent !important; border: none !important;
    color: var(--muted, #8a92b0) !important;
  }
  :global(.svelte-flow__controls-button:hover) {
    background: var(--elev, #22263a) !important; color: var(--text, #e0e4f0) !important;
  }

  :global(.svelte-flow__minimap) {
    border-radius: 10px !important; overflow: hidden;
    border: 1px solid rgba(255,255,255,.1) !important;
    background: rgba(11,13,20,.92) !important;
  }
</style>

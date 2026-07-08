<script>
  // The RELATIONSHIP RAILROAD — a READ-ONLY view of the FIXED bonds the story starts with. The cast
  // are boxed by home location; bonds are Svelte Flow edges coloured by kind, dotted when a hidden
  // undercurrent runs beneath. Click a face → open the card. Editing bonds (nature/undercurrent) and
  // homes happens in the section editor — this renders, the editor mutates. See [[bond-depth-weave]].
  import { SvelteFlow, SvelteFlowProvider, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import CharacterNode from '$lib/components/graph/CharacterNode.svelte';
  import PlaceBox from '$lib/components/graph/PlaceBox.svelte';
  import { buildRelationshipHub } from '$lib/canvas_graph.js';

  // onSetHome/onSaveBonds retained in props (unused) so the parent's bindings don't warn.
  let { storyKey = '', cast = [], relationships = [], locations = [],
        onSelect = () => {}, onSaveBonds = () => {}, onSetHome = () => {} } = $props();

  const nodeTypes = { character: CharacterNode, placebox: PlaceBox };
  const edgeTypes = {};
  let mc = $derived(cast.find((c) => c.primary) || cast[0] || null);

  let nodes = $state.raw([]);
  let edges = $state.raw([]);
  $effect(() => {
    // Build from PLAIN snapshots — reactive-proxy node objects break Svelte Flow's internals store.
    const built = buildRelationshipHub($state.snapshot(cast), $state.snapshot(relationships), $state.snapshot(locations), mc?.key || '');
    nodes = built.nodes.map((n) => n.type === 'character'
      ? { ...n, data: { ...n.data, _onClick: (d) => onSelect(d.key) } } : n);
    edges = built.edges;
  });
</script>

<div class="rflow">
  <p class="hint">The <b>fixed</b> bonds the story starts with — family, friends, enemies — with the cast
    boxed by where they live. <b>Click a face</b> to open the card. Track colour = kind of bond; dotted =
    a hidden undercurrent. Edit bonds and set homes in the editor.</p>

  <div class="canvas">
    {#if !cast.length}
      <div class="empty">No cast yet — add characters first.</div>
    {:else}
      <SvelteFlowProvider>
        <SvelteFlow bind:nodes bind:edges {nodeTypes} {edgeTypes} colorMode="dark" fitView
          fitViewOptions={{ padding: 0.25 }} minZoom={0.2} maxZoom={2}
          nodesDraggable={false} nodesConnectable={false} proOptions={{ hideAttribution: true }}>
          <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
          <Controls showFitView showZoom position="bottom-right" />
        </SvelteFlow>
      </SvelteFlowProvider>
    {/if}
  </div>
</div>

<style>
  .rflow { display: flex; flex-direction: column; gap: 10px; position: relative; }
  .hint { margin: 0; }
  .canvas { height: 68vh; border-radius: 12px; overflow: hidden; border: 1px solid var(--border-soft); background: var(--base, #11131c); position: relative; }
  .empty { height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
  :global(.svelte-flow__edge-text) { fill: rgba(232,234,242,.95); font-size: 10px; }
  :global(.svelte-flow__edge-textbg) { fill: rgba(18,21,30,.9); }
  :global(.svelte-flow) { background: transparent !important; }
  :global(.svelte-flow__controls) { background: var(--panel) !important; border: 1px solid var(--border) !important; border-radius: 8px !important; box-shadow: none !important; }
  :global(.svelte-flow__controls-button) { background: transparent !important; border: none !important; color: var(--muted) !important; }
  :global(.svelte-flow__controls-button:hover) { background: var(--elev) !important; color: var(--text) !important; }
</style>

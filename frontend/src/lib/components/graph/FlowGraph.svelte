<script>
  // The reusable Svelte Flow renderer — the robust core extracted from StoryGraph so EVERY
  // graph layer (structure, relationships, map) shares the same pan/zoom/minimap/controls
  // behaviour. Nodes carry their own `data._onClick(data)` (set by the builder), so this
  // wrapper stays generic. See [[overview-is-editor]].
  import { SvelteFlow, SvelteFlowProvider, Background, Controls, MiniMap } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';

  let { nodes = [], edges = [], nodeTypes = {}, edgeTypes = {}, fitView = true,
        minimap = true, minimapNodeColor = null, busy = false, empty = '' } = $props();
</script>

<SvelteFlowProvider>
  <div class="fg">
    {#if busy}
      <div class="fg-msg"><span class="spin"></span></div>
    {:else if !nodes.length}
      <div class="fg-msg">{empty || 'Nothing to show.'}</div>
    {:else}
      <SvelteFlow
        {nodes} {edges} {nodeTypes} {edgeTypes}
        colorMode="dark"
        {fitView}
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.06}
        maxZoom={2.5}
        panOnScroll={true}
        panOnScrollSpeed={1.4}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
        <Controls showFitView showZoom position="bottom-right" />
        {#if minimap}
          <MiniMap position="top-left" nodeColor={minimapNodeColor || undefined}
                   maskColor="rgba(8,10,18,.65)" zoomable pannable />
        {/if}
      </SvelteFlow>
    {/if}
  </div>
</SvelteFlowProvider>

<style>
  .fg { width: 100%; height: 100%; position: relative; }
  .fg-msg { position: absolute; inset: 0; display: grid; place-items: center;
            color: var(--muted, #8a92b0); font-size: 13px; }
  .spin { width: 22px; height: 22px; border-radius: 50%;
          border: 2.5px solid rgba(109,140,255,.25); border-top-color: var(--accent, #6d8cff);
          animation: spin .75s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

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
  :global(.svelte-flow__minimap) {
    border-radius: 10px !important; overflow: hidden;
    border: 1px solid rgba(255,255,255,.1) !important; background: rgba(11,13,20,.92) !important;
  }
</style>

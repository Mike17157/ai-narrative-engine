<script>
  // ONE graph space; every layer rendered through the SAME Svelte Flow engine (FlowGraph) so
  // Relationships and Map are as robust as Structure. Layers are stacked planes — only the active
  // one is visible; switching ZOOMS (2D scale). The active plane auto-follows the "active speaker"
  // (the agent's behaviour). Clicking a character node opens its detail modal. See [[overview-is-editor]].
  import FlowGraph from '$lib/components/graph/FlowGraph.svelte';
  import ArcBeatNode from '$lib/components/graph/ArcBeatNode.svelte';
  import ArcGroupNode from '$lib/components/graph/ArcGroupNode.svelte';
  import TimelineHeadNode from '$lib/components/graph/TimelineHeadNode.svelte';
  import CharacterNode from '$lib/components/graph/CharacterNode.svelte';
  import LocationNode from '$lib/components/graph/LocationNode.svelte';
  import RelEdge from '$lib/components/graph/RelEdge.svelte';
  import { buildStoryGraph, ARC_COLORS } from '$lib/story_graph.js';
  import { buildRelationshipGraph, buildMapGraph } from '$lib/canvas_graph.js';

  let { story, storyKey, cast = [], speaker = '', focus = '', relPulse = null,
        onSelectNode = () => {}, onSelectArc = () => {}, onSelectChar = () => {} } = $props();

  const nodeTypes = { beat: ArcBeatNode, arcGroup: ArcGroupNode, timelineHead: TimelineHeadNode,
                      character: CharacterNode, location: LocationNode };
  const edgeTypes = { rel: RelEdge };

  const LAYERS = [
    { id: 'structure',     label: 'Structure',     icon: '🪢' },
    { id: 'relationships', label: 'Relationships', icon: '🕸' },
    { id: 'map',           label: 'Map',           icon: '🗺' },
  ];
  let active = $state(0);

  function depthFor(spk) {
    const s = (spk || '').toLowerCase();
    if (!s) return -1;
    if (s.includes('smith') || s.includes('character') || s.includes('relationship') || s.includes('bond')) return 1;
    if (s.includes('location') || s.includes('scene') || s.includes('place') || s.includes('map')) return 2;
    if (s.includes('story') || s.includes('arc') || s.includes('board') || s.includes('spine') || s.includes('beat')) return 0;
    return -1;
  }
  let auto = $state(true);
  let lastSpk = '';
  $effect(() => {
    const spk = speaker;
    if (spk === lastSpk) return;
    lastSpk = spk;
    if (auto) { const d = depthFor(spk); if (d >= 0) active = d; }
  });
  function jump(i) { auto = false; active = i; }
  function resume() { auto = true; const d = depthFor(speaker); if (d >= 0) active = d; }

  // Zoom: only the active plane is visible; switching scales the incoming up from the back and
  // the outgoing past the camera. Wide interval (0.62 ↔ 1.45) for a pronounced zoom.
  function planeStyle(i) {
    if (i === active) return 'transform: scale(1); opacity: 1;';
    return `transform: scale(${i - active > 0 ? 0.62 : 1.45}); opacity: 0;`;
  }

  // ── Structure layer: the robust async ELK build (same code as StoryGraph), click → beat/arc.
  let struct = $state.raw({ nodes: [], edges: [] });
  let structBusy = $state(true);
  let seq = 0;
  async function rebuildStruct(s) {
    const k = ++seq; structBusy = true;
    try {
      const g = await buildStoryGraph(s);
      if (k !== seq) return;
      struct = {
        nodes: g.nodes.map((n) =>
          n.type === 'beat'     ? { ...n, data: { ...n.data, _onClick: (d) => onSelectNode(d) } }
        : n.type === 'arcGroup' ? { ...n, data: { ...n.data, _onClick: (d) => onSelectArc(d.arcIdx) } }
        : n),
        edges: g.edges,
      };
    } catch (e) { if (k === seq) struct = { nodes: [], edges: [] }; }
    finally { if (k === seq) structBusy = false; }
  }
  $effect(() => { if (story) rebuildStruct(story); });
  function structMiniColor(node) {
    if (node.type === 'beat') return node.data?.arcColor || ARC_COLORS[0].label;
    if (node.type === 'arcGroup') return 'rgba(255,255,255,.06)';
    return 'rgba(255,255,255,.03)';
  }

  // ── Relationships + Map layers: synchronous builders, click → character modal.
  let rel = $derived.by(() => {
    const g = buildRelationshipGraph(cast, story?.relationships || [], focus);
    return { ...g, nodes: g.nodes.map((n) => ({ ...n, data: { ...n.data, _onClick: (d) => onSelectChar(d.key) } })) };
  });
  let map = $derived.by(() => buildMapGraph(story?.locations || [], story?.start));
</script>

<div class="canvas">
  <div class="layers">
    {#each LAYERS as L, i (L.id)}
      <button class="lbtn" class:on={active === i} onclick={() => jump(i)} title={L.label}>
        <span class="li">{L.icon}</span><span class="ll">{L.label}</span>
      </button>
    {/each}
    {#if !auto}
      <button class="lbtn auto" onclick={resume} title="Follow the active speaker again">⟳ auto</button>
    {/if}
  </div>

  <div class="stack">
    <div class="plane" class:on={active === 0} style={planeStyle(0)}>
      <FlowGraph nodes={struct.nodes} edges={struct.edges} {nodeTypes} busy={structBusy}
                 minimapNodeColor={structMiniColor} empty="No structure yet — generate arcs or a storyboard." />
    </div>
    <div class="plane" class:on={active === 1} style={planeStyle(1)}>
      <FlowGraph nodes={rel.nodes} edges={rel.edges} {nodeTypes} {edgeTypes} minimap={false} empty="No cast yet." />
    </div>
    <div class="plane" class:on={active === 2} style={planeStyle(2)}>
      <FlowGraph nodes={map.nodes} edges={map.edges} {nodeTypes} minimap={false}
                 empty="No locations yet — add some in the Locations section." />
    </div>
  </div>
</div>

<style>
  .canvas { position: relative; height: 74vh; border-radius: 10px; overflow: hidden;
            border: 1px solid var(--border-soft); background: var(--base, #11131c); }

  .layers { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 20;
            display: flex; gap: 3px; padding: 3px; border-radius: 9px;
            background: color-mix(in srgb, var(--panel) 85%, transparent);
            border: 1px solid var(--border); box-shadow: 0 4px 14px rgba(0,0,0,.3); backdrop-filter: blur(6px); }
  .lbtn { display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 7px;
          background: none; border: none; box-shadow: none; color: var(--muted); font-size: 12.5px;
          font-weight: 600; cursor: pointer; }
  .lbtn:hover { color: var(--text); background: var(--elev); filter: none; }
  .lbtn.on { color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); }
  .lbtn.auto { color: var(--faint); font-weight: 500; }
  .li { font-size: 14px; line-height: 1; }

  .stack { position: absolute; inset: 0; }
  .plane { position: absolute; inset: 0; pointer-events: none; opacity: 0; transform-origin: 50% 45%;
           transition: transform .5s cubic-bezier(.4,.06,.2,1), opacity .38s ease; }
  .plane.on { pointer-events: auto; }
</style>

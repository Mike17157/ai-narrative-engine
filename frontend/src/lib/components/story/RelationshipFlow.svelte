<script>
  // The RELATIONSHIP RAILROAD — the FIXED bonds the story starts with. The cast are grouped into
  // container BOXES by home location (family/household units); bonds are NATIVE Svelte Flow
  // `smoothstep` edges (orthogonal = railroad), coloured by the KIND of bond (family/friend/enemy/
  // mentor/lover) and dotted when a hidden undercurrent runs beneath. Warmth/intimacy isn't set
  // here — it drifts in play (scribe rel-deltas + the StoryMaster). Click a face → the card; click
  // a track → edit the bond. Same relationships[] data the card/queue derive from. See [[bond-depth-weave]].
  import { SvelteFlow, SvelteFlowProvider, Background, Controls } from '@xyflow/svelte';
  import '@xyflow/svelte/dist/style.css';
  import CharacterNode from '$lib/components/graph/CharacterNode.svelte';
  import PlaceBox from '$lib/components/graph/PlaceBox.svelte';
  import { buildRelationshipHub } from '$lib/canvas_graph.js';

  let { storyKey = '', cast = [], relationships = [], locations = [],
        onSelect = () => {}, onSaveBonds = () => {}, onSetHome = () => {} } = $props();

  const nodeTypes = { character: CharacterNode, placebox: PlaceBox };
  const edgeTypes = {};
  const NATURES = ['family', 'friend', 'enemy', 'mentor', 'lover', 'rival', 'ally', 'neighbour'];
  const STANCES = ['devoted', 'warm', 'neutral', 'strained', 'hostile'];
  const nameOf = (k) => cast.find((c) => c.key === k)?.name || k;
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

  // Drag a character into a place BOX to set their home — the box whose bounds contain the
  // dropped card wins; dropped outside every box → unplaced. onSetHome persists it, which
  // rebuilds the graph so the card settles into that box's layout slot.
  function onNodeDragStop(_e, node) {
    if (!node || node.type === 'placebox' || !node.position) return;
    const cx = node.position.x + 66, cy = node.position.y + 72;
    const box = nodes.find((n) => n.type === 'placebox'
      && cx >= n.position.x && cx <= n.position.x + n.width
      && cy >= n.position.y && cy <= n.position.y + n.height);
    const lid = box ? box.id.slice(4) : '';   // strip 'box-'
    const home = lid === '__unplaced' ? '' : lid;
    const prev = cast.find((c) => c.key === node.id)?.home || '';
    if (home !== prev) onSetHome(node.id, home);   // only write when the box actually changed
  }

  // ── Bond inspector (edit the fixed bond) — opens from a track click or ＋ bond. ──
  let bond = $state(null);
  function editBond(rel) {
    bond = { source: '', target: '', nature: '', stance: 'neutral', dynamic: '',
             target_stance: '', target_dynamic: '', potential: '', trajectory: '', note: '', ...rel };
  }
  function newBond() {
    editBond({ source: mc?.key || '', target: cast.find((c) => c.key !== mc?.key)?.key || '' });
  }
  function saveBond() {
    if (!bond?.source || !bond?.target || bond.source === bond.target) return;
    const id = bond.id || `r-${bond.source}-${bond.target}`;
    const next = [...(relationships || []).filter((r) => (r.id || `r-${r.source}-${r.target}`) !== id), { ...bond, id }];
    bond = null;
    onSaveBonds(next);
  }
  function deleteBond() {
    const id = bond?.id || (bond?.source && bond?.target ? `r-${bond.source}-${bond.target}` : '');
    bond = null;
    if (id) onSaveBonds((relationships || []).filter((r) => (r.id || `r-${r.source}-${r.target}`) !== id));
  }
</script>

<div class="rflow">
  <div class="rtop">
    <p class="hint">The <b>fixed</b> bonds the story starts with — family, friends, enemies — with the cast
      boxed by where they live. <b>Drag a face into a place box</b> to set where they live. (Warmth/intimacy
      isn't set here; it drifts in play.) Track colour = kind of bond; dotted = a hidden undercurrent.</p>
    <span class="sp"></span>
    <button class="addb" onclick={newBond} disabled={cast.length < 2}>＋ bond</button>
  </div>

  <!-- Precise placement fallback (drag in the graph is the primary way). -->
  <details class="placer">
    <summary>📍 Place the cast precisely…</summary>
    <div class="prow">
      {#each cast as c (c.key)}
        <label class="pchip">{c.name}
          <select value={c.home || ''} onchange={(e) => onSetHome(c.key, e.currentTarget.value)}>
            <option value="">— unplaced —</option>
            {#each locations as l (l.id)}<option value={l.id}>{l.name}</option>{/each}
          </select>
        </label>
      {/each}
      {#if !locations.length}<span class="pnote">Add locations in the Map tab first.</span>{/if}
    </div>
  </details>

  <div class="canvas">
    {#if !cast.length}
      <div class="empty">No cast yet — add characters first.</div>
    {:else}
      <SvelteFlowProvider>
        <SvelteFlow bind:nodes bind:edges {nodeTypes} {edgeTypes} colorMode="dark" fitView
          fitViewOptions={{ padding: 0.25 }} minZoom={0.2} maxZoom={2}
          nodesDraggable nodesConnectable={false} proOptions={{ hideAttribution: true }}
          onnodedragstop={onNodeDragStop}
          onedgeclick={(_e, edge) => edge?.data?.rel && editBond(edge.data.rel)}>
          <Background gap={24} size={1} color="rgba(255,255,255,.03)" />
          <Controls showFitView showZoom position="bottom-right" />
        </SvelteFlow>
      </SvelteFlowProvider>
    {/if}
  </div>

  {#if bond}
    <div class="inspector">
      <div class="ihead"><b>Bond</b><span class="sp"></span>
        <button class="idel" onclick={deleteBond}>Delete</button>
        <button class="ix" onclick={() => (bond = null)} aria-label="Close">✕</button></div>
      <div class="pair">
        <select bind:value={bond.source}>{#each cast as c (c.key)}<option value={c.key}>{c.name}</option>{/each}</select>
        <span class="arrow">⇄</span>
        <select bind:value={bond.target}>{#each cast as c (c.key)}<option value={c.key}>{c.name}</option>{/each}</select>
      </div>
      <div class="fixed">
        <div class="dlbl nk">⛓ The bond <span class="opt">— fixed at story start</span></div>
        <input class="fld big" bind:value={bond.nature} placeholder="the KIND of bond — mother, mentor, rival, sworn enemy…" list="natures" />
        <datalist id="natures">{#each NATURES as n}<option value={n}></option>{/each}</datalist>
      </div>
      <div class="deep">
        <div class="dlbl">⌇ Undercurrent <span class="opt">— hidden; the narrator never sees this</span></div>
        <input class="fld" bind:value={bond.potential} placeholder="potential — the buried common core beneath the surface" />
        <input class="fld" bind:value={bond.trajectory} placeholder="trajectory — where it could travel, and how it feels" />
      </div>
      <details class="drift">
        <summary>Starting warmth <span class="opt">— optional; play drifts this</span></summary>
        <div class="side">
          <div class="slbl">{nameOf(bond.source)} → {nameOf(bond.target)}</div>
          <input class="fld" bind:value={bond.dynamic} placeholder="how they feel at the start (2-3 words)" />
          <select class="fld" bind:value={bond.stance}>{#each STANCES as s}<option value={s}>{s}</option>{/each}</select>
        </div>
        <div class="side">
          <div class="slbl">{nameOf(bond.target)} → {nameOf(bond.source)} <span class="opt">(blank = mirror)</span></div>
          <input class="fld" bind:value={bond.target_dynamic} placeholder="how they feel back (2-3 words)" />
          <select class="fld" bind:value={bond.target_stance}><option value="">— mirror —</option>{#each STANCES as s}<option value={s}>{s}</option>{/each}</select>
        </div>
      </details>
      <button class="save" onclick={saveBond}>Save bond</button>
    </div>
  {/if}
</div>

<style>
  .rflow { display: flex; flex-direction: column; gap: 10px; position: relative; }
  .rtop { display: flex; align-items: flex-start; gap: 10px; }
  .hint { margin: 0; }
  .sp { flex: 1; }
  .addb { flex: none; font-size: 12px; font-weight: 600; padding: 6px 13px; border-radius: 999px;
          background: none; border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .addb:disabled { opacity: .4; cursor: default; }
  .placer { border-radius: 9px; }
  .placer > summary { font-size: 11.5px; font-weight: 600; color: var(--faint); cursor: pointer; list-style: none; padding: 2px 0; }
  .placer[open] > summary { color: var(--muted); margin-bottom: 6px; }
  .prow { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .pchip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted);
           background: var(--elev); border: 1px solid var(--border-soft); border-radius: 999px; padding: 3px 6px 3px 11px; }
  .pchip select { font: inherit; font-size: 11.5px; color: var(--text); background: var(--panel);
                  border: 1px solid var(--border-soft); border-radius: 999px; padding: 3px 6px; cursor: pointer; }
  .pchip select:focus { outline: none; border-color: var(--accent); }
  .pnote { font-size: 11.5px; color: var(--faint); font-style: italic; }
  .canvas { height: 68vh; border-radius: 12px; overflow: hidden; border: 1px solid var(--border-soft); background: var(--base, #11131c); position: relative; }
  .empty { height: 100%; display: grid; place-items: center; color: var(--muted); font-size: 13px; }
  :global(.svelte-flow__edge) { cursor: pointer; }
  :global(.svelte-flow__edge-text) { fill: rgba(232,234,242,.95); font-size: 10px; }
  :global(.svelte-flow__edge-textbg) { fill: rgba(18,21,30,.9); }
  :global(.svelte-flow) { background: transparent !important; }
  :global(.svelte-flow__controls) { background: var(--panel) !important; border: 1px solid var(--border) !important; border-radius: 8px !important; box-shadow: none !important; }
  :global(.svelte-flow__controls-button) { background: transparent !important; border: none !important; color: var(--muted) !important; }
  :global(.svelte-flow__controls-button:hover) { background: var(--elev) !important; color: var(--text) !important; }

  .inspector { position: absolute; top: 46px; right: 10px; width: 320px; z-index: 30; display: flex;
    flex-direction: column; gap: 7px; padding: 12px; border-radius: 12px;
    background: color-mix(in srgb, var(--panel) 96%, transparent); border: 1px solid var(--accent);
    box-shadow: 0 10px 34px rgba(0,0,0,.5); }
  .ihead { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .idel { font-size: 11px; padding: 3px 9px; border-radius: 7px; background: none; border: 1px solid var(--border-soft); color: var(--bad, #d0655a); cursor: pointer; }
  .ix { background: none; border: none; color: var(--muted); font-size: 14px; cursor: pointer; }
  .pair { display: flex; align-items: center; gap: 8px; }
  .pair select { flex: 1; min-width: 0; }
  .arrow { color: var(--muted); }
  select, .fld { font: inherit; font-size: 12px; padding: 6px 8px; border-radius: 7px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); width: 100%; box-sizing: border-box; }
  select:focus, .fld:focus { outline: none; border-color: var(--accent); }
  .side, .deep, .fixed { display: flex; flex-direction: column; gap: 5px; padding: 8px; border-radius: 9px; background: var(--elev); }
  .fixed { border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border-soft)); }
  .deep { border: 1px dashed color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 5%, var(--elev)); }
  .slbl, .dlbl { font-size: 10.5px; font-weight: 700; color: var(--muted); }
  .dlbl { color: var(--accent); }
  .dlbl.nk { color: var(--text); }
  .fld.big { font-size: 13px; padding: 8px 10px; }
  .drift { border-radius: 9px; background: var(--elev); }
  .drift > summary { font-size: 10.5px; font-weight: 700; color: var(--faint); cursor: pointer; padding: 7px 8px; list-style: none; }
  .drift[open] > summary { color: var(--muted); }
  .drift .side { background: transparent; padding: 4px 8px 8px; }
  .opt { font-weight: 400; color: var(--faint); }
  .save { margin-top: 2px; font-size: 12.5px; font-weight: 700; padding: 8px; border-radius: 8px; border: none; background: var(--accent); color: #0b0e14; cursor: pointer; }
</style>

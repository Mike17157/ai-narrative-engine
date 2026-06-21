<script>
  import { useSvelteFlow } from '@xyflow/svelte';
  import { img, deleteNode } from '$lib/images.svelte.js';
  import { sectionOf, VIRTUAL_SECTIONS, buildSectionMap } from '$lib/workflow_graph.js';

  // nodes    — whatever the graph currently shows (section nodes or raw nodes)
  // isSectionView — true when the graph canvas is in Sections mode
  // drillSection  — non-null key when user has drilled into a section
  let { nodes, onmutate, onadd, onchainlora, isSectionView = false, drillSection = null, ondrillsection, ondrillexit } = $props();
  const { fitView, setCenter } = useSvelteFlow();

  let q = $state('');
  let collapsed = $state(new Set());

  // ── helpers ──────────────────────────────────────────────────────────────────
  const LORA_TYPES = new Set(['LoraLoader', 'LoraLoaderModelOnly']);
  function isLora(n) { return LORA_TYPES.has(n.data?.classType); }
  const secKey = (n) => n.data?.section || sectionOf(n.id);

  function secInfo(key) {
    if (VIRTUAL_SECTIONS[key]) return VIRTUAL_SECTIONS[key];
    const named = img.sections?.[key];
    if (named) return named;
    const num = parseInt(key.slice(1)) || 9000;
    return { name: `Subgraph ${key.slice(1)}`, color: `hsl(${(num * 37) % 360} 50% 55%)`, order: num };
  }

  // ── Mode 1: Section overview ──────────────────────────────────────────────
  // Nodes here are section nodes from toSectionGraph. Sort by order field.
  let overviewList = $derived.by(() => {
    if (!isSectionView || drillSection) return [];
    return [...nodes]
      .sort((a, b) => (a.data?.order ?? 9999) - (b.data?.order ?? 9999))
      .map(n => ({
        key: n.id,
        name: n.data?.name ?? secInfo(n.id).name,
        color: n.data?.color ?? secInfo(n.id).color,
        count: n.data?.count ?? 0,
      }));
  });

  // ── Mode 2: Drill-in ─────────────────────────────────────────────────────
  // Nodes here are the raw ComfyUI nodes inside the drilled section.
  let drillNodes = $derived.by(() => {
    if (!drillSection) return [];
    const term = q.trim().toLowerCase();
    return term ? nodes.filter(n => (n.data?.title || '').toLowerCase().includes(term)) : nodes;
  });

  const drillInfo = $derived.by(() => drillSection ? secInfo(drillSection) : { name: '', color: '#7f8aa3' });

  // ── Mode 3: Full node view ───────────────────────────────────────────────
  // Nodes are all raw workflow nodes. Group by section, with Essentials pinned.
  let essentials = $derived.by(() => {
    if (isSectionView) return [];
    const ks = new Set(img.keyNodes || []);
    return nodes.filter(n => ks.has(n.id));
  });

  let nodeGroups = $derived.by(() => {
    if (isSectionView) return [];
    const term = q.trim().toLowerCase();
    const ns = term ? nodes.filter(n => (n.data?.title || '').toLowerCase().includes(term)) : nodes;
    const byKey = new Map();
    for (const n of ns) {
      const k = secKey(n);
      if (!byKey.has(k)) byKey.set(k, []);
      byKey.get(k).push(n);
    }
    const keys = [...byKey.keys()].sort((a, b) => (secInfo(a).order ?? 9999) - (secInfo(b).order ?? 9999));
    return keys.map(k => ({
      key: k, info: secInfo(k),
      nodes: byKey.get(k).sort((a, b) => (a.position?.y ?? 0) - (b.position?.y ?? 0)),
      hasLora: byKey.get(k).some(isLora),
    }));
  });

  // ── Global I/O pane (sections overview only) ─────────────────────────────
  // Shows actual widget values from top-level input/output nodes (seeds, paths, etc.)
  // instead of displaying them as noisy wires in the graph.
  let ioOpen = $state(true);

  const ioData = $derived.by(() => {
    if (!isSectionView || drillSection) return null;
    const rsec = buildSectionMap(img.workflow);
    const inputs = [], outputs = [];
    for (const [id, node] of Object.entries(img.workflow || {})) {
      const sec = rsec(id, node?.class_type || '');
      if (sec === '__top_src__') inputs.push({ id, node });
      else if (sec === '__top_snk__') outputs.push({ id, node });
    }
    return { inputs, outputs };
  });

  // Extract non-link widget values from a node (up to 3 most useful ones).
  function nodeWidgets(node) {
    return Object.entries(node.inputs || {})
      .filter(([, v]) => !Array.isArray(v))
      .slice(0, 3);
  }

  function fmtVal(v) {
    if (typeof v === 'number') return String(v);
    const s = String(v ?? '');
    return s.length > 40 ? s.slice(0, 38) + '…' : s;
  }

  // ── actions ───────────────────────────────────────────────────────────────
  function focusNode(n) {
    setCenter(n.position.x + (n.width || 230) / 2, n.position.y + (n.height || 80) / 2, { zoom: 1.1, duration: 400 });
  }
  function del(n) { deleteNode(n.id); onmutate?.(); }
  function fitAll() { fitView({ duration: 380, padding: 0.08 }); }
  function toggle(key) {
    const s = new Set(collapsed); s.has(key) ? s.delete(key) : s.add(key); collapsed = s;
  }
  function focusSection(key) {
    img.activeSection = key;
    const ids = nodes.filter(n => secKey(n) === key).map(n => ({ id: n.id }));
    if (ids.length) fitView({ nodes: ids, duration: 380, padding: 0.15 });
  }
</script>

<div class="tree">
  <div class="thead">
    <input class="tsearch"
      placeholder={drillSection ? 'Find node…' : isSectionView ? 'Find section…' : 'Find block…'}
      bind:value={q} />
    {#if !isSectionView && !drillSection}
      <button class="addbtn" onclick={onadd} title="Add a block">＋</button>
    {/if}
  </div>

  <div class="tlist">

    <!-- ════════════════════════════════════════════════════
         MODE 2 — Drilled into a section
         ════════════════════════════════════════════════════ -->
    {#if drillSection}
      <div class="drill-hd" style="--dc:{drillInfo.color}">
        <button class="backbtn" onclick={ondrillexit}>← Sections</button>
        <span class="drill-title">{drillInfo.name}</span>
      </div>
      <div class="allrow">
        <button class="allbtn" onclick={fitAll}>Fit view <span class="lc">{nodes.length}</span></button>
      </div>
      {#each drillNodes as n (n.id)}
        <div class="nrow">
          <button class="tt" onclick={() => focusNode(n)} title={n.data?.classType}>{n.data?.title || n.id}</button>
          <button class="del" onclick={() => del(n)} aria-label="Delete">✕</button>
        </div>
      {:else}
        <div class="tempty">no nodes</div>
      {/each}

    <!-- ════════════════════════════════════════════════════
         MODE 1 — Section overview (click → drill in)
         ════════════════════════════════════════════════════ -->
    {:else if isSectionView}
      <div class="allrow">
        <button class="allbtn" onclick={fitAll}>All sections <span class="lc">{overviewList.length}</span></button>
      </div>
      {#each overviewList as s (s.key)}
        <button class="sec-row" onclick={() => ondrillsection?.(s.key)} title="Open section">
          <span class="sdot" style="background:{s.color}"></span>
          <span class="sec-nm">{s.name}</span>
          <span class="lc">{s.count} nodes</span>
          <span class="darr">→</span>
        </button>
      {:else}
        <div class="tempty">Loading…</div>
      {/each}

      <!-- Global I/O pane: inputs + outputs of the whole workflow -->
      {#if ioData && (ioData.inputs.length || ioData.outputs.length)}
        <div class="io-panel">
          <button class="io-hd" onclick={() => (ioOpen = !ioOpen)}>
            <span class="lcaret" class:open={ioOpen}>▸</span>
            Workflow I/O
          </button>
          {#if ioOpen}
            {#if ioData.inputs.length}
              <div class="io-sec">
                <button class="io-sec-hd" style="color:#27ae60"
                  onclick={() => ondrillsection?.('__top_src__')} title="Open Inputs section">
                  Inputs →
                </button>
                {#each ioData.inputs as { id, node } (id)}
                  <div class="io-node">
                    <span class="io-cls">{node.class_type}</span>
                    {#each nodeWidgets(node) as [k, v]}
                      <div class="io-row">
                        <span class="io-k">{k}</span>
                        <span class="io-v">{fmtVal(v)}</span>
                      </div>
                    {/each}
                  </div>
                {/each}
              </div>
            {/if}
            {#if ioData.outputs.length}
              <div class="io-sec">
                <button class="io-sec-hd" style="color:#c0392b"
                  onclick={() => ondrillsection?.('__top_snk__')} title="Open Outputs section">
                  Outputs →
                </button>
                {#each ioData.outputs as { id, node } (id)}
                  <div class="io-node">
                    <span class="io-cls">{node.class_type}</span>
                    {#each nodeWidgets(node) as [k, v]}
                      <div class="io-row">
                        <span class="io-k">{k}</span>
                        <span class="io-v">{fmtVal(v)}</span>
                      </div>
                    {/each}
                  </div>
                {/each}
              </div>
            {/if}
          {/if}
        </div>
      {/if}

    <!-- ════════════════════════════════════════════════════
         MODE 3 — Full node view (section-grouped, expandable)
         ════════════════════════════════════════════════════ -->
    {:else}
      <div class="allrow">
        <button class="allbtn" class:on={!img.activeSection}
          onclick={() => { img.activeSection = null; fitAll(); }}>
          All nodes <span class="lc">{nodes.length}</span>
        </button>
      </div>

      {#if essentials.length && !q}
        <div class="lvl">
          <div class="lhrow">
            <button class="lh" onclick={() => toggle('__ess__')}>
              <span class="sdot" style="background:#f6c90e"></span>
              <span class="lcaret" class:open={!collapsed.has('__ess__')}>▸</span>
              Essentials
              <span class="lc">{essentials.length}</span>
            </button>
          </div>
          {#if !collapsed.has('__ess__')}
            {#each essentials as n (n.id)}
              <div class="nrow">
                <button class="tt" onclick={() => focusNode(n)} title={n.data?.classType}>{n.data?.title || n.id}</button>
                <button class="del" onclick={() => del(n)} aria-label="Delete">✕</button>
              </div>
            {/each}
          {/if}
        </div>
      {/if}

      {#each nodeGroups as g (g.key)}
        <div class="lvl" class:active={img.activeSection === g.key}>
          <div class="lhrow">
            <button class="lh" onclick={() => focusSection(g.key)}>
              <span class="sdot" style="background:{g.info.color}"></span>
              <span class="lcaret" class:open={!collapsed.has(g.key)}>▸</span>
              {g.info.name}
              <span class="lc">{g.nodes.length}</span>
            </button>
            <button class="collbtn" onclick={(e) => { e.stopPropagation(); toggle(g.key); }}>
              {collapsed.has(g.key) ? '▾' : '▴'}
            </button>
            {#if g.hasLora}
              <button class="chainbtn" onclick={onchainlora}>＋ LoRA</button>
            {/if}
          </div>
          {#if !collapsed.has(g.key)}
            {#each g.nodes as n (n.id)}
              <div class="nrow">
                <button class="tt" onclick={() => focusNode(n)} title={n.data?.classType}>{n.data?.title || n.id}</button>
                <button class="del" onclick={() => del(n)} aria-label="Delete">✕</button>
              </div>
            {:else}
              <div class="nempty">empty</div>
            {/each}
          {/if}
        </div>
      {:else}
        <div class="tempty">no blocks</div>
      {/each}
    {/if}

  </div>
</div>

<style>
  .tree { display: flex; flex-direction: column; height: 74vh; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; overflow: hidden; }
  .thead { display: flex; align-items: center; gap: 6px; padding: 6px; border-bottom: 1px solid var(--border-soft); }
  .tsearch { flex: 1; border: 1px solid var(--border); border-radius: 7px; background: var(--elev); font-size: 12.5px; padding: 6px 9px; }
  .addbtn { flex: none; width: 30px; height: 30px; padding: 0; font-size: 16px; border-radius: 7px; }
  .tlist { flex: 1; overflow: auto; padding: 4px 0; }

  .allrow { padding: 4px 6px 2px; }
  .allbtn {
    width: 100%; display: flex; align-items: center; justify-content: space-between; gap: 8px;
    background: none; box-shadow: none; padding: 6px 8px; border-radius: 7px;
    font-size: 12px; color: var(--muted); font-weight: 600; text-align: left;
  }
  .allbtn:hover, .allbtn.on { background: var(--elev); color: var(--text); filter: none; }
  .allbtn.on { color: var(--accent); }

  /* ── Drill mode ── */
  .drill-hd {
    display: flex; align-items: center; gap: 8px; padding: 5px 8px;
    border-bottom: 1px solid color-mix(in srgb, var(--dc) 35%, transparent);
    background: color-mix(in srgb, var(--dc) 12%, var(--panel));
  }
  .backbtn {
    background: none; box-shadow: none; padding: 5px 9px; font-size: 12px; font-weight: 600;
    color: var(--muted); border-radius: 6px; white-space: nowrap;
  }
  .backbtn:hover { color: var(--text); background: var(--elev); filter: none; }
  .drill-title { font-size: 12px; font-weight: 700; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Section overview ── */
  .sec-row {
    width: 100%; display: flex; align-items: center; gap: 7px; padding: 10px 10px;
    background: none; box-shadow: none; text-align: left; color: var(--text);
    border-top: 1px solid var(--border-soft);
  }
  .sec-row:first-of-type { border-top: none; }
  .sec-row:hover { background: var(--elev); filter: none; }
  .sec-nm { flex: 1; font-size: 13px; font-weight: 600; }
  .darr { color: var(--faint); font-size: 12px; }
  .sec-row:hover .darr { color: var(--text); }

  /* ── Full node view ── */
  .lvl + .lvl { border-top: 1px solid var(--border-soft); }
  .lvl.active { background: color-mix(in srgb, var(--elev) 60%, transparent); }
  .lhrow { display: flex; align-items: center; }
  .lh {
    display: flex; align-items: center; gap: 6px; flex: 1; min-width: 0; text-align: left;
    background: none; box-shadow: none; padding: 7px 10px; color: var(--muted);
    font-size: 11px; text-transform: uppercase; letter-spacing: .4px; font-weight: 700;
  }
  .lh:hover { color: var(--text); filter: none; background: var(--elev); }
  .active .lh { color: var(--text); }
  .collbtn { flex: none; background: none; box-shadow: none; color: var(--faint); font-size: 11px; padding: 0 6px; }
  .collbtn:hover { color: var(--text); filter: none; }
  .chainbtn { flex: none; margin-right: 6px; padding: 3px 8px; font-size: 11px; border-radius: 6px; }
  .nempty { padding: 4px 10px 8px 28px; font-size: 12px; color: var(--faint); }

  /* ── Shared ── */
  .sdot { flex: none; width: 8px; height: 8px; border-radius: 50%; }
  .lcaret { font-size: 9px; transition: transform .12s; flex: none; }
  .lcaret.open { transform: rotate(90deg); }
  .lc { font-size: 11px; color: var(--faint); font-weight: 600; white-space: nowrap; }

  .nrow { display: flex; align-items: center; gap: 6px; padding: 0 10px 0 18px; height: 27px; }
  .nrow:hover { background: var(--elev); }
  .tt { flex: 1; min-width: 0; text-align: left; background: none; box-shadow: none; padding: 0; color: var(--text); font-size: 12.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt:hover { filter: none; color: var(--accent); }
  .del { background: none; box-shadow: none; padding: 0 3px; color: var(--faint); font-size: 12px; flex: none; opacity: 0; }
  .nrow:hover .del { opacity: 1; }
  .del:hover { color: var(--bad); filter: none; }
  .tempty { padding: 12px; color: var(--muted); font-size: 12.5px; }

  /* ── Workflow I/O pane ── */
  .io-panel { border-top: 1px solid var(--border-soft); margin-top: 4px; }
  .io-hd {
    width: 100%; display: flex; align-items: center; gap: 6px;
    background: none; box-shadow: none; padding: 8px 10px;
    font-size: 11px; text-transform: uppercase; letter-spacing: .4px;
    font-weight: 700; color: var(--muted); text-align: left;
  }
  .io-hd:hover { color: var(--text); filter: none; }
  .io-sec { padding: 2px 0 6px; border-top: 1px solid var(--border-soft); }
  .io-sec-hd {
    display: block; width: 100%; text-align: left; padding: 5px 10px;
    background: none; box-shadow: none; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .4px;
  }
  .io-sec-hd:hover { filter: none; opacity: .8; }
  .io-node { padding: 2px 10px 4px; }
  .io-cls { display: block; font-size: 10.5px; color: var(--faint); font-style: italic; margin-bottom: 1px; }
  .io-row { display: flex; gap: 6px; align-items: baseline; padding: 1px 0; }
  .io-k { font-size: 10.5px; color: var(--muted); font-family: ui-monospace, monospace; flex: none; }
  .io-v { font-size: 11px; color: var(--text); font-family: ui-monospace, monospace;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
</style>

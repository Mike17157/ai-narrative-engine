<script>
  import { useSvelteFlow } from '@xyflow/svelte';
  import { img, deleteNode } from '$lib/images.svelte.js';

  // The control surface: the workflow as ordered STAGES (start → output). Each
  // stage = one depth/column in the layout (grouped by the canvas x-position, so
  // it mirrors the left-to-right layers). Every block appears exactly once, in
  // its stage. Click = focus on canvas, ✕ = delete, ＋ = add. Fields are edited
  // on the canvas. Must run inside <SvelteFlowProvider>.
  let { nodes, onmutate, onadd, onchainlora } = $props();
  const { setCenter } = useSvelteFlow();
  let q = $state('');
  let collapsed = $state(new Set());

  // Semantic stages: loaders are always Stage 1, the LoRA chain is always Stage 2,
  // everything downstream follows by canvas column. LoRAs get a chain-more button.
  const LOAD = new Set([
    'UNETLoader', 'UnetLoaderGGUF', 'CheckpointLoaderSimple', 'CheckpointLoader', 'CheckpointLoaderSimpleShared',
    'CLIPLoader', 'DualCLIPLoader', 'TripleCLIPLoader', 'CLIPLoaderGGUF', 'DualCLIPLoaderGGUF',
    'VAELoader', 'EmptyLatentImage', 'EmptySD3LatentImage', 'LoadImage', 'LoadImageMask'
  ]);
  const LORA = new Set(['LoraLoader', 'LoraLoaderModelOnly']);
  const roleOf = (n) => (LORA.has(n.data?.classType) ? 'lora' : LOAD.has(n.data?.classType) ? 'load' : 'down');

  let stages = $derived.by(() => {
    const term = q.trim().toLowerCase();
    const ns = term ? nodes.filter((n) => (n.data?.title || '').toLowerCase().includes(term)) : nodes;
    const byX = (a, b) => a.position.x - b.position.x || a.position.y - b.position.y;
    const load = ns.filter((n) => roleOf(n) === 'load').sort(byX);
    const lora = ns.filter((n) => roleOf(n) === 'lora').sort(byX);
    const down = ns.filter((n) => roleOf(n) === 'down');

    // downstream: group by canvas column, merge consecutive same-signature columns
    const byCol = new Map();
    for (const n of down) {
      const key = Math.round(n.position.x);
      (byCol.get(key) ?? byCol.set(key, []).get(key)).push(n);
    }
    const cols = [...byCol.entries()].sort((a, b) => a[0] - b[0]).map(([, l]) => l.sort((a, b) => a.position.y - b.position.y));
    const sig = (l) => [...new Set(l.map((n) => n.data?.title || n.id))].sort().join('|');
    const downStages = [];
    for (const col of cols) {
      const last = downStages[downStages.length - 1];
      if (last && sig(last) === sig(col)) last.push(...col);
      else downStages.push([...col]);
    }

    const out = [];
    if (load.length || !term) out.push({ name: 'Inputs', nodes: load });
    if (lora.length || !term) out.push({ name: 'LoRAs', nodes: lora, lora: true });
    for (const l of downStages) out.push({ name: null, nodes: l });
    return out.map((s, i) => ({ ...s, stage: i + 1 }));
  });

  function toggle(i) { const s = new Set(collapsed); s.has(i) ? s.delete(i) : s.add(i); collapsed = s; }
  function focus(n) { setCenter(n.position.x + (n.width || 230) / 2, n.position.y + (n.height || 80) / 2, { zoom: 1.1, duration: 450 }); }
  function del(n) { deleteNode(n.id); onmutate?.(); }
</script>

<div class="tree">
  <div class="thead">
    <input class="tsearch" placeholder="Find block…" bind:value={q} />
    <button class="addbtn" onclick={onadd} title="Add a block">＋</button>
  </div>
  <div class="tlist">
    {#each stages as s (s.stage)}
      <div class="lvl">
        <div class="lhrow">
          <button class="lh" onclick={() => toggle(s.stage)}>
            <span class="lcaret" class:open={!collapsed.has(s.stage)}>▸</span>
            Stage {s.stage}{#if s.name}<span class="sname">{s.name}</span>{/if}<span class="lc">{s.nodes.length}</span>
          </button>
          {#if s.lora}<button class="chainbtn" onclick={onchainlora} title="Chain another LoRA">＋ LoRA</button>{/if}
        </div>
        {#if !collapsed.has(s.stage)}
          {#each s.nodes as n (n.id)}
            <div class="nrow">
              <button class="tt" onclick={() => focus(n)} title={n.data?.classType}>{n.data?.title || n.id}</button>
              <button class="del" onclick={() => del(n)} title="Delete block" aria-label="Delete">✕</button>
            </div>
          {:else}
            {#if s.lora}<div class="nempty">no LoRAs — ＋ to chain one</div>{/if}
          {/each}
        {/if}
      </div>
    {:else}
      <div class="tempty">no blocks</div>
    {/each}
  </div>
</div>

<style>
  .tree { display: flex; flex-direction: column; height: 74vh; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; overflow: hidden; }
  .thead { display: flex; align-items: center; gap: 6px; padding: 6px; border-bottom: 1px solid var(--border-soft); }
  .tsearch { flex: 1; border: 1px solid var(--border); border-radius: 7px; background: var(--elev); font-size: 12.5px; padding: 6px 9px; }
  .addbtn { flex: none; width: 30px; height: 30px; padding: 0; font-size: 16px; border-radius: 7px; }
  .tlist { flex: 1; overflow: auto; padding: 4px 0; }

  .lvl + .lvl { border-top: 1px solid var(--border-soft); }
  .lhrow { display: flex; align-items: center; }
  .lh {
    display: flex; align-items: center; gap: 7px; flex: 1; min-width: 0; text-align: left; background: none; box-shadow: none;
    padding: 7px 10px; color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; font-weight: 700;
  }
  .lh:hover { color: var(--text); filter: none; background: var(--elev); }
  .lcaret { font-size: 9px; transition: transform .12s; }
  .lcaret.open { transform: rotate(90deg); }
  .sname { color: var(--text); text-transform: none; letter-spacing: 0; font-weight: 600; }
  .lc { margin-left: auto; font-size: 11px; color: var(--faint); font-weight: 600; }
  .chainbtn { flex: none; margin-right: 6px; padding: 3px 8px; font-size: 11px; border-radius: 6px; }
  .nempty { padding: 4px 10px 8px 26px; font-size: 12px; color: var(--faint); }

  .nrow { display: flex; align-items: center; gap: 6px; padding: 0 10px 0 26px; height: 27px; }
  .nrow:hover { background: var(--elev); }
  .tt { flex: 1; min-width: 0; text-align: left; background: none; box-shadow: none; padding: 0; color: var(--text); font-size: 12.5px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tt:hover { filter: none; color: var(--accent); }
  .del { background: none; box-shadow: none; padding: 0 3px; color: var(--faint); font-size: 12px; flex: none; opacity: 0; }
  .nrow:hover .del { opacity: 1; }
  .del:hover { color: var(--bad); filter: none; }
  .tempty { padding: 12px; color: var(--muted); font-size: 12.5px; }
</style>

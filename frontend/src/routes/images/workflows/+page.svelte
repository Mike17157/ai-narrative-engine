<script>
  // Images ▸ Workflows — the unified place to browse + test every image workflow.
  // Left: a searchable list of all `kind: image` models grouped by family (anima_*,
  // flux, wan…). Right: the Wan-style single-render tester for the selected one.
  // Wan is no longer a special tab — it's just another workflow in this list.
  import { app } from '$lib/app.svelte.js';
  import { img, selectWorkflow } from '$lib/images.svelte.js';
  import WorkflowTester from '$lib/components/image/WorkflowTester.svelte';

  let q = $state('');
  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other');

  // Group the image models by family (mirrors the Graph tab's wfGroups), filtered by search.
  let groups = $derived.by(() => {
    const needle = q.trim().toLowerCase();
    const order = [], by = {};
    for (const m of app.models.image || []) {
      if (needle && !m.key.toLowerCase().includes(needle) && !(m.family || '').toLowerCase().includes(needle)) continue;
      const g = famCap(m.family);
      if (!(g in by)) { by[g] = []; order.push(g); }
      by[g].push(m);
    }
    return order.map((g) => ({ label: g, models: by[g].sort((a, b) => a.key.localeCompare(b.key)) }));
  });

  const isVideo = (key) => key === 'wan' || key === 'wan_i2v';
</script>

<div class="wf">
  <aside class="list">
    <input class="search" placeholder="Search workflows…" bind:value={q} />
    {#each groups as g (g.label)}
      <div class="grp">{g.label}</div>
      {#each g.models as m (m.key)}
        <button class="row" class:on={app.activeImage === m.key} onclick={() => selectWorkflow(m.key)}>
          <span class="key">{m.key}</span>
          {#if isVideo(m.key)}<span class="badge">🎞</span>{/if}
        </button>
      {/each}
    {/each}
    {#if !groups.length}<div class="empty">no workflows match “{q}”</div>{/if}
  </aside>

  <section class="main">
    {#if app.activeImage}
      {#if img.loading}
        <div class="ph">Loading workflow…</div>
      {:else}
        {#key app.activeImage}<WorkflowTester />{/key}
      {/if}
    {:else}
      <div class="ph">Pick a workflow on the left to test it.</div>
    {/if}
  </section>
</div>

<style>
  .wf { display: flex; gap: 22px; align-items: flex-start; }
  .list { width: 240px; flex: none; display: flex; flex-direction: column; gap: 2px; }
  .search { width: 100%; box-sizing: border-box; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); margin-bottom: 8px; }
  .grp { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin: 12px 0 3px; padding: 0 4px; }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 6px; width: 100%; text-align: left; border: 1px solid transparent; background: none; color: var(--text); padding: 7px 10px; border-radius: 8px; font-size: 13px; box-shadow: none; }
  .row:hover { background: var(--elev); }
  .row.on { background: var(--elev-2); border-color: var(--border); }
  .key { font-family: var(--mono, monospace); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 11px; flex: none; }
  .empty { font-size: 12.5px; color: var(--faint); padding: 8px 4px; }
  .main { flex: 1; min-width: 0; }
  .ph { color: var(--faint); font-size: 13px; padding: 20px 0; }
</style>

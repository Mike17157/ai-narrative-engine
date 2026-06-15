<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';

  // A similarity-ordered list of LoRAs: variants (epochs/versions) folded,
  // similar models adjacent, each showing its nearest neighbours by tag overlap.
  let entries = $state([]);
  let loading = $state(true);
  let err = $state(null);
  let q = $state('');
  let open = $state(null); // expanded entry label

  async function load() {
    loading = true; err = null;
    try {
      const d = await get('/loras/clusters');
      if (d.error) err = d.error;
      entries = d.entries || [];
    } catch (e) { err = String(e); }
    loading = false;
  }
  onMount(load);

  let shown = $derived(
    q.trim()
      ? entries.filter((e) => (e.label + ' ' + e.tags.join(' ')).toLowerCase().includes(q.toLowerCase()))
      : entries
  );
  function simClass(s) { return s >= 0.9 ? 'hi' : s >= 0.75 ? 'mid' : 'lo'; }
</script>

<div class="bar">
  <span class="hint">Your LoRAs ordered by similarity (neighbours sit together); epochs/versions are folded. Built from embedded training tags.</span>
  <input class="search" bind:value={q} placeholder="filter by name / tag…" />
  <button class="ghost sm" onclick={load} disabled={loading}>{loading ? 'Building…' : '↻ Rebuild'}</button>
</div>

{#if err}<div class="err">Couldn’t build the list: {err}</div>{/if}
{#if loading}
  <div class="center">Embedding LoRA tags…</div>
{:else if !shown.length}
  <div class="center">{q ? 'No matches.' : 'No tagged LoRAs found.'}</div>
{:else}
  <div class="list">
    {#each shown as e (e.label)}
      <div class="row" class:open={open === e.label}>
        <button class="head" onclick={() => (open = open === e.label ? null : e.label)}>
          <span class="name">{e.label}</span>
          {#if e.variants > 1}<span class="badge">{e.variants} variants</span>{/if}
          <span class="cnt">{e.count} tags</span>
          <span class="nbrs">
            {#each e.neighbors.slice(0, 4) as n}<span class="nb {simClass(n.sim)}" title={`${Math.round(n.sim * 100)}% similar`}>{n.label}</span>{/each}
          </span>
        </button>
        {#if open === e.label}
          <div class="body">
            <div class="sec"><span class="lbl">Top tags</span><div class="chips">{#each e.tags as t}<span class="tag">{t}</span>{/each}</div></div>
            <div class="sec"><span class="lbl">Most similar</span>
              <div class="chips">{#each e.neighbors as n}<span class="tag nbchip {simClass(n.sim)}">{n.label} · {Math.round(n.sim * 100)}%</span>{/each}</div>
            </div>
            {#if e.variants > 1}<div class="sec"><span class="lbl">Folded files</span><div class="files">{#each e.ids as id}<code>{id}</code>{/each}</div></div>{/if}
          </div>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<style>
  .bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-bottom: 12px; }
  .hint { font-size: 12.5px; color: var(--muted); flex: 1; min-width: 220px; }
  .search { width: 220px; }
  .err { color: var(--bad); font-size: 12.5px; margin-bottom: 10px; }
  .center { display: grid; place-items: center; height: 40vh; color: var(--muted); font-size: 13px; }

  .list { display: flex; flex-direction: column; gap: 4px; }
  .row { border: 1px solid var(--border-soft); border-radius: 10px; background: var(--panel); overflow: hidden; }
  .row.open { border-color: var(--border); }
  .head {
    display: flex; align-items: center; gap: 10px; width: 100%; text-align: left; background: none;
    box-shadow: none; padding: 9px 12px; cursor: pointer; color: var(--text); font: inherit;
  }
  .head:hover { background: var(--elev); }
  .name { font-weight: 560; font-size: 13px; flex: none; max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 10.5px; color: var(--accent); border: 1px solid var(--border-soft); border-radius: 999px; padding: 1px 7px; flex: none; }
  .cnt { font-size: 11px; color: var(--faint); flex: none; }
  .nbrs { display: flex; gap: 5px; margin-left: auto; overflow: hidden; flex-wrap: nowrap; }
  .nb { font-size: 11px; color: var(--muted); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 999px; padding: 1px 8px; white-space: nowrap; }
  .nb.hi { color: #c9a6ff; border-color: rgba(124, 109, 255, .4); }
  .nb.mid { color: #8fcaff; }

  .body { padding: 4px 12px 12px; border-top: 1px solid var(--border-soft); }
  .sec { margin-top: 10px; }
  .lbl { display: block; font-size: 10.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-bottom: 5px; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .tag { font-size: 11.5px; color: var(--accent); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px; }
  .nbchip { color: var(--muted); } .nbchip.hi { color: #c9a6ff; } .nbchip.mid { color: #8fcaff; }
  .files { display: flex; flex-direction: column; gap: 3px; }
  .files code { font-size: 11px; color: var(--muted); }
</style>

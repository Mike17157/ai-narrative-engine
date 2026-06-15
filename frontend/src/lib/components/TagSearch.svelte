<script>
  import { get } from '$lib/api.js';

  // Vocabulary search with a tags|characters scope toggle and a debounced autocomplete dropdown.
  // Calls onpick(tag) on Enter / selection. Self-contained.
  let { onpick } = $props();

  let query = $state('');
  let scope = $state('all');     // 'all' | 'character'
  let results = $state([]);
  let open = $state(false);
  let active = $state(-1);

  $effect(() => {
    const q = query, sc = scope;   // track both
    if (!q.trim()) { results = []; open = false; return; }
    const id = setTimeout(async () => {
      const cat = sc === 'character' ? '&cat=character' : '';
      const d = await get('/tags/search?limit=12' + cat + '&q=' + encodeURIComponent(q));
      results = d?.tags || []; active = results.length ? 0 : -1; open = results.length > 0;
    }, 1000);   // 1s debounce — update once typing pauses
    return () => clearTimeout(id);
  });

  function pick(tag) { onpick?.(tag); query = ''; results = []; open = false; active = -1; }
  function onkey(e) {
    if (e.key === 'Enter') { e.preventDefault(); if (open && active >= 0 && results[active]) pick(results[active].tag); else if (query.trim()) pick(query); }
    else if (e.key === 'ArrowDown' && open) { e.preventDefault(); active = Math.min(active + 1, results.length - 1); }
    else if (e.key === 'ArrowUp' && open) { e.preventDefault(); active = Math.max(active - 1, 0); }
    else if (e.key === 'Escape' && open) { e.stopPropagation(); open = false; }   // close dropdown, don't cancel modal
  }
  const fmt = (n) => (n >= 1000 ? Math.round(n / 1000) + 'k' : n);
</script>

<div class="search">
  <div class="srow">
    <div class="scope">
      <button class:on={scope === 'all'} onclick={() => (scope = 'all')}>tags</button>
      <button class:on={scope === 'character'} onclick={() => (scope = 'character')}>characters</button>
    </div>
    <input placeholder={scope === 'character' ? 'search a character…' : 'search any tag…'}
      bind:value={query} onkeydown={onkey} />
  </div>
  {#if open}
    <div class="pop">
      {#each results as r, i (r.name)}
        <div class="opt" class:on={i === active} onmousedown={(e) => { e.preventDefault(); pick(r.tag); }}>
          <span>{r.tag}</span><span class="cnt">{fmt(r.count)}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .search { position: relative; }
  .srow { display: flex; gap: 8px; align-items: stretch; }
  .scope { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; flex: none; }
  .scope button { background: var(--elev); border: 0; color: var(--muted); padding: 0 11px; font-size: 12px; box-shadow: none; cursor: pointer; }
  .scope button.on { background: var(--accent); color: #fff; }
  .search input { flex: 1; min-width: 0; background: var(--bg); border: 1px solid var(--border); border-radius: 9px; padding: 8px 11px; color: var(--text); font-size: 13px; }
  .search input:focus { border-color: var(--accent); outline: none; }
  .pop { position: absolute; z-index: 5; left: 0; right: 0; top: 100%; margin-top: 5px; background: var(--elev);
    border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); max-height: 240px; overflow: auto; padding: 4px; }
  .opt { display: flex; justify-content: space-between; gap: 8px; padding: 6px 9px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  .opt.on, .opt:hover { background: var(--elev-2); }
  .opt .cnt { color: var(--faint); font-size: 11px; }
</style>

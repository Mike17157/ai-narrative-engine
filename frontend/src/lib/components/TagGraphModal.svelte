<script>
  import { get } from '$lib/api.js';
  import { pickerState, resolveGraphPicker } from '$lib/graphpicker.svelte.js';

  // Graph-driven prompt editor (faceted suggestion panel). Shows the current prompt as chips; click
  // a chip to TARGET it for replacement, ✕ to remove. Search the vocabulary or click a graph
  // suggestion to add (or swap the target). Apply returns the edited tag array via the store.
  const norm = (s) => (s || '').toString().trim().toLowerCase().replace(/\s+/g, ' ');
  const dedupe = (a) => { const seen = new Set(), out = []; for (const t of a) { const k = norm(t); if (t && !seen.has(k)) { seen.add(k); out.push(t); } } return out; };

  let tags = $state([]);
  let kind = $state('clothing');
  let target = $state(null);      // the chip selected for replacement (or null = add mode)
  let suggestions = $state({});   // { facet: [tag, ...] }
  let busy = $state(false);
  let seeded = false;

  // query / autocomplete
  let query = $state('');
  let results = $state([]);
  let open = $state(false);
  let active = $state(-1);

  // seed local state on the rising edge of `open`
  $effect(() => {
    if (pickerState.open && !seeded) {
      seeded = true;
      tags = pickerState.tags.slice();
      kind = pickerState.kind;
      target = pickerState.target || null; query = ''; results = []; open = false; suggestions = {};
      refreshSuggestions();
    }
    if (!pickerState.open) seeded = false;
  });

  let sugTimer = null;
  function scheduleSuggestions() { clearTimeout(sugTimer); sugTimer = setTimeout(refreshSuggestions, 180); }
  async function refreshSuggestions() {
    const seed = tags.join(',');
    if (!seed) { suggestions = {}; return; }
    busy = true;
    try {
      const d = await get('/api/tags/related?kind=' + kind + '&tags=' + encodeURIComponent(seed));
      suggestions = d?.palette || {};
    } catch { suggestions = {}; }
    busy = false;
  }

  function applyTag(t) {
    t = (t || '').trim(); if (!t) return;
    if (target) { tags = dedupe(tags.map((x) => (norm(x) === norm(target) ? t : x))); target = null; }
    else { tags = dedupe([...tags, t]); }
    query = ''; results = []; open = false; active = -1;
    scheduleSuggestions();
  }
  function removeChip(t) { tags = tags.filter((x) => norm(x) !== norm(t)); if (target && norm(target) === norm(t)) target = null; scheduleSuggestions(); }
  function targetChip(t) { target = (target && norm(target) === norm(t)) ? null : t; }

  // search the full vocabulary
  $effect(() => {
    const q = query;
    if (!q.trim()) { results = []; open = false; return; }
    const id = setTimeout(async () => {
      const d = await get('/api/tags/search?limit=12&q=' + encodeURIComponent(q));
      results = d?.tags || []; active = results.length ? 0 : -1; open = results.length > 0;
    }, 140);
    return () => clearTimeout(id);
  });
  function onkey(e) {
    if (e.key === 'Enter') { e.preventDefault(); if (open && active >= 0 && results[active]) applyTag(results[active].tag); else if (query.trim()) applyTag(query); }
    else if (e.key === 'ArrowDown' && open) { e.preventDefault(); active = Math.min(active + 1, results.length - 1); }
    else if (e.key === 'ArrowUp' && open) { e.preventDefault(); active = Math.max(active - 1, 0); }
    else if (e.key === 'Escape') { if (open) open = false; else cancel(); }
  }

  function setKind(k) { if (k !== kind) { kind = k; refreshSuggestions(); } }
  const apply = () => resolveGraphPicker(dedupe(tags));
  const cancel = () => resolveGraphPicker(null);

  // order facets so the targeted tag's facet leads
  let facetOrder = $derived(Object.keys(suggestions));
</script>

{#if pickerState.open}
  <div class="overlay" onclick={cancel} role="presentation">
    <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="head">
        <h3 class="title">{pickerState.title}</h3>
        <div class="kinds">
          <button class:on={kind === 'clothing'} onclick={() => setKind('clothing')}>clothing</button>
          <button class:on={kind === 'appearance'} onclick={() => setKind('appearance')}>appearance</button>
        </div>
      </div>

      <div class="chips">
        {#each tags as t (t)}
          <span class="chip" class:target={target && norm(target) === norm(t)}>
            <button class="lbl" onclick={() => targetChip(t)} title="select to replace">{t}</button>
            <button class="x" onclick={() => removeChip(t)} title="remove">×</button>
          </span>
        {/each}
        {#if !tags.length}<span class="empty">no tags yet — search or pick below</span>{/if}
      </div>

      <div class="status">
        {#if target}Replacing <b>{target}</b> — pick a tag to swap it in. <button class="link" onclick={() => (target = null)}>cancel</button>
        {:else}Click a chip to replace it, or add tags below.{/if}
      </div>

      <div class="search">
        <input placeholder="search any tag…" bind:value={query} onkeydown={onkey} />
        {#if open}
          <div class="pop">
            {#each results as r, i (r.name)}
              <div class="opt" class:on={i === active} onmousedown={(e) => { e.preventDefault(); applyTag(r.tag); }}>
                <span>{r.tag}</span><span class="cnt">{r.count >= 1000 ? Math.round(r.count / 1000) + 'k' : r.count}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>

      <div class="sug">
        {#if busy && !facetOrder.length}<p class="lo">finding compatible tags…</p>
        {:else if !facetOrder.length}<p class="lo">no graph suggestions for these tags — use search above.</p>
        {:else}
          {#each facetOrder as f (f)}
            <div class="facet">
              <div class="fname">{f}</div>
              <div class="pills">
                {#each suggestions[f] as t (t)}
                  <button class="pill" onclick={() => applyTag(t)} title={target ? 'replace ' + target : 'add'}>{t}</button>
                {/each}
              </div>
            </div>
          {/each}
        {/if}
      </div>

      <div class="acts">
        <span class="lo">{tags.length} tag{tags.length === 1 ? '' : 's'}</span>
        <button class="ghost" onclick={cancel}>Cancel</button>
        <button onclick={apply}>Apply</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (pickerState.open && e.key === 'Escape' && !open) cancel(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 90; background: rgba(6,8,12,.62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(94vw, 720px); max-height: 88vh; display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg, 14px);
    box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55)); padding: 16px 18px; animation: pop .13s ease; }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .title { margin: 0; font-size: 16px; font-weight: 680; color: var(--text); flex: 1; }
  .kinds { display: flex; gap: 0; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .kinds button { background: var(--elev); border: 0; color: var(--muted); padding: 5px 12px; font-size: 12px; box-shadow: none; }
  .kinds button.on { background: var(--accent); color: #fff; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 9px; border: 1px solid var(--border);
    border-radius: 10px; background: var(--bg); min-height: 44px; max-height: 22vh; overflow: auto; }
  .chip { display: inline-flex; align-items: center; border: 1px solid var(--border-soft); background: var(--elev-2);
    border-radius: 8px; overflow: hidden; }
  .chip.target { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .chip .lbl { background: none; border: 0; color: var(--text); font-size: 12.5px; padding: 3px 4px 3px 9px; box-shadow: none; cursor: pointer; }
  .chip .x { background: none; border: 0; color: var(--muted); font-size: 15px; padding: 0 7px 0 3px; box-shadow: none; cursor: pointer; }
  .chip .x:hover { color: #ff8a8a; }
  .empty { color: var(--faint); font-size: 12.5px; align-self: center; padding: 2px 4px; }
  .status { font-size: 12px; color: var(--muted); margin: 8px 2px; }
  .status b { color: var(--text); }
  .link { background: none; border: 0; color: var(--accent); box-shadow: none; cursor: pointer; font-size: 12px; padding: 0 2px; }
  .search { position: relative; }
  .search input { width: 100%; background: var(--bg); border: 1px solid var(--border); border-radius: 9px;
    padding: 8px 11px; color: var(--text); font-size: 13px; }
  .search input:focus { border-color: var(--accent); outline: none; }
  .pop { position: absolute; z-index: 5; left: 0; right: 0; top: 100%; margin-top: 5px; background: var(--elev);
    border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); max-height: 240px; overflow: auto; padding: 4px; }
  .opt { display: flex; justify-content: space-between; gap: 8px; padding: 6px 9px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  .opt.on, .opt:hover { background: var(--elev-2); }
  .opt .cnt { color: var(--faint); font-size: 11px; }
  .sug { flex: 1; overflow: auto; margin: 10px 0; padding-right: 4px; }
  .facet { margin-bottom: 10px; }
  .fname { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 5px; }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--elev-2);
    border: 1px solid var(--border); color: var(--text); box-shadow: none; cursor: pointer; }
  .pill:hover { border-color: var(--accent); color: var(--accent); filter: none; }
  .lo { color: var(--faint); font-size: 12px; }
  .acts { display: flex; align-items: center; gap: 10px; justify-content: flex-end; padding-top: 10px; border-top: 1px solid var(--border-soft); }
  .acts .lo { margin-right: auto; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

<script>
  import { get, post } from '$lib/api.js';
  import { openGraphPicker } from '$lib/graphpicker.svelte.js';

  // Booru-tag editor for a comma-separated prompt. Type to search the REAL Danbooru
  // vocabulary (ranked by post count); each tag is coloured by validity (known / will-be-
  // snapped / unknown) via /api/tags/snap; unknown tags get one-click suggestions. The
  // value is a plain comma string so this drops in for a <textarea> — edits flow out via
  // onchange(newValue). charge nothing to render: validation is debounced + non-blocking.
  // `kind` ('clothing'|'appearance') steers the graph picker (✦) opened from this box.
  let { value = '', onchange, placeholder = 'type a tag…', kind = 'clothing' } = $props();

  async function openPicker(targetTag = null) {
    const r = await openGraphPicker({ tags: chips, kind, target: targetTag,
      title: targetTag ? 'Replace tag via graph' : 'Edit prompt via graph' });
    if (r) commit(r);
  }

  const norm = (s) => (s || '').toString().trim().toLowerCase().replace(/\s+/g, ' ');
  const split = (v) => (v || '').split(',').map((s) => s.trim()).filter(Boolean);
  const join = (a) => a.join(', ');
  let chips = $derived(split(value));

  function commit(arr) {
    const seen = new Set(), out = [];
    for (const t of arr) {
      if (!t) continue;
      if (norm(t) === 'break') { out.push('BREAK'); continue; }   // region separator — keep every one
      const k = norm(t);
      if (!seen.has(k)) { seen.add(k); out.push(t); }
    }
    onchange?.(join(out));
  }
  function add(tag) { const t = (tag || '').trim(); if (t) commit([...chips, t]); query = ''; results = []; open = false; active = -1; }
  function removeAt(i) { const a = [...chips]; a.splice(i, 1); commit(a); }

  // ---- live validation (debounced) → per-tag status keyed by normalized text ----
  let statusMap = $state({});      // norm(input) -> { status, display, suggestions }
  $effect(() => {
    const v = value;               // track
    const id = setTimeout(async () => {
      const r = await post('/tags/snap', { prompt: v });
      if (r.ok && r.data) {
        const m = {};
        for (const it of r.data.items || []) m[norm(it.input)] = it;
        statusMap = m;
      }
    }, 250);
    return () => clearTimeout(id);
  });
  const stat = (tag) => statusMap[norm(tag)]?.status || '';
  let unknowns = $derived(Object.values(statusMap).filter((i) => i.status === 'unknown'));

  // ---- autocomplete ----
  let query = $state('');
  let results = $state([]);
  let open = $state(false);
  let active = $state(-1);
  let root;

  $effect(() => {
    const q = query;
    if (!q.trim()) { results = []; open = false; return; }
    const id = setTimeout(async () => {
      const d = await get('/tags/search?q=' + encodeURIComponent(q) + '&limit=12');
      results = d?.tags || []; active = results.length ? 0 : -1; open = results.length > 0;
    }, 140);
    return () => clearTimeout(id);
  });

  function onkey(e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      if (open && active >= 0 && results[active]) add(results[active].tag);
      else if (query.trim()) add(query);
    } else if (e.key === 'Backspace' && !query && chips.length) {
      removeAt(chips.length - 1);
    } else if (e.key === 'ArrowDown' && open) { e.preventDefault(); active = Math.min(active + 1, results.length - 1); }
    else if (e.key === 'ArrowUp' && open) { e.preventDefault(); active = Math.max(active - 1, 0); }
    else if (e.key === 'Escape') { open = false; }
  }
  async function snapAll() {
    const r = await post('/tags/snap', { prompt: value });
    if (r.ok && r.data?.prompt !== undefined) onchange?.(r.data.prompt);
  }
  function fmtCount(n) { return n >= 1000 ? Math.round(n / 1000) + 'k' : '' + n; }

  $effect(() => {
    function onDoc(e) { if (root && !root.contains(e.target)) open = false; }
    document.addEventListener('click', onDoc);
    return () => document.removeEventListener('click', onDoc);
  });
</script>

<div class="ti" bind:this={root}>
  <div class="box">
    {#each chips as tag, i (tag + i)}
      {#if norm(tag) === 'break'}
        <span class="brk" title="region separator (BREAK)">BREAK<button class="x" onclick={() => removeAt(i)} title="remove">×</button></span>
      {:else}
      {@const st = stat(tag)}
      <span class="chip {st}" ondblclick={() => openPicker(tag)}
        title={st === 'unknown' ? 'not a known tag' : (st === 'ok' || st === 'control' || st === '') ? 'double-click to swap via graph' : '→ ' + (statusMap[norm(tag)]?.display || '')}>
        {tag}{#if st && st !== 'ok' && st !== 'control' && st !== 'unknown'}<i class="arrow">→{statusMap[norm(tag)]?.display}</i>{/if}
        <button class="x" onclick={() => removeAt(i)} title="remove">×</button>
      </span>
      {/if}
    {/each}
    <input
      class="entry" {placeholder}
      bind:value={query}
      onkeydown={onkey}
      onfocus={() => { if (results.length) open = true; }} />
    {#if open}
      <div class="pop">
        {#each results as r, i (r.name)}
          <div class="opt" class:on={i === active} onmousedown={(e) => { e.preventDefault(); add(r.tag); }}>
            <span class="t">{r.tag}</span>
            <span class="meta">{#if r.category}<span class="cat {r.category}">{r.category}</span>{/if}<span class="cnt">{fmtCount(r.count)}</span></span>
          </div>
        {/each}
      </div>
    {/if}
  </div>

  <div class="bar">
    {#if unknowns.length}
      <span class="free">{unknowns.length} free-text tag{unknowns.length === 1 ? '' : 's'} <span class="lo">(used as written)</span></span>
    {:else}
      <span class="ok">✓ all booru tags</span>
    {/if}
    <button class="graph" onclick={() => openPicker()} title="browse the tag graph — add or swap tags by what they pair with">✦ graph</button>
    <button class="snap" onclick={snapAll} title="snap every tag onto its real Danbooru form (aliases, typos, word order)">⇥ snap to real tags</button>
  </div>
  <!-- free-text tags are kept as written; correct them via ⇥ snap or ✦ graph if you want. -->
</div>

<style>
  .ti { width: 100%; }
  .box {
    position: relative; display: flex; flex-wrap: wrap; gap: 5px; align-items: center;
    background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 7px 8px; min-height: 38px;
  }
  .chip {
    display: inline-flex; align-items: center; gap: 4px; font-size: 12.5px; line-height: 1.3;
    border-radius: 7px; padding: 2px 6px; border: 1px solid var(--border-soft); background: var(--elev-2);
    color: var(--text);
  }
  /* known = quiet; snapped (alias/typo/reorder) = amber; unknown = red */
  .chip.ok, .chip.control { border-color: rgba(120,200,140,.35); background: rgba(120,200,140,.10); }
  .chip.alias, .chip.reorder, .chip.typo { border-color: rgba(230,180,90,.45); background: rgba(230,180,90,.12); color: #f0d49a; }
  /* not a canonical booru tag — fine, used as written. Neutral dashed, not an error. */
  .chip.unknown { border-style: dashed; border-color: var(--border); background: var(--elev); color: var(--muted); }
  .chip .arrow { font-style: normal; opacity: .7; font-size: 11px; margin-left: 2px; }
  .chip .x { background: none; border: 0; color: inherit; opacity: .6; cursor: pointer; padding: 0 1px; font-size: 14px; }
  .chip .x:hover { opacity: 1; }
  .brk { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; letter-spacing: .5px;
    color: var(--accent); background: rgba(109,140,255,.10); border: 1px dashed rgba(109,140,255,.5);
    border-radius: 7px; padding: 2px 5px 2px 8px; }
  .brk .x { background: none; border: 0; color: inherit; opacity: .6; cursor: pointer; padding: 0 1px; font-size: 13px; }
  .brk .x:hover { opacity: 1; }
  .entry { flex: 1; min-width: 120px; border: 0; background: none; padding: 2px; font-size: 12.5px; }
  .entry:focus { outline: none; }
  .pop {
    position: absolute; z-index: 40; left: 0; right: 0; top: 100%; margin-top: 5px;
    background: var(--elev); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); max-height: 280px; overflow: auto; padding: 4px;
  }
  .opt { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 9px; border-radius: 7px; cursor: pointer; font-size: 13px; }
  .opt.on, .opt:hover { background: var(--elev-2); }
  .opt .t { color: var(--text); }
  .meta { display: inline-flex; align-items: center; gap: 7px; }
  .cnt { color: var(--faint); font-size: 11px; font-variant-numeric: tabular-nums; }
  .cat { font-size: 9.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); opacity: .7; }
  .cat.character { color: #9fb3d8; } .cat.copyright { color: #c8a8e0; } .cat.artist { color: #e0b48a; }
  .bar { display: flex; align-items: center; gap: 10px; margin: 5px 1px 0; font-size: 11.5px; }
  .bar .free { color: var(--muted); } .bar .free .lo { color: var(--faint); }
  .bar .ok { color: #8fc7a0; }
  .snap, .graph { font-size: 11px; padding: 3px 8px; border-radius: 7px; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); }
  .graph { margin-left: auto; }
  .snap:hover, .graph:hover { color: var(--accent); border-color: var(--accent); }
</style>

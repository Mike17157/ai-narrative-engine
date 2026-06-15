<script>
  import { get, post } from '$lib/api.js';

  // Booru-tag editor for a comma-separated prompt. Type to search the REAL Danbooru
  // vocabulary (ranked by post count); each tag is coloured by validity (known / will-be-
  // snapped / unknown) via /api/tags/snap; unknown tags get one-click suggestions. The
  // value is a plain comma string so this drops in for a <textarea> — edits flow out via
  // onchange(newValue). charge nothing to render: validation is debounced + non-blocking.
  let { value = '', onchange, placeholder = 'type a tag…' } = $props();

  const norm = (s) => (s || '').toString().trim().toLowerCase().replace(/\s+/g, ' ');
  const split = (v) => (v || '').split(',').map((s) => s.trim()).filter(Boolean);
  const join = (a) => a.join(', ');
  let chips = $derived(split(value));

  function commit(arr) {
    const seen = new Set(), out = [];
    for (const t of arr) { const k = norm(t); if (t && !seen.has(k)) { seen.add(k); out.push(t); } }
    onchange?.(join(out));
  }
  function add(tag) { const t = (tag || '').trim(); if (t) commit([...chips, t]); query = ''; results = []; open = false; active = -1; }
  function removeAt(i) { const a = [...chips]; a.splice(i, 1); commit(a); }
  function replaceTag(input, tag) { commit(chips.map((c) => (norm(c) === norm(input) ? tag : c))); }
  function removeTag(input) { commit(chips.filter((c) => norm(c) !== norm(input))); }

  // ---- live validation (debounced) → per-tag status keyed by normalized text ----
  let statusMap = $state({});      // norm(input) -> { status, display, suggestions }
  $effect(() => {
    const v = value;               // track
    const id = setTimeout(async () => {
      const r = await post('/api/tags/snap', { prompt: v });
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
      const d = await get('/api/tags/search?q=' + encodeURIComponent(q) + '&limit=12');
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
    const r = await post('/api/tags/snap', { prompt: value });
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
      {@const st = stat(tag)}
      <span class="chip {st}" title={st === 'unknown' ? 'not a known tag' : st === 'ok' || st === 'control' || st === '' ? '' : '→ ' + (statusMap[norm(tag)]?.display || '')}>
        {tag}{#if st && st !== 'ok' && st !== 'control' && st !== 'unknown'}<i class="arrow">→{statusMap[norm(tag)]?.display}</i>{/if}
        <button class="x" onclick={() => removeAt(i)} title="remove">×</button>
      </span>
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
      <span class="warn">⚠ {unknowns.length} unrecognized</span>
    {:else}
      <span class="ok">✓ all tags recognized</span>
    {/if}
    <button class="snap" onclick={snapAll} title="snap every tag onto its real Danbooru form (aliases, typos, word order)">⇥ snap to real tags</button>
  </div>

  {#each unknowns as u (u.input)}
    <div class="issue">
      <span class="bad">{u.display}</span>
      <span class="sep">→</span>
      {#each (u.suggestions || []).slice(0, 5) as s (s.name)}
        <button class="sug" onmousedown={(e) => { e.preventDefault(); replaceTag(u.input, s.tag); }} title="{fmtCount(s.count)} posts">{s.tag}</button>
      {/each}
      <button class="drop" onmousedown={(e) => { e.preventDefault(); removeTag(u.input); }}>remove</button>
    </div>
  {/each}
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
  .chip.unknown { border-color: rgba(230,110,110,.5); background: rgba(230,110,110,.12); color: #f3b0b0; }
  .chip .arrow { font-style: normal; opacity: .7; font-size: 11px; margin-left: 2px; }
  .chip .x { background: none; border: 0; color: inherit; opacity: .6; cursor: pointer; padding: 0 1px; font-size: 14px; box-shadow: none; }
  .chip .x:hover { opacity: 1; }
  .entry { flex: 1; min-width: 120px; border: 0; background: none; padding: 2px; font-size: 12.5px; box-shadow: none; }
  .entry:focus { box-shadow: none; outline: none; }
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
  .bar .warn { color: #f0c074; } .bar .ok { color: #8fc7a0; }
  .snap { margin-left: auto; font-size: 11px; padding: 3px 8px; border-radius: 7px; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); box-shadow: none; }
  .snap:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .issue { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin: 5px 1px 0; font-size: 12px; }
  .issue .bad { color: #f3b0b0; }
  .issue .sep { color: var(--faint); }
  .sug { font-size: 11.5px; padding: 2px 7px; border-radius: 6px; background: rgba(109,140,255,.14); border: 1px solid rgba(109,140,255,.3); color: #cdd8ff; box-shadow: none; }
  .sug:hover { background: rgba(109,140,255,.26); filter: none; }
  .drop { font-size: 11px; padding: 2px 7px; border-radius: 6px; background: none; border: 1px solid var(--border); color: var(--muted); box-shadow: none; }
  .drop:hover { color: #f3b0b0; border-color: rgba(230,110,110,.5); filter: none; }
</style>

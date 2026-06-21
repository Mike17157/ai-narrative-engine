<script>
  import { onMount, tick } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get, post, put, patch, del } from '$lib/api.js';

  // Lorebook manager. Left: the book library (filter by rating/category, search, create,
  // import). Right: the selected book — its metadata + keyword-triggered entries, with a
  // retrieval test box. Everything auto-saves (debounced); no Save buttons.

  const CATEGORIES = ['world', 'story', 'rpg', 'character', 'craft', 'intimacy', 'guard', 'function'];

  // ── Function books (entries whose content is a graph-op spec) ──────────────
  let isFnBook = $derived(detail?.book?.category === 'function');
  function parseFn(content) {
    try { const s = JSON.parse(content); return (s && Array.isArray(s.ops)) ? s : null; }
    catch { return null; }
  }
  const FN_TEMPLATE = JSON.stringify({
    fn: 'my_function',
    describe: 'what this function does to the graph',
    params: { id: 'a beat id', value: 'new text' },
    ops: [{ op: 'set', path: '/nodes/#{{id}}/title', value: '{{value}}' }],
  }, null, 2);
  async function addFunction() {
    const r = await put('/lorebooks/' + encodeURIComponent(selId) + '/entries',
      { title: 'new_function', keywords: [], content: FN_TEMPLATE, facet: 'fn', priority: 1, enabled: true });
    if (r.ok && r.data?.entry) {
      const e = r.data.entry; e._open = true;
      detail.entries = [...detail.entries, e];
      await tick();
      document.getElementById('ent-' + e.id)?.scrollIntoView({ block: 'center' });
    }
  }
  const CAT_ICON = { world: '🌍', story: '📖', rpg: '🎲', character: '🧑', craft: '✒', intimacy: '❤', guard: '🛡' };

  let books = $state([]);
  let selId = $state(null);
  let detail = $state(null);          // { book, entries: [] }
  let search = $state('');
  let loading = $state(true);

  // Filters driven by the subnav query (?rating=sfw|nsfw, ?import=1).
  let ratingFilter = $derived(page.url.searchParams.get('rating') || '');
  let catFilter = $state('');

  let filtered = $derived(
    books.filter((b) =>
      (!ratingFilter || b.rating === ratingFilter) &&
      (!catFilter || b.category === catFilter) &&
      (!search.trim() || (b.name + ' ' + b.id + ' ' + b.description).toLowerCase().includes(search.toLowerCase())))
  );

  // Model presets a book can bind to (Function → Lorebook → Preset). The book's `preset`
  // chooses which model preset runs whatever function/flow uses it.
  let presets = $state([]);

  // Recycle bin: archived books live in their own view.
  let bin = $state(false);
  let binCount = $state(0);
  async function loadBinCount() {
    try { binCount = ((await get('/lorebooks?archived=1')).books || []).length; } catch { binCount = 0; }
  }
  async function toggleBin() { bin = !bin; selId = null; detail = null; catFilter = ''; await loadBooks(); }

  onMount(async () => {
    await loadBooks();
    await loadBinCount();
    try { presets = (await get('/presets')).presets || []; } catch { presets = []; }
    loading = false;
  });

  // Open the import modal whenever we land here with ?import=1. onMount fires only on the
  // first mount, so this $effect also catches the subnav's "Import" link being clicked
  // while already on /lorebooks (no remount → onMount wouldn't re-run).
  $effect(() => {
    if (page.url.searchParams.get('import')) importOpen = true;
  });

  async function loadBooks() {
    const r = await get('/lorebooks' + (bin ? '?archived=1' : ''));
    books = r?.books || [];
    if (!selId && books.length) selectBook(books[0].id);
    else if (selId) { const b = books.find((x) => x.id === selId); if (!b) detail = null, selId = null; }
  }

  async function selectBook(id) {
    selId = id;
    detail = null;
    entSel = -1;
    const r = await get('/lorebooks/' + encodeURIComponent(id));
    if (r?.book) detail = { book: r.book, entries: r.entries || [] };
  }

  // Textarea that grows to fit its content instead of scrolling internally. Pass the bound
  // value so `use:autosize={value}` re-measures when the text changes externally (e.g. when
  // arrow-browsing opens a different entry).
  function autosize(node) {
    const fit = () => { node.style.height = 'auto'; node.style.height = node.scrollHeight + 'px'; };
    node.style.overflowY = 'hidden';
    node.style.resize = 'none';
    fit();
    node.addEventListener('input', fit);
    return { update: fit, destroy: () => node.removeEventListener('input', fit) };
  }

  // ── Keyboard browse (↑/↓ through entries, centered) ──────────────────────
  let entSel = $state(-1);   // index of the keyboard-selected entry, -1 = none

  async function selectEnt(i) {
    if (!detail || !detail.entries.length) return;
    const n = detail.entries.length;
    entSel = Math.max(0, Math.min(n - 1, i));
    const e = detail.entries[entSel];
    e._open = true;                       // reveal it so browsing shows the content
    detail.entries = detail.entries;
    await tick();
    document.getElementById('ent-' + e.id)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function onEntriesKey(ev) {
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    // Only grab the arrows once an entry is actually selected (click one to start browsing).
    if (entSel < 0 || !detail || !detail.entries.length) return;
    // Don't hijack arrows while typing in a field.
    const t = ev.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return;
    ev.preventDefault();
    selectEnt(entSel + (ev.key === 'ArrowDown' ? 1 : -1));
  }

  // ── Book metadata (auto-save) ────────────────────────────────────────────
  let metaTimer = null, metaMsg = $state('');
  function touchMeta() {
    clearTimeout(metaTimer);
    metaMsg = '…';
    metaTimer = setTimeout(saveMeta, 600);
  }
  async function saveMeta() {
    if (!detail) return;
    const { name, description, rating, category, enabled, preset, scope } = detail.book;
    const r = await patch('/lorebooks/' + encodeURIComponent(selId), { name, description, rating, category, enabled, preset: preset || '', scope: scope || 'global' });
    if (r.ok) { metaMsg = '✓'; await refreshCounts(); }
    else metaMsg = '✗';
  }
  async function refreshCounts() {
    const r = await get('/lorebooks');
    books = r?.books || [];
  }

  async function createBook() {
    const r = await post('/lorebooks', { name: 'New lorebook', rating: ratingFilter || 'sfw', category: catFilter || 'world' });
    if (r.ok && r.data?.book) { await loadBooks(); selectBook(r.data.book.id); }
  }

  // Soft-delete into the recycle bin (reversible). Works on built-in books too.
  async function archiveBook(b) {
    await post('/lorebooks/' + encodeURIComponent(b.id) + '/archive', { archived: true });
    if (selId === b.id) { selId = null; detail = null; }
    await loadBooks(); await loadBinCount();
  }
  async function restoreBook(b) {
    await post('/lorebooks/' + encodeURIComponent(b.id) + '/archive', { archived: false });
    if (selId === b.id) { selId = null; detail = null; }
    await loadBooks(); await loadBinCount();
  }
  async function deleteForever(b) {
    if (!confirm(`Permanently delete “${b.name}” and all its entries? This can't be undone.`)) return;
    await del('/lorebooks/' + encodeURIComponent(b.id));
    if (selId === b.id) { selId = null; detail = null; }
    await loadBooks(); await loadBinCount();
  }

  // ── Entries (auto-save per entry) ────────────────────────────────────────
  let entryTimers = {};
  function touchEntry(e) {
    clearTimeout(entryTimers[e.id]);
    e._msg = '…';
    entryTimers[e.id] = setTimeout(() => saveEntry(e), 600);
    detail.entries = detail.entries;   // nudge reactivity
  }
  async function saveEntry(e) {
    const payload = {
      id: e.id, title: e.title, content: e.content, priority: Number(e.priority) || 0,
      facet: e.facet || '', enabled: e.enabled !== false,
      trigger: e.trigger || 'input', script: e.script || '',
      keywords: (e._kw ?? (e.keywords || []).join(', ')).split(',').map((s) => s.trim()).filter(Boolean)
    };
    const r = await put('/lorebooks/' + encodeURIComponent(selId) + '/entries', payload);
    e._msg = r.ok ? '✓' : '✗';
    if (r.ok) { e.keywords = payload.keywords; await refreshCounts(); }
    detail.entries = detail.entries;
  }
  async function addEntry() {
    const r = await put('/lorebooks/' + encodeURIComponent(selId) + '/entries',
      { title: 'New entry', keywords: [], content: '', priority: 1, enabled: true });
    if (r.ok && r.data?.entry) {
      const e = r.data.entry; e._open = true;
      detail.entries = [e, ...detail.entries];
      await refreshCounts();
    }
  }
  async function deleteEntry(e) {
    if (!confirm('Delete this entry?')) return;
    await del('/lorebooks/' + encodeURIComponent(selId) + '/entries/' + encodeURIComponent(e.id));
    detail.entries = detail.entries.filter((x) => x.id !== e.id);
    await refreshCounts();
  }

  // ── Retrieval test ───────────────────────────────────────────────────────
  let showTest = $state(false);   // toggled from the header 🔎 Test button
  let testQ = $state('');
  let testHits = $state(null);
  async function runTest() {
    if (!testQ.trim() || !selId) return;
    const r = await post('/lorebooks/retrieve', { query: testQ, books: [selId], top_k: 6 });
    testHits = r.data?.entries || [];
  }

  // ── Import (SillyTavern) ─────────────────────────────────────────────────
  let importOpen = $state(false);
  let importText = $state('');
  let importName = $state('');
  let importRating = $state('sfw');
  let importTarget = $state('');     // '' = new book, else existing book id
  let importBusy = $state(false);
  let importResult = $state(null);
  let importDrag = $state(false);   // dragging a file over the drop zone
  let importFile = $state(null);    // hidden <input type=file> for click-to-browse

  // Read a dropped/picked .json export into the paste box; seed the name from the filename.
  async function loadImportFile(file) {
    if (!file) return;
    if (!/\.json$/i.test(file.name) && file.type && !/json|text/.test(file.type)) {
      importResult = { error: 'Drop a .json lorebook export.' };
      return;
    }
    try { importText = await file.text(); }
    catch { importResult = { error: 'Could not read that file.' }; return; }
    importResult = null;
    if (!importName.trim()) importName = file.name.replace(/\.json$/i, '');
  }

  function onImportDrop(e) {
    e.preventDefault();
    importDrag = false;
    const file = e.dataTransfer?.files?.[0];
    if (file) loadImportFile(file);
  }

  // Close the modal and drop ?import=1 from the URL, so a later filter/nav change to the
  // URL doesn't trip the $effect above and re-open it.
  function closeImport() {
    importOpen = false;
    if (page.url.searchParams.get('import')) {
      const u = new URL(page.url);
      u.searchParams.delete('import');
      goto(u.pathname + u.search, { replaceState: true, noScroll: true, keepFocus: true });
    }
  }

  async function doImport() {
    let parsed;
    try { parsed = JSON.parse(importText); }
    catch { importResult = { error: 'That is not valid JSON.' }; return; }
    importBusy = true; importResult = null;
    let bookId = importTarget;
    if (!bookId) {
      const cr = await post('/lorebooks', { name: importName || 'Imported lorebook', rating: importRating, category: 'world' });
      bookId = cr.data?.book?.id;
      if (!bookId) { importBusy = false; importResult = { error: 'could not create book' }; return; }
    }
    const r = await post('/lorebooks/' + encodeURIComponent(bookId) + '/import', { entries: parsed, rating: importRating });
    importBusy = false;
    importResult = r.data || { error: 'import failed' };
    await loadBooks();
    if (bookId) selectBook(bookId);
  }
</script>

<svelte:window onkeydown={onEntriesKey} />

<div class="mgr">
  <!-- ── Library ──────────────────────────────────────────────────────────── -->
  <aside class="lib">
    <div class="libhead">
      <input class="search" placeholder={bin ? 'Search recycle bin…' : 'Search lorebooks…'} bind:value={search} />
      {#if !bin}<button class="new" onclick={createBook} title="New lorebook">＋</button>{/if}
      <button class="new binbtn" class:on={bin} onclick={toggleBin} title={bin ? 'Back to library' : 'Recycle bin'}>{bin ? '←' : `🗑${binCount ? ' ' + binCount : ''}`}</button>
    </div>
    {#if bin}
      <div class="binbanner">♻ Recycle bin — archived books, hidden from chats & pickers</div>
    {:else}
      <div class="cats">
        <button class="cat" class:on={!catFilter} onclick={() => (catFilter = '')}>All</button>
        {#each CATEGORIES as c}
          <button class="cat" class:on={catFilter === c} onclick={() => (catFilter = catFilter === c ? '' : c)}>{CAT_ICON[c]} {c}</button>
        {/each}
      </div>
      {#if ratingFilter}<div class="ratingnote">showing <b>{ratingFilter.toUpperCase()}</b> only</div>{/if}
    {/if}

    <div class="list">
      {#if loading}<div class="empty">Loading…</div>
      {:else if !filtered.length && bin}<div class="empty">Recycle bin is empty.</div>
      {:else if !filtered.length}<div class="empty">No lorebooks. <button class="link" onclick={createBook}>Create one</button> or <button class="link" onclick={() => (importOpen = true)}>import</button>.</div>
      {/if}
      {#each filtered as b (b.id)}
        <button class="bcard" class:on={selId === b.id} onclick={() => selectBook(b.id)}>
          <div class="brow">
            <span class="bname">{b.name}</span>
            <span class="rate {b.rating}">{b.rating}</span>
          </div>
          <div class="bmeta">
            <span class="bcat">{CAT_ICON[b.category] || '•'} {b.category}</span>
            <span class="bn">{b.entries} {b.entries === 1 ? 'entry' : 'entries'}</span>
            {#if b.builtin}<span class="builtin">built-in</span>{/if}
            {#if !b.enabled}<span class="off">disabled</span>{/if}
          </div>
        </button>
      {/each}
    </div>
    <div
      class="importzone"
      class:over={importDrag}
      role="button"
      tabindex="0"
      ondragover={(e) => { e.preventDefault(); importDrag = true; }}
      ondragleave={() => (importDrag = false)}
      ondrop={(e) => { onImportDrop(e); importOpen = true; }}
      onclick={() => (importOpen = true)}
      onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); importOpen = true; } }}
      title="Drop a SillyTavern .json export, or click to paste"
    >
      <span class="iz-icon">⇪</span>
      <span class="iz-text">{importDrag ? 'Drop to import' : 'Drop lorebook.json'}</span>
      <span class="iz-sub">or click to paste</span>
    </div>
  </aside>

  <!-- ── Editor ───────────────────────────────────────────────────────────── -->
  <section class="ed">
    {#if !detail}
      <div class="placeholder">Select a lorebook, or create one.</div>
    {:else}
      {@const bk = detail.book}
      <header class="edhead">
        <input class="title" bind:value={bk.name} oninput={touchMeta} />
        <span class="savemsg">{metaMsg}</span>
        <button class="testbtn" class:on={showTest} onclick={() => (showTest = !showTest)} title="Test what a passage would retrieve">🔎 Test</button>
        {#if bin}
          <button class="testbtn" onclick={() => restoreBook(bk)} title="Restore to the library">♻ Restore</button>
          <button class="del" onclick={() => deleteForever(bk)} title="Delete forever">🗑</button>
        {:else}
          <button class="del" onclick={() => archiveBook(bk)} title="Move to recycle bin">🗑</button>
        {/if}
      </header>
      <div class="metarow">
        <select bind:value={bk.rating} onchange={touchMeta}>
          <option value="sfw">SFW</option><option value="nsfw">NSFW</option>
        </select>
        <select bind:value={bk.category} onchange={touchMeta}>
          {#each CATEGORIES as c}<option value={c}>{c}</option>{/each}
        </select>
        <label class="psel" title="Global = attachable to any chat. Local = system-native or belongs to one place (not offered in the attach picker).">
          <select bind:value={bk.scope} onchange={touchMeta}>
            <option value="global">🌐 global</option>
            <option value="local">📌 local</option>
          </select>
        </label>
        <label class="tog"><input type="checkbox" bind:checked={bk.enabled} onchange={touchMeta} /> enabled</label>
        <label class="psel" title="Model preset this book drives (Function → Lorebook → Preset)">🎛
          <select bind:value={bk.preset} onchange={touchMeta}>
            <option value="">— no preset —</option>
            {#each presets as p}<option value={p.id}>{p.name || p.id}</option>{/each}
          </select>
        </label>
        {#if bk.builtin}<span class="builtin">built-in</span>{/if}
        <code class="scope">{bk.id}</code>
      </div>
      <textarea class="desc grow" rows="2" placeholder="What is this lorebook for?" bind:value={bk.description} use:autosize={bk.description} oninput={touchMeta}></textarea>

      <!-- Retrieval test (toggled from the header) -->
      {#if showTest}
        <div class="test">
          <div class="testbody">
            <textarea rows="2" placeholder="Paste a line of dialogue or scene text — see what this book would pull…" bind:value={testQ}></textarea>
            <button onclick={runTest}>Test</button>
            {#if testHits !== null}
              {#if testHits.length}
                <ul class="hits">{#each testHits as h}<li><b>{h.title || '(untitled)'}</b> <span class="hkw">{(h.keywords || []).join(', ')}</span></li>{/each}</ul>
              {:else}<div class="empty">No entries matched.</div>{/if}
            {/if}
          </div>
        </div>
      {/if}

      <!-- Entries -->
      <div class="enthead">
        <h3>{isFnBook ? 'Functions' : 'Entries'} <span class="cnt">{detail.entries.length}</span></h3>
        {#if isFnBook}<button class="addent" onclick={addFunction}>＋ Function</button>{/if}
        <button class="addent" onclick={addEntry}>＋ Add entry</button>
      </div>
      {#if isFnBook}
        <p class="fnhelp">Each entry is a graph FUNCTION: its content is a JSON op-spec ({"{ fn, describe, params, ops }"}),
          its keywords are the trigger terms the workshop matches. The model fills <code>{'{{param}}'}</code> placeholders.</p>
      {/if}
      <div class="entries">
        {#each detail.entries as e, ei (e.id)}
          <div id={'ent-' + e.id} class="ent" class:open={e._open} class:dis={e.enabled === false} class:sel={entSel === ei}>
            <div class="entrow" onclick={() => { e._open = !e._open; entSel = ei; detail.entries = detail.entries; }}>
              <span class="caret">{e._open ? '▾' : '▸'}</span>
              <span class="etitle">{e.title || '(untitled)'}</span>
              {#if isFnBook}{@const fn = parseFn(e.content)}
                <span class="fnbadge" class:bad={!fn}>{fn ? 'ƒ ' + (fn.fn || e.title) : '⚠ invalid'}</span>
              {/if}
              <span class="ekw">{(e.keywords || []).slice(0, 4).join(', ')}{(e.keywords || []).length > 4 ? '…' : ''}</span>
              <span class="emsg">{e._msg || ''}</span>
            </div>
            {#if e._open}
              <div class="entbody">
                <label>Title<input bind:value={e.title} oninput={() => touchEntry(e)} /></label>
                <label>Trigger keywords <span class="hint">(comma-separated — any match fires this entry)</span>
                  <input value={e._kw ?? (e.keywords || []).join(', ')}
                    oninput={(ev) => { e._kw = ev.target.value; touchEntry(e); }} placeholder="ghost, haunting, the manor" /></label>
                <div class="entopts">
                  <label class="sel">Match on
                    <select value={e.trigger || 'input'} onchange={(ev) => { e.trigger = ev.target.value; touchEntry(e); }}>
                      <option value="input">input (transcript)</option>
                      <option value="output">output (the reply)</option>
                    </select></label>
                  <label class="sel">Script
                    <select value={e.script || ''} onchange={(ev) => { e.script = ev.target.value; touchEntry(e); }}>
                      <option value="">— none (inject text) —</option>
                      <option value="fallback">fallback (re-run on fallback model)</option>
                    </select></label>
                </div>
                <label>{isFnBook ? 'Function spec (JSON)' : ((e.script && !e.content) ? 'Text (optional)' : 'Content')}
                  <textarea class="grow" class:mono={isFnBook} rows="4" bind:value={e.content} use:autosize={e.content} oninput={() => touchEntry(e)}
                    placeholder={isFnBook ? '{ "fn": "...", "describe": "...", "params": {...}, "ops": [...] }' : (e.script ? 'optional text to also return when this fires' : 'the lore text injected when triggered')}></textarea></label>
                <div class="entopts">
                  <label class="num">Priority<input type="number" bind:value={e.priority} oninput={() => touchEntry(e)} /></label>
                  <label class="num">Facet<input bind:value={e.facet} oninput={() => touchEntry(e)} placeholder="(group; 1 per turn)" /></label>
                  <label class="tog"><input type="checkbox" checked={e.enabled !== false} onchange={(ev) => { e.enabled = ev.target.checked; touchEntry(e); }} /> enabled</label>
                  <button class="delent" onclick={() => deleteEntry(e)}>Delete</button>
                </div>
              </div>
            {/if}
          </div>
        {:else}
          <div class="empty">No entries yet. <button class="link" onclick={addEntry}>Add one</button>.</div>
        {/each}
      </div>
    {/if}
  </section>
</div>

<!-- ── Import modal ──────────────────────────────────────────────────────── -->
{#if importOpen}
  <div class="modal" onclick={(e) => { if (e.target === e.currentTarget) closeImport(); }}>
    <div class="sheet">
      <div class="sheethead"><h2>Import SillyTavern lorebook</h2><button class="x" onclick={closeImport}>✕</button></div>
      <p class="note">Drop a <code>.json</code> export below, or paste one. Prompt-injection and jailbreak payloads are filtered automatically.</p>
      <div class="irow">
        <label>Into
          <select bind:value={importTarget}>
            <option value="">＋ New book…</option>
            {#each books as b}<option value={b.id}>{b.name}</option>{/each}
          </select>
        </label>
        {#if !importTarget}
          <label>Name<input bind:value={importName} placeholder="Imported lorebook" /></label>
          <label>Rating<select bind:value={importRating}><option value="sfw">SFW</option><option value="nsfw">NSFW</option></select></label>
        {/if}
      </div>
      <div
        class="drop"
        class:over={importDrag}
        role="button"
        tabindex="0"
        ondragover={(e) => { e.preventDefault(); importDrag = true; }}
        ondragleave={() => (importDrag = false)}
        ondrop={onImportDrop}
        onclick={() => importFile?.click()}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); importFile?.click(); } }}
      >
        <textarea class="ijson" rows="10" placeholder={'Drop a .json file here, or paste { "entries": { ... } }'} bind:value={importText} onclick={(e) => e.stopPropagation()}></textarea>
        {#if importDrag}<div class="dropmsg">Drop to load</div>{/if}
      </div>
      <input class="hidden" type="file" accept=".json,application/json" bind:this={importFile}
             onchange={(e) => { loadImportFile(e.currentTarget.files?.[0]); e.currentTarget.value = ''; }} />
      {#if importResult}
        {#if importResult.error}<div class="ierr">{importResult.error}</div>
        {:else}<div class="iok">Added {importResult.count_added}, skipped {importResult.count_skipped}{importResult.cleaned?.length ? `, cleaned ${importResult.cleaned.length}` : ''}.</div>{/if}
      {/if}
      <div class="iact">
        <button class="cancel" onclick={closeImport}>Close</button>
        <button class="go" disabled={importBusy || !importText.trim()} onclick={doImport}>{importBusy ? 'Importing…' : 'Import'}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .mgr { display: grid; grid-template-columns: 320px 1fr; gap: 16px; height: calc(100vh - 120px); padding: 14px 16px; }

  /* Library */
  .lib { display: flex; flex-direction: column; gap: 8px; min-height: 0; border-right: 1px solid var(--border); padding-right: 14px; }
  .libhead { display: flex; gap: 6px; }
  .search { flex: 1; }
  .new { width: 34px; font-size: 18px; line-height: 1; }
  .binbtn { width: auto; min-width: 34px; padding: 0 8px; font-size: 14px; }
  .binbtn.on { border-color: var(--accent); color: var(--accent); }
  .binbanner { font-size: 11.5px; color: var(--muted); background: var(--elev); border: 1px solid var(--border-soft);
    border-radius: 8px; padding: 6px 10px; }
  .cats { display: flex; flex-wrap: wrap; gap: 4px; }
  .cat { font-size: 11px; padding: 3px 8px; border-radius: 999px; background: var(--elev); border: 1px solid var(--border); color: var(--muted); text-transform: capitalize; }
  .cat.on { background: var(--elev-2); color: #fff; border-color: var(--accent); }
  .ratingnote { font-size: 11px; color: var(--muted); }
  .list { flex: 1; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
  .bcard { text-align: left; padding: 9px 11px; border-radius: 10px; background: var(--elev); border: 1px solid var(--border); cursor: pointer; }
  .bcard:hover { background: var(--elev-2); }
  .bcard.on { border-color: var(--accent); background: var(--elev-2); }
  .brow { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .bname { font-weight: 600; font-size: 13.5px; color: var(--text); }
  .rate { font-size: 9.5px; font-weight: 700; letter-spacing: .4px; text-transform: uppercase; padding: 1px 6px; border-radius: 6px; }
  .rate.sfw { color: #8fc7a0; background: rgba(120,200,140,.12); }
  .rate.nsfw { color: #e9a0b8; background: rgba(230,120,160,.14); }
  .bmeta { display: flex; align-items: center; gap: 9px; margin-top: 4px; font-size: 11px; color: var(--muted); }
  .bcat { text-transform: capitalize; }
  .builtin { font-size: 10px; color: var(--faint); border: 1px solid var(--border); border-radius: 5px; padding: 0 4px; }
  .off { color: #d88; }
  .importzone { margin-top: auto; display: flex; flex-direction: column; align-items: center; gap: 2px;
    padding: 12px 10px; border: 1.5px dashed var(--border); border-radius: 10px; cursor: pointer;
    color: var(--muted); text-align: center; transition: border-color .12s, background .12s, color .12s; }
  .importzone:hover { border-color: var(--accent, #6ea8fe); color: var(--fg, inherit); }
  .importzone.over { border-color: var(--accent, #6ea8fe); background: color-mix(in srgb, var(--accent, #6ea8fe) 12%, transparent); color: var(--accent, #6ea8fe); }
  .importzone .iz-icon { font-size: 18px; line-height: 1; }
  .importzone .iz-text { font-size: 12px; font-weight: 600; }
  .importzone .iz-sub { font-size: 10.5px; opacity: .7; }
  .importzone.over * { pointer-events: none; }

  /* Editor */
  .ed { min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
  .placeholder, .empty { color: var(--faint); font-size: 13px; padding: 8px 2px; }
  .edhead { display: flex; align-items: center; gap: 10px; }
  .title { flex: 1; font-size: 18px; font-weight: 600; background: none; border: 0; border-bottom: 1px solid transparent; padding: 4px 2px; }
  .title:hover, .title:focus { border-bottom-color: var(--border); }
  .savemsg { color: var(--accent); font-size: 12px; min-width: 14px; }
  .testbtn { background: var(--elev); border: 1px solid var(--border); border-radius: 7px; font-size: 12px; padding: 4px 9px; cursor: pointer; color: var(--muted); white-space: nowrap; }
  .testbtn:hover { color: var(--fg, inherit); border-color: var(--accent, #6ea8fe); }
  .testbtn.on { color: var(--accent, #6ea8fe); border-color: var(--accent, #6ea8fe); background: color-mix(in srgb, var(--accent, #6ea8fe) 12%, transparent); }
  .del { background: none; border: 0; font-size: 15px; opacity: .6; cursor: pointer; }
  .del:hover { opacity: 1; }
  .metarow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 12px; }
  .metarow select { font-size: 12px; padding: 3px 6px; }
  .tog { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); }
  .psel { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); }
  .scope { margin-left: auto; font-size: 11px; color: var(--faint); }
  .desc { width: 100%; resize: vertical; font-size: 12.5px; }

  .test { border: 1px solid var(--border); border-radius: 9px; padding: 4px 10px; background: var(--elev); }
  .testbody { display: flex; flex-direction: column; gap: 6px; padding: 4px 0 8px; }
  .testbody textarea { width: 100%; resize: vertical; }
  .testbody button { align-self: flex-start; }
  .hits { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 3px; }
  .hits li { font-size: 12px; }
  .hkw { color: var(--faint); font-size: 11px; }

  .enthead { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
  .enthead h3 { margin: 0; font-size: 14px; }
  .cnt { color: var(--faint); font-weight: 400; }
  .addent { font-size: 12px; }
  .entries { display: flex; flex-direction: column; gap: 6px; }
  .ent { border: 1px solid var(--border); border-radius: 9px; background: var(--elev); overflow: hidden; scroll-margin: 80px; transition: border-color .12s, box-shadow .12s; }
  .ent.dis { opacity: .55; }
  .ent.sel { border-color: var(--accent, #6ea8fe); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent, #6ea8fe) 35%, transparent); }
  .entrow { display: flex; align-items: center; gap: 8px; padding: 8px 11px; cursor: pointer; }
  .entrow:hover { background: var(--elev-2); }
  .caret { color: var(--faint); font-size: 11px; }
  .etitle { font-weight: 600; font-size: 13px; }
  .fnbadge { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 999px; font-family: ui-monospace, monospace;
    color: rgba(100,210,130,.95); background: rgba(100,210,130,.12); border: 1px solid rgba(100,210,130,.25); }
  .fnbadge.bad { color: var(--bad); background: rgba(255,122,122,.1); border-color: rgba(255,122,122,.3); }
  .fnhelp { font-size: 11.5px; color: var(--muted); line-height: 1.5; margin: 2px 0 6px; }
  .fnhelp code { font-family: ui-monospace, monospace; background: var(--elev); padding: 0 4px; border-radius: 4px; }
  textarea.mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .ekw { color: var(--faint); font-size: 11px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .emsg { color: var(--accent); font-size: 12px; }
  .entbody { padding: 4px 12px 12px; display: flex; flex-direction: column; gap: 8px; border-top: 1px solid var(--border); }
  .entbody label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
  .entbody input, .entbody textarea { font-size: 13px; }
  .entbody textarea { resize: vertical; }
  .grow { resize: none; overflow-y: hidden; }
  .hint { color: var(--faint); font-weight: 400; }
  .entopts { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
  .num { max-width: 110px; }
  .num input { width: 100%; }
  .sel { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
  .sel select { font-size: 12px; padding: 3px 6px; }
  .delent { margin-left: auto; font-size: 12px; color: #d88; background: none; border: 1px solid var(--border); }
  .delent:hover { border-color: #d88; }
  .link { background: none; border: 0; color: var(--accent); cursor: pointer; padding: 0; text-decoration: underline; }

  /* Import modal */
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: grid; place-items: center; z-index: 100; }
  .sheet { width: min(640px, 92vw); max-height: 88vh; overflow: auto; background: var(--bg); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; box-shadow: var(--shadow); }
  .sheethead { display: flex; align-items: center; justify-content: space-between; }
  .sheethead h2 { margin: 0; font-size: 16px; }
  .x { background: none; border: 0; font-size: 16px; cursor: pointer; opacity: .6; }
  .note { font-size: 12px; color: var(--muted); margin: 0; }
  .irow { display: flex; gap: 12px; flex-wrap: wrap; }
  .irow label { display: flex; flex-direction: column; gap: 3px; font-size: 11.5px; color: var(--muted); }
  .drop { position: relative; border: 1.5px dashed var(--border); border-radius: 10px; padding: 2px; transition: border-color .12s, background .12s; cursor: pointer; }
  .drop.over { border-color: var(--accent, #6ea8fe); background: color-mix(in srgb, var(--accent, #6ea8fe) 10%, transparent); }
  .dropmsg { position: absolute; inset: 0; display: grid; place-items: center; font-size: 13px; font-weight: 600; color: var(--accent, #6ea8fe); pointer-events: none; background: color-mix(in srgb, var(--bg) 70%, transparent); border-radius: 9px; }
  .hidden { display: none; }
  .ijson { width: 100%; font-family: ui-monospace, monospace; font-size: 12px; resize: vertical; border: 0; background: transparent; display: block; }
  .ijson:focus { outline: none; }
  .note code { font-family: ui-monospace, monospace; font-size: 11.5px; padding: 0 3px; background: var(--surface, rgba(127,127,127,.15)); border-radius: 4px; }
  .ierr { color: #e88; font-size: 12.5px; }
  .iok { color: #8fc7a0; font-size: 12.5px; }
  .iact { display: flex; justify-content: flex-end; gap: 8px; }
  .go:disabled { opacity: .5; }
</style>

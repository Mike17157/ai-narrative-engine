<script>
  // The story library — a searchable, filterable grid of authored stories, the experiences
  // that actually play. "New story" mints an empty story and opens the editor; you build it by
  // CONVERSATION there (no wizard, no draft/commit).
  import { goto } from '$app/navigation';
  import { chars } from '$lib/characters.svelte.js';
  import { stories, deleteStory, loadStories } from '$lib/stories.svelte.js';
  import { post, put } from '$lib/api.js';

  async function newStory() {
    const r = await post('/stories/new', {});
    if (r.ok && r.data?.key) { await loadStories(); goto(`/stories/${r.data.key}/structure`); }
  }
  let seedOpen = $state(false), seedUrl = $state(''), seedBusy = $state(false), seedError = $state('');
  async function seedFromCard() {
    if (!seedUrl.trim() || seedBusy) return;
    seedBusy = true; seedError = '';
    try {
      const imported = await post('/characters/import-url', { url: seedUrl.trim() });
      if (!imported.data?.ok) throw new Error(imported.data?.error || 'Could not import card');
      const seeded = await post('/stories/seed-from-card', { character: imported.data.key });
      if (!seeded.data?.seed) throw new Error(seeded.data?.error || 'The story model could not build a seed');
      const seed = seeded.data.seed;
      await post(`/characters/${imported.data.key}/card`, { fields: { story_seed: seed } });
      const story = await post('/stories/from-cast', { name: seed.name || imported.data.name, characters: [imported.data.key], premise: seed.opening || seed.world || '' });
      if (!story.data?.ok) throw new Error(story.data?.error || 'Could not create story');
      await put(`/stories/${story.data.key}`, { world: { seed: seed.world || '', question: seed.question || '' }, premise_parts: { root: seed.world || '', question: seed.question || '' } });
      await loadStories();
      goto(`/stories/${story.data.key}/structure?tab=overview`);
    } catch (err) { seedError = err.message || String(err); }
    finally { seedBusy = false; }
  }
  const open = (key) => goto(`/stories/${key}/structure`);

  let complete = $derived(stories.list || []);

  // ── search + filters ──
  let q = $state('');
  let toneFilter = $state('');
  let castFilter = $state('any');   // 'any' | '1' | '2-4' | '5+'
  let sort = $state('recent');      // 'recent' | 'name' | 'cast'
  let showFilters = $state(false);  // filter modal open

  // Count only the constraining filters (tone/cast), not sort, for the button badge.
  let activeFilters = $derived((toneFilter ? 1 : 0) + (castFilter !== 'any' ? 1 : 0));
  function clearFilters() { toneFilter = ''; castFilter = 'any'; sort = 'recent'; }

  let tones = $derived(
    [...new Set(complete.map((s) => s.tone).filter(Boolean))].sort()
  );

  function castSize(s) { return s.cast?.length || 0; }
  function matchesCast(s) {
    const n = castSize(s);
    if (castFilter === 'any') return true;
    if (castFilter === '1') return n === 1;
    if (castFilter === '2-4') return n >= 2 && n <= 4;
    if (castFilter === '5+') return n >= 5;
    return true;
  }
  function matchesQ(s) {
    if (!q.trim()) return true;
    const hay = [s.name, s.premise, s.tone, ...(s.themes || []), ...castNames(s)].join(' ').toLowerCase();
    return q.trim().toLowerCase().split(/\s+/).every((tok) => hay.includes(tok));
  }
  function castNames(s) {
    return (s.cast || []).map((k) => chars.list.find((c) => c.key === k)?.name || k);
  }
  let filtered = $derived(
    complete
      .filter((s) => matchesQ(s) && matchesCast(s) && (!toneFilter || s.tone === toneFilter))
      .sort((a, b) => {
        if (sort === 'name') return (a.name || '').localeCompare(b.name || '');
        if (sort === 'cast') return castSize(b) - castSize(a);
        return (b._mtime || 0) - (a._mtime || 0);   // recent
      })
  );
</script>

<div class="page"><div class="col wide">

  {#if stories.msg}<div class="msg" class:ok={stories.msg.ok} class:err={stories.msg.err}>{stories.msg.text}</div>{/if}

  <div class="sechead">
    <span class="sectitle">Finished{complete.length ? ` · ${complete.length}` : ''}</span>
    <div class="newacts"><button class="ghost" onclick={() => (seedOpen = true)}>Seed from card link</button><button onclick={newStory}>＋ New story</button></div>
  </div>

  {#if complete.length}
    <!-- toolbar: search + filter button -->
    <div class="toolbar">
      <div class="search">
        <span class="sico">🔍</span>
        <input bind:value={q} placeholder="Search stories — name, premise, cast, themes…" />
        {#if q}<button class="clear" onclick={() => (q = '')} title="Clear">✕</button>{/if}
      </div>
      <button class="filterbtn" class:active={activeFilters} onclick={() => (showFilters = true)}>
        ⚙ Filters{#if activeFilters}<span class="fbadge">{activeFilters}</span>{/if}
      </button>
    </div>

    {#if filtered.length}
      <div class="grid">
        {#each filtered as s (s.key)}
          <div class="card story-card" onclick={() => open(s.key)} role="button" tabindex="0">
            <div class="cname">{s.name}</div>
            <p class="cprem">{s.premise || 'No premise.'}</p>
            <div class="cmeta">
              <span class="badge">{s.locations} location{s.locations === 1 ? '' : 's'}</span>
              <span class="badge">{castSize(s)} cast</span>
              {#if s.tone}<span class="tone">{s.tone}</span>{/if}
            </div>
            {#if castSize(s) <= 4}
              <div class="cast-row">
                {#each castNames(s).slice(0, 4) as nm (nm)}<span class="cast-chip" title={nm}>{nm}</span>{/each}
              </div>
            {/if}
            <button class="playbtn" title="Play" onclick={(e) => { e.stopPropagation(); goto(`/stories/${s.key}/play`); }}>▶</button>
            <button class="del" title="Delete story" onclick={(e) => { e.stopPropagation(); deleteStory(s.key); }}>🗑</button>
          </div>
        {/each}
      </div>
    {:else}
      <div class="no-match">
        <p>No stories match your filters.</p>
        <button class="ghost sm" onclick={() => { q = ''; clearFilters(); }}>Clear filters</button>
      </div>
    {/if}
  {:else}
    <div class="empty">
      <div class="emk">📖</div>
      <p>No stories yet.</p>
      <span>Generate a cast from a premise and build a story from the tension between them.</span>
      <button onclick={newStory}>＋ New story</button>
    </div>
  {/if}

</div></div>

{#if seedOpen}
  <div class="overlay" onclick={() => (seedOpen = false)} role="presentation"><div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
    <div class="dlghead"><h3 class="title">Seed from a character card</h3><button class="x" onclick={() => (seedOpen = false)}>✕</button></div>
    <p class="seedhint">Paste a JanitorAI, Chub, AICC, Pygmalion, Risu, or direct Tavern-card link. Loom imports the card, runs the story seed model, and creates the first Character and Story cards.</p>
    <input class="seedurl" bind:value={seedUrl} placeholder="https://janitorai.com/characters/..." onkeydown={(e) => e.key === 'Enter' && seedFromCard()} />
    {#if seedError}<p class="seederr">{seedError}</p>{/if}
    <div class="acts"><button class="ghost" onclick={() => (seedOpen = false)}>Cancel</button><button onclick={seedFromCard} disabled={!seedUrl.trim() || seedBusy}>{seedBusy ? 'Building cards…' : 'Import & build cards'}</button></div>
  </div></div>
{/if}

{#if showFilters}
  <div class="overlay" onclick={() => (showFilters = false)} role="presentation">
    <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="dlghead">
        <h3 class="title">Filter & sort</h3>
        <button class="x" onclick={() => (showFilters = false)} title="Close">✕</button>
      </div>

      <label class="field">
        <span>Tone</span>
        <select bind:value={toneFilter} disabled={tones.length < 2}>
          <option value="">All tones</option>
          {#each tones as t (t)}<option value={t}>{t}</option>{/each}
        </select>
      </label>

      <label class="field">
        <span>Cast size</span>
        <select bind:value={castFilter}>
          <option value="any">Any cast size</option>
          <option value="1">Solo</option>
          <option value="2-4">2–4 cast</option>
          <option value="5+">5+ cast</option>
        </select>
      </label>

      <label class="field">
        <span>Sort by</span>
        <select bind:value={sort}>
          <option value="recent">Recent</option>
          <option value="name">Name A–Z</option>
          <option value="cast">Cast size</option>
        </select>
      </label>

      <div class="acts">
        <button class="ghost" onclick={clearFilters} disabled={!activeFilters && sort === 'recent'}>Reset</button>
        <button onclick={() => (showFilters = false)}>Done</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (showFilters && e.key === 'Escape') showFilters = false; }} />

<style>
  .sechead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .sectitle { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .msg { margin: 10px 0; font-size: 13px; }
  .msg.ok { color: var(--good); } .msg.err { color: var(--bad); }

  /* toolbar */
  .toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
  .search { flex: 1; min-width: 220px; position: relative; display: flex; align-items: center; }
  .search .sico { position: absolute; left: 11px; font-size: 13px; opacity: .6; pointer-events: none; }
  .search input { padding: 8px 32px 8px 32px; font-size: 13px; }
  .search input:focus { outline: none; border-color: var(--accent); }
  .search .clear { position: absolute; right: 8px; background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 12px; padding: 4px; }
  select { padding: 8px 10px; font-size: 13px; cursor: pointer; }

  /* filter button */
  .filterbtn { display: inline-flex; align-items: center; gap: 7px; padding: 8px 14px; border-radius: 9px;
    background: var(--elev); border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; cursor: pointer; }
  .filterbtn:hover { border-color: var(--border-strong, var(--accent)); }
  .filterbtn.active { border-color: var(--accent); color: var(--accent); }
  .fbadge { display: inline-grid; place-items: center; min-width: 17px; height: 17px; padding: 0 5px;
    border-radius: 999px; background: var(--accent); color: #fff; font-size: 10.5px; font-weight: 700; }

  /* filter modal */
  .overlay { position: fixed; inset: 0; z-index: 80; background: rgba(6, 8, 12, .62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(92vw, 380px); background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-lg, 14px); box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55));
    padding: 18px; animation: pop .13s ease; }
  .dlghead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
  .title { margin: 0; font-size: 16px; font-weight: 680; color: var(--text); }
  .x { background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 13px; padding: 4px; }
  .x:hover { color: var(--text); }
  .field { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
  .field span { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .field select { width: 100%; }
  .acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }
  .newacts { display:flex; gap:8px; } .seedhint { color:var(--muted); font-size:13px; line-height:1.45; } .seedurl { width:100%; box-sizing:border-box; margin-top:12px; } .seederr { color:var(--bad); font-size:12px; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  .acts .ghost:disabled { opacity: .4; cursor: not-allowed; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }

  /* grids */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
  .card {
    position: relative; padding: 14px; cursor: pointer;
  }
  .card:hover { border-color: var(--border); background: var(--elev); }

  .cname { font-size: 15px; font-weight: 650; margin-bottom: 6px; }
  .cprem { margin: 0 0 10px; font-size: 12.5px; color: var(--muted);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cmeta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .badge { font-size: 11px; background: var(--elev-2); border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px; color: var(--muted); }
  .tone { font-size: 11px; color: var(--faint); }

  /* cast chips on story cards */
  .cast-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 10px; }
  .cast-chip { font-size: 10.5px; background: var(--elev-2); border: 1px solid var(--border-soft); border-radius: 6px; padding: 1px 7px; color: var(--muted); max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .del, .playbtn {
    position: absolute; top: 8px; width: 26px; height: 26px; padding: 0;
    border-radius: 7px; background: rgba(10,12,18,.6);
    border: 1px solid var(--border); font-size: 12px; opacity: 0;
  }
  .del { right: 8px; color: var(--muted); }
  .playbtn { right: 40px; color: #fff; font-size: 11px; }
  .card:hover .del, .card:hover .playbtn { opacity: 1; }
  .del:hover { color: var(--bad); }
  .playbtn:hover { color: var(--accent); border-color: var(--accent); }

  .no-match, .empty { margin: 50px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .no-match p, .empty p { margin: 0; }
  .empty { margin: 60px auto; }
  .emk { font-size: 34px; }
  .empty p { color: var(--text); font-size: 16px; }
  .empty span { font-size: 12.5px; max-width: 360px; line-height: 1.5; }
  .empty button { margin-top: 12px; }
</style>

<script>
  // The story library — a searchable, filterable grid of authored stories (the
  // experiences that actually play), plus an "In progress" row for wizard drafts.
  import { goto } from '$app/navigation';
  import { del } from '$lib/api.js';
  import { chars } from '$lib/characters.svelte.js';
  import { stories, deleteStory, startWizard, resumeDraft, resetWizard } from '$lib/stories.svelte.js';

  function newStory() {
    const c = chars.list.find((x) => !x.story) || chars.list[0];
    startWizard(c?.key || '', c?.name || '');
  }
  const open = (key) => goto(`/stories/${key}/overview`);

  const STEP_LABELS = ['Setup', 'Storyboard', 'Scenes', 'Cast'];

  let drafts = $derived((stories.list || []).filter((s) => s.draft));
  let complete = $derived((stories.list || []).filter((s) => !s.draft));

  // ── search + filters ──
  let q = $state('');
  let toneFilter = $state('');
  let castFilter = $state('any');   // 'any' | '1' | '2-4' | '5+'
  let sort = $state('recent');      // 'recent' | 'name' | 'cast'

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

  function relTime(iso) {
    if (!iso) return '';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }
</script>

<div class="page"><div class="col wide">

  {#if stories.msg}<div class="msg" class:ok={stories.msg.ok} class:err={stories.msg.err}>{stories.msg.text}</div>{/if}

  {#if drafts.length}
    <div class="sechead"><span class="sectitle">In progress</span></div>
    <div class="drafts">
      {#each drafts as s (s.id)}
        <div class="card draft-card" onclick={() => resumeDraft(s.id)} role="button" tabindex="0">
          <div class="dportrait">
            {#if s.character}
              <img src="/api/characters/{s.character}/reference" alt={s.charName || s.name}
                   onerror={(e) => e.target.style.display='none'} />
            {/if}
          </div>
          <div class="dbody">
            <div class="cname">{s.name || s.charName || 'Untitled story'}</div>
            {#if s.premise}<p class="cprem">{s.premise}</p>{/if}
            <div class="cmeta">
              <span class="badge building">Building · {STEP_LABELS[s.step ?? 0]}</span>
              {#if s.updated_at}<span class="dtime">{relTime(s.updated_at)}</span>{/if}
            </div>
          </div>
          <button class="del" title="Discard draft" onclick={(e) => {
            e.stopPropagation();
            del(`/stories/draft/${s.id}`).then(() => {
              stories.list = stories.list.filter(x => x.id !== s.id);
              if (stories.wizard?.draftId === s.id) resetWizard();
            });
          }}>✕</button>
        </div>
      {/each}
    </div>
  {/if}

  <div class="sechead" style="margin-top:{drafts.length ? '28px' : '4px'}">
    <span class="sectitle lo">{complete.length} stor{complete.length === 1 ? 'y' : 'ies'}</span>
    <button onclick={newStory}>＋ New story</button>
  </div>

  {#if complete.length}
    <!-- toolbar: search + filters + sort -->
    <div class="toolbar">
      <div class="search">
        <span class="sico">🔍</span>
        <input bind:value={q} placeholder="Search stories — name, premise, cast, themes…" />
        {#if q}<button class="clear" onclick={() => (q = '')} title="Clear">✕</button>{/if}
      </div>
      <select bind:value={toneFilter} disabled={tones.length < 2}>
        <option value="">All tones</option>
        {#each tones as t (t)}<option value={t}>{t}</option>{/each}
      </select>
      <select bind:value={castFilter}>
        <option value="any">Any cast size</option>
        <option value="1">Solo</option>
        <option value="2-4">2–4 cast</option>
        <option value="5+">5+ cast</option>
      </select>
      <select bind:value={sort}>
        <option value="recent">Recent</option>
        <option value="name">Name A–Z</option>
        <option value="cast">Cast size</option>
      </select>
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
        <button class="ghost sm" onclick={() => { q = ''; toneFilter = ''; castFilter = 'any'; }}>Clear filters</button>
      </div>
    {/if}
  {:else if !drafts.length}
    <div class="empty">
      <div class="emk">📖</div>
      <p>No stories yet.</p>
      <span>Storyboard a plausible story from a character, then extract its scenes and cast.</span>
      <button onclick={newStory}>＋ New story</button>
    </div>
  {/if}

</div></div>

<style>
  .sechead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .sectitle { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .sectitle.lo { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--faint); }
  .msg { margin: 10px 0; font-size: 13px; }
  .msg.ok { color: var(--good); } .msg.err { color: var(--bad); }

  /* toolbar */
  .toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; align-items: center; }
  .search { flex: 1; min-width: 220px; position: relative; display: flex; align-items: center; }
  .search .sico { position: absolute; left: 11px; font-size: 13px; opacity: .6; pointer-events: none; }
  .search input { width: 100%; padding: 8px 32px 8px 32px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; }
  .search input:focus { outline: none; border-color: var(--accent); }
  .search .clear { position: absolute; right: 8px; background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 12px; padding: 4px; }
  select { padding: 8px 10px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; cursor: pointer; }

  /* grids */
  .drafts { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; }
  .card {
    position: relative; background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: 14px; padding: 14px; cursor: pointer;
  }
  .card:hover { border-color: var(--border); background: var(--elev); }

  /* draft card */
  .draft-card { display: flex; gap: 12px; align-items: flex-start; border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); }
  .draft-card:hover { border-color: color-mix(in srgb, var(--accent) 50%, var(--border)); }
  .dportrait { width: 48px; height: 64px; flex: none; border-radius: 8px; background: var(--elev-2); border: 1px solid var(--border); overflow: hidden; }
  .dportrait img { width: 100%; height: 100%; object-fit: cover; }
  .dbody { flex: 1; min-width: 0; }

  .cname { font-size: 15px; font-weight: 650; margin-bottom: 6px; }
  .cprem { margin: 0 0 10px; font-size: 12.5px; color: var(--muted);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .cmeta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .badge { font-size: 11px; background: var(--elev-2); border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px; color: var(--muted); }
  .badge.building { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 35%, transparent); background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .dtime { font-size: 11px; color: var(--faint); }
  .tone { font-size: 11px; color: var(--faint); }

  /* cast chips on story cards */
  .cast-row { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 10px; }
  .cast-chip { font-size: 10.5px; background: var(--elev-2); border: 1px solid var(--border-soft); border-radius: 6px; padding: 1px 7px; color: var(--muted); max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .del, .playbtn {
    position: absolute; top: 8px; width: 26px; height: 26px; padding: 0;
    border-radius: 7px; box-shadow: none; background: rgba(10,12,18,.6);
    border: 1px solid var(--border); font-size: 12px; opacity: 0;
  }
  .del { right: 8px; color: var(--muted); }
  .playbtn { right: 40px; color: #fff; font-size: 11px; }
  .card:hover .del, .card:hover .playbtn { opacity: 1; }
  .del:hover { color: var(--bad); filter: none; }
  .playbtn:hover { color: var(--accent); border-color: var(--accent); filter: none; }

  .no-match, .empty { margin: 50px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .no-match p, .empty p { margin: 0; }
  .empty { margin: 60px auto; }
  .emk { font-size: 34px; }
  .empty p { color: var(--text); font-size: 16px; }
  .empty span { font-size: 12.5px; max-width: 360px; line-height: 1.5; }
  .empty button { margin-top: 12px; }
</style>

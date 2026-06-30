<script>
  import { charName, chars, loadChars, blurb } from '$lib/characters.svelte.js';
  import { stories, deleteStory, expandArc, generateTimelines, loadStory, setStoryMode, persistCurrent } from '$lib/stories.svelte.js';
  import { patch, put, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ChapterCard from '$lib/components/story/ChapterCard.svelte';
  import SceneModal  from '$lib/components/story/SceneModal.svelte';
  import ArcModal    from '$lib/components/story/ArcModal.svelte';
  import StoryCanvas from '$lib/components/story/StoryCanvas.svelte';
  import CharacterModal from '$lib/components/story/CharacterModal.svelte';
  import PlacesEditor from '$lib/components/story/PlacesEditor.svelte';
  import AgentChat from '$lib/components/story/AgentChat.svelte';
  import NovelChapters from '$lib/components/story/NovelChapters.svelte';
  import SceneRoutes from '$lib/components/story/SceneRoutes.svelte';

  let st = $derived(stories.current);

  // ── Default personas: the playable "you" cards this story suggests ──────────
  loadChars();
  let playableCards = $derived((chars.list || []).filter((c) => c.playable));
  function isDefaultPersona(k) { return (st?.default_personas || []).includes(k); }
  async function toggleDefaultPersona(k) {
    const cur = st.default_personas || [];
    const next = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
    stories.current.default_personas = next;          // reactive
    await put(`/stories/${st.key}`, { default_personas: next });
  }

  // ── Places & scenes: story-authored containers + character-anchored spots ───
  // Enriched cast: name + role + base image (for the relationship-graph character cards).
  let castOptions = $derived((st?.cast || []).map((m) => {
    const ci = chars.list.find((c) => c.key === m.character);
    return {
      key: m.character, name: charName(m.character) || m.character, primary: m.primary,
      role: ci?.fields?.role || '',
      img: ci?.reference || ci?.avatar || (ci?.images || []).map((im) => im?.url).find(Boolean) || null,
    };
  }));
  let placesTimer = null;
  function savePlaces(next) {
    stories.current.places = next;
    clearTimeout(placesTimer);
    placesTimer = setTimeout(() => put(`/stories/${st.key}`, { places: next }), 600);
  }

  // ── Inline editing: the description fields edit in place. Every change mutates
  //    stories.current and a single debounced persistCurrent() PUTs the whole
  //    description (title/logline/premise/tone/themes/cast/locations/ending). Same
  //    mutate-current-then-save pattern the rest of this page already uses. ──────────
  let saveTimer = null;
  function saveSoon() { clearTimeout(saveTimer); saveTimer = setTimeout(persistCurrent, 600); }

  // ── Premise components: the overview is STRUCTURED by the premise's parts (protagonist, lie,
  //    inciting, opposition, stakes, texture). Coverage is checked AUTOMATICALLY — on open and after
  //    premise/logline/tone edits (debounced). Gaps are filled in place via the Author or the fields.
  //    Replaced the old scripted premise interview. ──
  const PREMISE_COMPONENTS = [
    { id: 'protagonist', label: 'Protagonist' },
    { id: 'lie', label: 'The lie they live by' },
    { id: 'inciting', label: 'Inciting incident' },
    { id: 'opposition', label: 'Opposition' },
    { id: 'stakes', label: 'Stakes' },
    { id: 'texture', label: 'Tone & texture' },
  ];
  let coverage = $state(null);    // [{id,label,covered,note}] | null
  let covBusy = $state(false);
  let covErr = $state(null);
  let covById = $derived(Object.fromEntries((coverage || []).map((c) => [c.id, c])));
  let covGaps = $derived((coverage || []).filter((c) => !c.covered).length);
  async function checkCoverage() {
    if (covBusy || !st) return;
    covBusy = true; covErr = null;
    const r = await post(`/stories/${st.key}/premise-coverage`, {});
    covBusy = false;
    if (r.ok && r.data?.components) coverage = r.data.components;
    else covErr = r.data?.error || 'check failed';
  }
  // Auto-run: debounced on open + when the premise material changes. Resets on story switch.
  let covTimer = null, covKey = null;
  $effect(() => {
    const key = st?.key; if (!key) return;
    void (st.premise || ''); void (st.storyboard?.logline || ''); void (st.tone || '');   // track edits
    if (covKey !== key) { covKey = key; coverage = null; covErr = null; }                   // new story
    clearTimeout(covTimer);
    covTimer = setTimeout(checkCoverage, 1200);
  });

  // Themes ↔ comma-string mirror (re-seed when the story changes; avoids array churn per keystroke).
  let themesStr = $state(''); let themesSeed = null;
  $effect(() => { if (st && themesSeed !== st.key) { themesStr = (st.themes || []).join(', '); themesSeed = st.key; } });
  function commitThemes() { stories.current.themes = themesStr.split(',').map((t) => t.trim()).filter(Boolean); saveSoon(); }

  // Cast roster (story-level): add/remove existing character cards.
  let addPick = $state('');
  let castAddItems = $derived((chars.list || [])
    .filter((c) => !c.story && !(st?.cast || []).some((m) => m.character === c.key))
    .map((c) => ({ value: c.key, label: c.name || c.key })));
  function addCastMember() {
    if (!addPick) return;
    stories.current.cast = [...(st.cast || []), { character: addPick, primary: false }];
    addPick = ''; saveSoon();
  }
  function removeCastMember(i) { stories.current.cast = (st.cast || []).filter((_, j) => j !== i); saveSoon(); }

  // Locations: inline editor (name/description/background + area grouping + start marker).
  let locUid = 0;
  function addLocation() {
    const id = `loc_${Date.now().toString(36)}_${locUid++}`;
    stories.current.locations = [...(st.locations || []), { id, name: 'New location', description: '', background_prompt: '', parent: '' }];
    saveSoon();
  }
  function removeLocation(i) {
    const loc = st.locations?.[i];
    stories.current.locations = (st.locations || []).filter((_, j) => j !== i);
    if (loc && st.start === loc.id) stories.current.start = null;
    saveSoon();
  }
  let tagging = $state({});
  async function tagifyBg(loc) {
    const text = (loc.background_prompt || '').trim(); if (!text || tagging[loc.id]) return;
    tagging = { ...tagging, [loc.id]: true };
    const r = await post('/tagify', { text, kind: 'scene' });
    tagging = { ...tagging, [loc.id]: false };
    if (r.ok && r.data?.tags) { loc.background_prompt = r.data.tags; saveSoon(); }
  }

  // ── Memory window: how many recent turns stay verbatim before older ones compress ──
  let windowTimer = null;
  function saveWindow(n) {
    const v = Math.max(2, Math.min(40, Math.round(+n || 8)));
    stories.current.recent_window = v;
    clearTimeout(windowTimer);
    windowTimer = setTimeout(() => put(`/stories/${st.key}`, { recent_window: v }), 400);
  }

  // Arc expansion state
  let expandingArc  = $state(null);
  let expandStream  = $state('');

  // SceneModal state
  let regenModal    = $state(null);   // { arcIdx, nodeId, chapter, chapterIdx }

  // Cast picker state
  let castPickerArcId = $state(null);  // arc id whose picker is open

  // Lens tabs — the right side is the story DOCUMENT or one focused lens, never stacked.
  let tab = $state('document');   // 'document' | 'plot' | 'relationships' | 'map'

  // Timeline generation state
  let generatingTimelineArc = $state(null);  // arc.id currently generating
  let tlPhase = $state('');                  // current phase label
  let tlStreamsByTl = $state({});           // { [timeline_id]: string } — live delta text

  async function handleGenerateTimelines(arc) {
    if (generatingTimelineArc) return;
    generatingTimelineArc = arc.id;
    tlPhase = '';
    tlStreamsByTl = {};
    await generateTimelines(st.key, arc.id, (ev) => {
      if (ev.type === 'phase')         tlPhase = ev.label;
      else if (ev.type === 'delta')    tlStreamsByTl = { ...tlStreamsByTl, [ev.timeline_id]: (tlStreamsByTl[ev.timeline_id] || '') + ev.text };
      else if (ev.type === 'timeline_start') tlStreamsByTl = { ...tlStreamsByTl, [ev.timeline_id]: '' };
      else if (ev.type === 'result') {
        // Patch the arc in reactive state so graph rebuilds immediately
        if (stories.current?.arcs) {
          const idx = stories.current.arcs.findIndex(a => a.id === arc.id);
          if (idx >= 0) stories.current.arcs[idx] = { ...stories.current.arcs[idx], ...ev };
        }
      }
    });
    generatingTimelineArc = null;
    tlPhase = '';
    tlStreamsByTl = {};
    await loadStory(st.key);
  }

  // Arc-details modal
  let arcModal = $state(null);   // { arcIdx }
  function openArcModal(arcIdx) { arcModal = { arcIdx }; }
  function closeArcModal() { arcModal = null; }
  async function handleArcSave(updated) {
    if (!arcModal || !stories.current?.arcs) return;
    const idx = arcModal.arcIdx;
    const arc = stories.current.arcs[idx];
    stories.current.arcs[idx] = { ...arc, ...updated };
    arcModal = null;
    await patch(`/stories/${st.key}/arc/${arc.id}`, {
      name: updated.name, dramatic_function: updated.dramatic_function,
      mini_ending: updated.mini_ending, rationale: updated.rationale,
    });
  }
  // ── Voice agent (Phase 1): a spoken edit applies via graph-ops; highlight what changed ──────
  let speaker = $state('');         // the agent's active behaviour → unified canvas zooms to its view
  let relPulse = $state(null);     // {source,target} edge to pulse after a change
  let agentMsg = $state('');       // one-line confirmation from the last command
  let agentMsgTimer = null;
  // Tools that mean "the agent worked on a character" → pop that character's modal.
  const CHAR_FN = /character|persona|appearance|backstory|wardrobe|outfit|sprite|\bcast\b/i;
  async function handleApplied(log, artifacts, warning, before) {
    const after = stories.current?.relationships || [];
    const ekey = (r) => `${r.source}→${r.target}`;
    const sig = (r) => `${r.stance}|${r.dynamic}|${r.nature}`;
    const prev = new Map((before || []).map((r) => [ekey(r), sig(r)]));
    // First new-or-changed edge → pulse it.
    const changed = after.find((r) => !prev.has(ekey(r)) || prev.get(ekey(r)) !== sig(r));
    if (changed) {
      relPulse = { source: changed.source, target: changed.target };
      setTimeout(() => { relPulse = null; }, 1400);
    }
    const newChar = (artifacts || []).map((a) => a.name).find(Boolean);
    agentMsg = warning
      || (newChar ? `Added ${newChar}`
        : changed ? `${charName(changed.source)} → ${charName(changed.target)}: ${changed.stance}`
        : `${(log || []).filter((o) => o.ok).length} change(s) applied`);
    clearTimeout(agentMsgTimer);
    agentMsgTimer = setTimeout(() => { agentMsg = ''; }, 6000);

    // The agent modified a character → bring up its modal. New character: reload the roster first
    // so the card resolves, then open by name. Edit: open the character in focus.
    if (newChar) {
      await loadChars();
      const c = (chars.list || []).find((x) => (x.name || '').toLowerCase() === newChar.toLowerCase());
      if (c) openCharModal(c.key);
    } else if ((log || []).some((o) => o.ok && CHAR_FN.test(o.fn || ''))) {
      openCharModal(selectedCharKey || primaryCharKey);
    }
  }

  // Character detail: clicking a relationship node (or the agent touching a character) opens the
  // modal; selectedCharKey also anchors the relationship-ring focus.
  let selectedCharKey = $state(null);
  let modalCharKey = $state(null);
  function openCharModal(k) { if (!k) return; selectedCharKey = k; modalCharKey = k; }
  function closeCharModal() { modalCharKey = null; }
  let modalChar = $derived((chars.list || []).find((c) => c.key === modalCharKey) || null);
  let modalBonds = $derived(
    !modalChar ? [] : (st?.relationships || [])
      .filter((r) => r.source === modalCharKey || r.target === modalCharKey)
      .map((r) => ({
        other: charName(r.source === modalCharKey ? r.target : r.source),
        dir: r.source === modalCharKey ? '→' : '←',
        nature: r.nature, stance: r.stance || 'neutral', dynamic: r.dynamic || '',
      }))
  );

  // Click a card on the canvas → open the chapter modal. Flat-beat stories use a
  // synthetic '_board' arc, so route those to the storyboard.beats list instead.
  function selectGraphNode(data) {
    if (data.arcId === '_board') {
      const i = parseInt(String(data.nodeId).slice(1), 10);
      const beat = stories.current?.storyboard?.beats?.[i];
      if (beat) regenModal = { flat: true, beatIdx: i, chapter: beat, chapterIdx: i };
      return;
    }
    const arc = stories.current?.arcs?.[data.arcIdx];
    const node = arc?.nodes?.[data.nodeId];
    // data.nodeId is the dict key (e.g. 'n1') — pass it explicitly so saves work
    if (node) openRegenModal(arc, data.arcIdx, data.nodeId, node, -1);
  }

  // Primary character key (always locked in every arc)
  let primaryCharKey = $derived(
    st?.cast?.find((m) => m.primary)?.character || st?.cast?.[0]?.character || ''
  );

  // Walk arc nodes — arc.nodes is a {id: ArcBeat} dict, not an array.
  // Returns beat objects with _key attached so openRegenModal can save correctly.
  function walkNodes(arc) {
    const nodeMap = arc?.nodes || {};
    const keys = Object.keys(nodeMap);
    if (!keys.length) return [];
    const ordered = [];
    let curId = arc.start || keys[0];
    const seen = new Set();
    while (curId && !seen.has(curId)) {
      seen.add(curId);
      const cur = nodeMap[curId];
      if (!cur) break;
      ordered.push({ ...cur, _key: curId });
      curId = cur.next?.[0] || null;
    }
    return ordered;
  }

  // Characters available to add to an arc (in story cast but not yet in arc.cast).
  function availableForArc(arc) {
    const inArc = new Set(arc.cast || []);
    return (st?.cast || []).filter((m) => !inArc.has(m.character));
  }

  async function addToArcCast(arc, arcIdx, charKey) {
    const newCast = [...(arc.cast || []), charKey];
    stories.current.arcs[arcIdx] = { ...arc, cast: newCast };
    castPickerArcId = null;
    await patch(`/stories/${st.key}/arc/${arc.id}`, { cast: newCast });
  }

  async function removeFromArcCast(arc, arcIdx, charKey) {
    if (charKey === primaryCharKey) return;  // protagonist is locked
    const newCast = (arc.cast || []).filter((k) => k !== charKey);
    stories.current.arcs[arcIdx] = { ...arc, cast: newCast };
    await patch(`/stories/${st.key}/arc/${arc.id}`, { cast: newCast });
  }

  async function handleExpand(arc) {
    if (expandingArc) return;
    expandingArc = arc.id;
    expandStream = '';
    await expandArc(
      st.key, arc.id,
      (text) => { expandStream += text; },
      (arcData) => {
        if (stories.current?.arcs) {
          const idx = stories.current.arcs.findIndex((a) => a.id === arc.id);
          if (idx >= 0) stories.current.arcs[idx] = { ...stories.current.arcs[idx], ...arcData };
        }
      },
    );
    expandingArc = null;
    expandStream = '';
    await loadStory(st.key);
  }

  function openRegenModal(arc, arcIdx, nodeId, node, chapterIdx) {
    regenModal = { arcIdx, nodeId, chapter: node, chapterIdx };
  }
  function closeRegenModal() { regenModal = null; }
  function handleRegenSave(updated) {
    if (!regenModal) return;
    if (regenModal.flat) {
      const beats = stories.current?.storyboard?.beats;
      if (beats?.[regenModal.beatIdx]) {
        beats[regenModal.beatIdx] = { ...beats[regenModal.beatIdx], ...updated };
        persistCurrent();
      }
      regenModal = null;
      return;
    }
    const arc = stories.current?.arcs?.[regenModal.arcIdx];
    if (arc?.nodes) arc.nodes[regenModal.nodeId] = { ...arc.nodes[regenModal.nodeId], ...updated };  // nodes is a dict
    regenModal = null;
  }
</script>

<div class="page withchat"><div class="col">

  <!-- Actions row -->
  <div class="vacts">
    <span class="sp"></span>
    <button class="ghost sm del" onclick={() => deleteStory(st.key)}>Delete</button>
  </div>

  <!-- Title (edit in place) + format badge -->
  <div class="title-row">
    <input class="ip ip-title" bind:value={st.name} oninput={saveSoon} placeholder="Untitled story" />
    <span class="type-badge">{st.type === 'vn' ? '🎴 Visual novel' : '📖 Novel'}</span>
  </div>

  <!-- Lens tabs — the right side is the document or one focused lens, never stacked -->
  <div class="lens-tabs">
    <button class="lens-tab" class:on={tab === 'document'} onclick={() => (tab = 'document')}>Document</button>
    <button class="lens-tab" class:on={tab === 'plot'} onclick={() => (tab = 'plot')}>{st.type === 'vn' ? 'Routes' : 'Plot'}</button>
    <button class="lens-tab" class:on={tab === 'relationships'} onclick={() => (tab = 'relationships')}>Relationships</button>
    <button class="lens-tab" class:on={tab === 'map'} onclick={() => (tab = 'map')}>Map</button>
  </div>

  {#if tab === 'document'}
  <!-- Heart callout (read-only — the storyboard's emotional core) -->
  {#if st.storyboard?.heart}
    <div class="heart-callout">
      <span class="heart-icon">♡</span>
      <span class="heart-text">{st.storyboard.heart}</span>
    </div>
  {/if}

  <!-- Logline + premise (edit in place) -->
  {#if st.storyboard}
    <input class="ip ip-logline" bind:value={st.storyboard.logline} oninput={saveSoon} placeholder="One-line logline…" />
  {/if}
  <textarea class="ip ip-prem" use:autosize={st.premise} bind:value={st.premise} oninput={saveSoon}
            placeholder="Premise — what is this story about?"></textarea>

  <!-- Premise components — the structure of the premise, auto-checked against the story -->
  <div class="cov">
    <div class="cov-head">
      <span class="cov-t">Premise components</span>
      <span class="cov-status">
        {#if covBusy}checking…
        {:else if covErr}<span class="cov-err">{covErr}</span>
        {:else if coverage}{covGaps ? `${covGaps} to address` : 'all covered ✓'}{/if}
      </span>
    </div>
    <div class="cov-grid">
      {#each PREMISE_COMPONENTS as comp (comp.id)}
        {@const c = covById[comp.id]}
        <div class="cov-item" class:ok={c?.covered} class:gap={c && !c.covered} class:pend={!c}>
          <span class="cov-mark">{c ? (c.covered ? '✓' : '○') : '·'}</span>
          <div class="cov-body">
            <span class="cov-label">{comp.label}</span>
            {#if c?.note}<span class="cov-note">{c.note}</span>{/if}
          </div>
        </div>
      {/each}
    </div>
  </div>

  <!-- Tone + themes (edit in place) -->
  <div class="ip-meta">
    <input class="ip ip-tone" bind:value={st.tone} oninput={saveSoon} placeholder="tone (e.g. melancholy, hopeful)" />
    <input class="ip ip-themes" bind:value={themesStr} oninput={commitThemes} placeholder="themes, comma separated" />
  </div>

  <!-- Default personas — the playable "you" cards this story suggests -->
  <details class="dp-details">
    <summary class="dp-summary">🎭 Default personas <span class="dp-count">{(st.default_personas || []).length || ''}</span></summary>
    <div class="dp-body">
      <p class="dp-hint">Playable cards this story suggests you embody. They float to the top of the <b>Playing as</b> menu when someone plays — pick the “you” that fits this world.</p>
      {#if playableCards.length}
        <div class="dp-chips">
          {#each playableCards as c (c.key)}
            <button class="dp-chip" class:on={isDefaultPersona(c.key)} onclick={() => toggleDefaultPersona(c.key)} title={blurb(c)}>
              {#if c.reference || c.avatar}<img src={c.reference || c.avatar} alt={c.name} />{:else}<span class="dp-ph">🎭</span>{/if}
              {c.name || c.key}
              <span class="dp-mark">{isDefaultPersona(c.key) ? '✓' : '+'}</span>
            </button>
          {/each}
        </div>
      {:else}
        <p class="dp-empty">No playable characters yet — make one in <a href="/characters/personas">Characters ▸ Personas</a>.</p>
      {/if}
    </div>
  </details>

  <!-- Cast roster — the story's characters -->
  <details class="dp-details" open>
    <summary class="dp-summary">🎬 Cast <span class="dp-count">{(st.cast || []).length || ''}</span></summary>
    <div class="dp-body">
      <div class="cast-roster">
        {#each st.cast || [] as m, i (m.character)}
          <span class="chip cast-chip" class:locked={m.primary} title={m.primary ? 'Protagonist' : ''}>
            {m.primary ? '★ ' : ''}{charName(m.character) || m.character}
            {#if !m.primary}<button class="cast-rm" onclick={() => removeCastMember(i)} title="Remove from cast">×</button>{/if}
          </span>
        {/each}
      </div>
      <div class="addrow">
        <Combobox items={castAddItems} bind:value={addPick} placeholder="add character…" />
        <button class="ghost sm" onclick={addCastMember} disabled={!addPick}>＋ Add</button>
      </div>
    </div>
  </details>

  <!-- Locations — bare environments; group under an area to sketch a light map -->
  <details class="dp-details">
    <summary class="dp-summary">📍 Locations <span class="dp-count">{(st.locations || []).length || ''}</span></summary>
    <div class="dp-body">
      <p class="dp-hint">The world’s bare places (no people/events). Pick an <b>area</b> to nest a location inside a larger region — a light map, no coordinates.</p>
      {#each st.locations || [] as loc, i (loc.id)}
        <div class="loc-box" class:start={st.start === loc.id} class:child={loc.parent}>
          <div class="loc-top">
            <input class="ip fld title" bind:value={loc.name} oninput={saveSoon} placeholder="location name" />
            <select class="area" bind:value={loc.parent} onchange={saveSoon} title="Group under an area">
              <option value="">— top level —</option>
              {#each (st.locations || []).filter((o) => o.id !== loc.id) as o (o.id)}<option value={o.id}>in {o.name || o.id}</option>{/each}
            </select>
            <label class="startsel"><input type="radio" name="estart" checked={st.start === loc.id}
              onchange={() => { stories.current.start = loc.id; saveSoon(); }} /> start</label>
            <button class="cast-rm" onclick={() => removeLocation(i)} title="Delete">×</button>
          </div>
          <input class="ip fld" bind:value={loc.description} oninput={saveSoon} placeholder="description (objective, no people)" />
          <div class="bgrow">
            <input class="ip fld" bind:value={loc.background_prompt} oninput={saveSoon} placeholder="background prompt — pure environment, Danbooru tags" />
            <button class="ghost xs" onclick={() => tagifyBg(loc)} disabled={tagging[loc.id]} title="convert prose → tags">{tagging[loc.id] ? '…' : '⇥ tagify'}</button>
          </div>
        </div>
      {/each}
      <button class="add-loc" onclick={addLocation}>+ Add a location</button>
    </div>
  </details>

  <!-- Places & scenes — story-authored containers + character-anchored spots -->
  <details class="dp-details">
    <summary class="dp-summary">🗺 Places & scenes <span class="dp-count">{(st.places || []).length || ''}</span></summary>
    <div class="dp-body">
      <p class="dp-hint">The world’s spots — a <b>place</b> (the house) holds character <b>scenes</b> (mom in the kitchen, sister’s room). The director places characters in their spots automatically. Mark one a <b>🏠 home slot</b> and an embodied persona’s home stands in for it.</p>
      <PlacesEditor storyKey={st.key} places={st.places || []} cast={castOptions} onChange={savePlaces} />
    </div>
  </details>

  <!-- Memory window — how far the narrative thread keeps turns verbatim -->
  <details class="dp-details">
    <summary class="dp-summary">🧠 Memory window <span class="dp-count">{st.recent_window ?? 8} turns</span></summary>
    <div class="dp-body">
      <p class="dp-hint">How many of the most recent play turns the story keeps <b>verbatim</b>. Older turns get compressed into each character’s memory when the player sleeps or dies. Smaller = leaner context; larger = more recent detail carried forward.</p>
      <div class="win-row">
        <input class="win-slider" type="range" min="2" max="40" step="1"
          value={st.recent_window ?? 8} oninput={(e) => saveWindow(e.currentTarget.value)} />
        <input class="win-num" type="number" min="2" max="40"
          value={st.recent_window ?? 8} oninput={(e) => saveWindow(e.currentTarget.value)} />
        <span class="win-unit">turns</span>
      </div>
    </div>
  </details>

  {:else if tab === 'plot'}
  <!-- Plot — novels show the linear chapter manuscript; VNs the arc/timeline outline. -->
  {#if st.type === 'novel'}
    <NovelChapters storyKey={st.key} chapters={st.chapters || []} onChange={() => loadStory(st.key)} />
  {:else if st.type === 'vn'}
    <SceneRoutes storyKey={st.key} scenes={st.scenes || []} features={st.features || []}
                 startScene={st.start_scene || ''} cast={castOptions} onChange={() => loadStory(st.key)} />
  {:else if st.arcs?.length}
    <div class="arc-sections">
      {#each st.arcs as arc, arcIdx}
        {@const nodes = walkNodes(arc)}
        <div class="arc-section">
          <!-- Arc header -->
          <div class="arc-head">
            <span class="arc-num">{arcIdx + 1}</span>
            <span class="arc-name">{arc.name || `Arc ${arcIdx + 1}`}</span>
            {#if arc.dramatic_function}
              <span class="chip df">{arc.dramatic_function}</span>
            {/if}
            <button class="arc-edit" onclick={() => openArcModal(arcIdx)} title="Edit arc details">✎</button>
            <span class="sp"></span>
            <!-- Cast chips — protagonist locked, others removable -->
            {#each (arc.cast || []) as ckey}
              <span class="chip cast-chip" class:locked={ckey === primaryCharKey} title={ckey === primaryCharKey ? 'Protagonist — always present' : ckey}>
                {charName(ckey) || ckey}
                {#if ckey !== primaryCharKey}
                  <button class="cast-rm" onclick={() => removeFromArcCast(arc, arcIdx, ckey)} title="Remove from arc">×</button>
                {/if}
              </span>
            {/each}
            <!-- Add cast member -->
            <div class="cast-add-wrap">
              <button class="cast-add" onclick={() => castPickerArcId = castPickerArcId === arc.id ? null : arc.id} title="Add character to arc">+</button>
              {#if castPickerArcId === arc.id}
                <div class="cast-picker">
                  {#each availableForArc(arc) as m}
                    <button class="cast-pick-item" onclick={() => addToArcCast(arc, arcIdx, m.character)}>
                      {charName(m.character) || m.character}
                    </button>
                  {:else}
                    <span class="cast-pick-empty">All story cast already in this arc</span>
                  {/each}
                </div>
              {/if}
            </div>
          </div>

          <!-- Mini ending -->
          {#if arc.mini_ending}
            <p class="arc-mini">Ends: {arc.mini_ending}</p>
          {/if}

          <!-- Rationale -->
          {#if arc.rationale}
            <p class="arc-rat">{arc.rationale}</p>
          {/if}

          <!-- Arc body: timelines, legacy chapters, generating state, or action buttons -->
          {#if generatingTimelineArc === arc.id}
            <div class="expand-stream">
              <div class="stream-head">
                <span class="spin"></span>
                <span>{tlPhase || 'Generating timelines…'}</span>
              </div>
              {#each Object.entries(tlStreamsByTl) as [tlId, text]}
                {#if text}
                  <div class="tl-stream-block">
                    <div class="tl-stream-label">{tlId}</div>
                    <pre class="stream-text">{text}</pre>
                  </div>
                {/if}
              {/each}
            </div>
          {:else if arc.timelines?.length}
            <!-- Timeline rows in list view -->
            {#each arc.timelines as tl, tlIdx}
              {@const tlNodes = walkNodes(tl.nodes || {}, tl.start)}
              <div class="timeline-section">
                <div class="tl-head-row">
                  <span class="tl-dot" style={`background:var(--tl${tlIdx}-color,#6db4ff)`}></span>
                  <span class="tl-name">{tl.name}</span>
                  {#if tl.premise}<span class="tl-premise">{tl.premise}</span>{/if}
                </div>
                {#if tlNodes.length}
                  <div class="chapter-list">
                    {#each tlNodes as node, chIdx}
                      <ChapterCard chapter={node} index={chIdx}
                        onRegen={() => openRegenModal(arc, arcIdx, node.id, node, chIdx)} />
                    {/each}
                  </div>
                {/if}
              </div>
            {/each}
            <button class="expand-btn regen" onclick={() => handleGenerateTimelines(arc)}
              disabled={!!generatingTimelineArc} title="Re-derive timelines from persona">
              ↻ Regenerate timelines
            </button>
          {:else if nodes.length}
            <!-- Legacy flat chapters -->
            <div class="chapter-list">
              {#each nodes as node, chIdx}
                <ChapterCard chapter={node} index={chIdx}
                  onRegen={() => openRegenModal(arc, arcIdx, node._key, node, chIdx)} />
              {/each}
            </div>
          {:else if expandingArc === arc.id}
            <div class="expand-stream">
              <div class="stream-head"><span class="spin"></span><span>Expanding arc…</span></div>
              {#if expandStream}<pre class="stream-text">{expandStream}</pre>{/if}
            </div>
          {:else}
            <div class="arc-actions">
              <button class="expand-btn primary"
                onclick={() => handleGenerateTimelines(arc)}
                disabled={!!generatingTimelineArc || !!expandingArc}>
                ⑂ Generate timelines
              </button>
              <button class="expand-btn"
                onclick={() => handleExpand(arc)}
                disabled={!!expandingArc || !!generatingTimelineArc}>
                ⊕ Expand flat
              </button>
            </div>
          {/if}
        </div>
      {/each}
    </div>
    {:else if st.storyboard?.beats?.length}
    <!-- Flat-beat stories (no arcs): simple chapter list -->
    <h4>Chapters <span class="lo">— {st.storyboard.beats.length} beats</span></h4>
    <ol class="beats">
      {#each st.storyboard.beats as b, i}
        <li>
          <div class="btitle">{b.title || `Chapter ${i + 1}`}</div>
          <span class="bsum">{b.summary}</span>
          <span class="bmeta">
            {#if b.location}@ {b.location}{/if}
            {#if b.characters?.length} · {b.characters.join(', ')}{/if}
          </span>
        </li>
      {/each}
    </ol>
  {:else}
    <div class="empty-plot">No plot yet — ask the Author to draft arcs, or generate a storyboard.</div>
  {/if}

  {:else if tab === 'relationships'}
    <StoryCanvas layer="relationships" story={st} storyKey={st.key} cast={castOptions}
                 focus={selectedCharKey || primaryCharKey} onSelectChar={openCharModal} />

  {:else if tab === 'map'}
    <StoryCanvas layer="map" story={st} storyKey={st.key} cast={castOptions}
                 onSelectNode={selectGraphNode} />
  {/if}

</div></div>

<!-- Voice story agent (Phase 1): speak an edit; it applies via graph-ops + highlights the change -->
<AgentChat storyKey={st.key} primaryChar={primaryCharKey} onApplied={handleApplied} onSpeaker={(s) => speaker = s} />
{#if agentMsg}
  <div class="agent-toast">{agentMsg}</div>
{/if}

<!-- Character detail modal — opens on a node click or when the agent modifies a character -->
{#if modalChar}
  <CharacterModal char={modalChar} bonds={modalBonds} storyKey={st.key} onClose={closeCharModal} />
{/if}

<!-- SceneModal for chapter editing + per-location background generation -->
{#if regenModal}
  <SceneModal
    chapter={regenModal.chapter}
    index={regenModal.chapterIdx}
    board={st.storyboard || {}}
    charKey={primaryCharKey}
    storyKey={st.key}
    locations={st.locations || []}
    onSave={handleRegenSave}
    onBgPicked={() => loadStory(st.key)}
    onClose={closeRegenModal}
  />
{/if}

<!-- ArcModal for arc-detail editing + expansion -->
{#if arcModal && st.arcs?.[arcModal.arcIdx]}
  {@const arc = st.arcs[arcModal.arcIdx]}
  <ArcModal
    {arc}
    index={arcModal.arcIdx}
    expanded={!!Object.keys(arc.nodes || {}).length}
    onSave={handleArcSave}
    onExpand={() => { closeArcModal(); handleExpand(arc); }}
    onClose={closeArcModal}
  />
{/if}

<style>
  /* ── Layout ───────────────────────────────────────────────────────────────── */
  .page { padding: 0; }
  /* Make room for the docked AgentChat sidebar (340px + gutter). */
  .page.withchat { padding-left: 356px; }
  .col  { display: flex; flex-direction: column; gap: 14px; }

  /* ── Actions ──────────────────────────────────────────────────────────────── */
  .vacts { display: flex; gap: 8px; align-items: center; }
  .del:hover { color: var(--bad, #ff7a7a); border-color: var(--bad, #ff7a7a); }
  .arc-edit {
    width: 22px; height: 22px; flex: none; padding: 0; border-radius: 6px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--faint);
    font-size: 11px; line-height: 1; cursor: pointer;
  }
  .arc-edit:hover { color: var(--accent); border-color: var(--accent); }

  /* ── Heart callout ────────────────────────────────────────────────────────── */
  .heart-callout {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    background: rgba(var(--accent-rgb, 109,140,255), .07);
    border: 1px solid rgba(var(--accent-rgb, 109,140,255), .18);
    border-radius: 9px;
    padding: 10px 13px;
  }
  .heart-icon { font-size: 14px; color: var(--accent); flex: none; margin-top: 1px; }
  .heart-text  { font-size: 13.5px; color: var(--text); font-style: italic; line-height: 1.55; }

  /* ── Edit-in-place fields — look like text, reveal a border on hover/focus ──── */
  .ip {
    width: 100%; box-sizing: border-box; background: transparent; color: var(--text);
    font: inherit; border: 1px solid transparent; border-radius: 8px; padding: 5px 8px;
    transition: border-color .12s, background .12s;
  }
  .ip:hover { border-color: var(--border-soft); }
  .ip:focus { outline: none; border-color: var(--accent); background: var(--elev); }
  .ip-title   { font-size: 22px; font-weight: 800; margin: 0 0 2px -8px; }
  .ip-logline { font-size: 14.5px; font-style: italic; margin-left: -8px; }
  .ip-prem    { font-size: 13.5px; color: var(--muted); line-height: 1.6; resize: none; margin-left: -8px; }
  /* ── premise coverage ── */
  .cov { margin: 4px 0 14px; }
  .cov-head { display: flex; align-items: baseline; gap: 10px; }
  .cov-t { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .cov-status { font-size: 11.5px; color: var(--faint); }
  .cov-err { font-size: 11.5px; color: var(--bad); }
  .cov-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 6px; margin-top: 8px; }
  .cov-item { display: flex; gap: 8px; padding: 7px 10px; border-radius: 9px; border: 1px solid var(--border-soft); background: var(--elev); transition: border-color .15s, opacity .15s; }
  .cov-item.pend { opacity: .5; }
  .cov-item.gap { border-color: color-mix(in srgb, var(--bad) 45%, transparent); background: color-mix(in srgb, var(--bad) 6%, var(--elev)); }
  .cov-mark { font-weight: 700; line-height: 1.5; color: var(--faint); }
  .cov-item.ok .cov-mark { color: var(--accent); }
  .cov-item.gap .cov-mark { color: var(--bad); }
  .cov-body { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .cov-label { font-size: 12px; font-weight: 600; color: var(--text); }
  .cov-note { font-size: 11.5px; color: var(--faint); line-height: 1.4; }
  .title-row { display: flex; align-items: center; gap: 10px; }
  .title-row .ip-title { flex: 1; }
  .type-badge { flex: none; font-size: 11.5px; color: var(--muted); padding: 3px 9px; border-radius: 999px;
    border: 0.5px solid var(--border); background: var(--elev); white-space: nowrap; }
  /* ── lens tabs ── */
  .lens-tabs { display: flex; gap: 16px; border-bottom: 0.5px solid var(--border); margin: 10px 0 16px; }
  .lens-tab { background: none; border: 0; border-bottom: 2px solid transparent; padding: 8px 2px; margin-bottom: -1px;
    font-size: 13.5px; color: var(--muted); cursor: pointer; }
  .lens-tab:hover { color: var(--text); }
  .lens-tab.on { color: var(--text); border-bottom-color: var(--text); }
  .empty-plot { padding: 28px 4px; color: var(--faint); font-size: 13px; }
  .ip-meta    { display: flex; flex-wrap: wrap; gap: 8px; margin-left: -8px; }
  .ip-tone    { flex: 0 0 220px; font-size: 12.5px; }
  .ip-themes  { flex: 1; min-width: 200px; font-size: 12.5px; }
  textarea.ip { resize: vertical; min-height: 38px; line-height: 1.55; }

  /* ── Cast roster + locations editors ──────────────────────────────────────── */
  .cast-roster { display: flex; flex-wrap: wrap; gap: 6px; }
  .addrow { display: flex; gap: 8px; align-items: center; margin-top: 8px; }
  .loc-box { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 10px;
             background: var(--panel); display: flex; flex-direction: column; gap: 6px; }
  .loc-box.start { border-color: var(--accent); }
  .loc-box.child { margin-left: 18px; border-left: 2px solid var(--border); }
  .loc-top { display: flex; align-items: center; gap: 7px; }
  .loc-top .title { flex: 1; font-weight: 650; }
  .area { font-size: 11px; color: var(--muted); background: var(--bg); border: 1px solid var(--border-soft);
          border-radius: 6px; padding: 4px 6px; max-width: 150px; }
  .startsel { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--muted); white-space: nowrap; }
  .bgrow { display: flex; align-items: center; gap: 7px; }
  .add-loc { align-self: flex-start; font-size: 12.5px; padding: 6px 12px; border-radius: 8px;
             background: var(--elev); border: 1px dashed var(--border); color: var(--muted); }
  .add-loc:hover { border-color: var(--accent); color: var(--accent); }
  .xs { font-size: 11px; padding: 3px 8px; }

  /* ── Chips ────────────────────────────────────────────────────────────────── */
  .chip {
    font-size: 11.5px;
    padding: 2px 9px;
    border-radius: 999px;
    background: var(--elev);
    border: 1px solid var(--border-soft);
    color: var(--muted);
    white-space: nowrap;
  }
  .df {
    background: rgba(109,140,255,.14);
    border-color: rgba(109,140,255,.28);
    color: var(--accent);
    font-size: 10.5px;
    font-weight: 600;
  }
  .cast-chip { font-size: 10.5px; }

  /* ── Default personas ─────────────────────────────────────────────────────── */
  .dp-details { margin: 4px 0; }
  .dp-summary {
    cursor: pointer; user-select: none; list-style: none; display: inline-flex; align-items: center; gap: 7px;
    font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 4px 0;
  }
  .dp-summary::-webkit-details-marker { display: none; }
  .dp-summary:hover { color: var(--muted); }
  .dp-count { font-size: 10px; color: var(--accent); }
  .dp-body { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .dp-hint { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.5; max-width: 560px; }
  .dp-chips { display: flex; flex-wrap: wrap; gap: 7px; }
  .dp-chip {
    display: inline-flex; align-items: center; gap: 7px; padding: 4px 9px 4px 5px; border-radius: 999px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer;
    font-size: 12px;
  }
  .dp-chip:hover { color: var(--text); border-color: var(--border); }
  .dp-chip.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--elev)); }
  .dp-chip img { width: 20px; height: 20px; border-radius: 50%; object-fit: cover; flex: none; }
  .dp-ph { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; font-size: 11px; background: var(--elev-2); flex: none; }
  .dp-mark { font-size: 11px; color: var(--faint); font-weight: 700; }
  .dp-chip.on .dp-mark { color: var(--accent); }
  .dp-empty { font-size: 12px; color: var(--faint); margin: 0; }
  .dp-empty a { color: var(--accent); }

  /* ── Voice agent toast ────────────────────────────────────────────────────── */
  .agent-toast {
    position: fixed; left: 20px; bottom: 74px; z-index: 60; max-width: 320px;
    background: var(--panel); border: 1px solid var(--accent); border-radius: 9px;
    padding: 8px 12px; font-size: 12.5px; color: var(--text);
    box-shadow: 0 6px 24px rgba(0,0,0,.3);
  }

  /* ── Memory window slider ─────────────────────────────────────────────────── */
  .win-row { display: flex; align-items: center; gap: 12px; max-width: 460px; }
  .win-slider { flex: 1; accent-color: var(--accent); cursor: pointer; }
  .win-num {
    width: 60px; padding: 4px 7px; border-radius: 7px; font-size: 12.5px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--text);
  }
  .win-num:focus { outline: none; border-color: var(--accent); }
  .win-unit { font-size: 12px; color: var(--faint); }

  /* ── Arc sections ─────────────────────────────────────────────────────────── */
  .arc-sections {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .arc-section {
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    padding: 14px 16px;
    background: var(--panel);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  /* Arc header row */
  .arc-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .arc-num {
    width: 22px; height: 22px; flex: none;
    border-radius: 50%;
    display: grid; place-items: center;
    font-size: 10.5px; font-weight: 700;
    background: var(--accent);
    color: #0b0e14;
  }
  .arc-name {
    font-size: 14px;
    font-weight: 700;
    color: var(--text);
  }
  .sp { flex: 1; }

  /* Arc body text */
  .arc-mini {
    margin: 0;
    font-size: 12.5px;
    color: var(--muted);
    font-style: italic;
    line-height: 1.45;
  }
  .arc-rat {
    margin: 0;
    font-size: 11.5px;
    color: var(--faint);
    font-style: italic;
    line-height: 1.4;
  }

  /* Chapter grid within arc */
  .chapter-list {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 10px;
  }

  /* Action row for two-button state */
  .arc-actions { display: flex; gap: 8px; flex-wrap: wrap; }

  /* Expand / generate buttons */
  .expand-btn {
    align-self: flex-start;
    font-size: 12.5px; font-weight: 600;
    padding: 7px 14px; border-radius: 8px;
    background: var(--elev); border: 1px solid var(--border);
    color: var(--text); cursor: pointer;
  }
  .expand-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .expand-btn:disabled { opacity: .45; cursor: not-allowed; }
  .expand-btn.primary {
    background: rgba(109,140,255,.12); border-color: rgba(109,140,255,.35);
    color: var(--accent);
  }
  .expand-btn.primary:hover:not(:disabled) { background: rgba(109,140,255,.22); }
  .expand-btn.regen { font-size: 11.5px; opacity: .7; }
  .expand-btn.regen:hover:not(:disabled) { opacity: 1; }

  /* Timeline rows in list mode */
  .timeline-section { display: flex; flex-direction: column; gap: 8px; }
  .tl-head-row { display: flex; align-items: baseline; gap: 8px; }
  .tl-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
  .tl-name { font-size: 12px; font-weight: 800; color: var(--text); }
  .tl-premise { font-size: 11.5px; color: var(--faint); font-style: italic; }

  /* Timeline streaming blocks */
  .tl-stream-block { border-top: 1px solid var(--border-soft); }
  .tl-stream-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--faint); padding: 4px 12px 0;
  }

  /* Streaming preview */
  .expand-stream {
    border: 1px solid var(--border);
    border-radius: 9px;
    overflow: hidden;
    background: var(--elev);
  }
  .stream-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 12px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
    background: rgba(109,140,255,.08);
    border-bottom: 1px solid var(--border-soft);
  }
  .stream-text {
    max-height: 200px;
    overflow: auto;
    margin: 0;
    padding: 8px 12px;
    font-size: 11px;
    color: var(--muted);
    white-space: pre-wrap;
    word-break: break-word;
    font-family: ui-monospace, monospace;
    line-height: 1.5;
  }

  /* ── Fallback flat beats ──────────────────────────────────────────────────── */
  h4 { margin: 6px 0 4px; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .beats { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 12px; }
  .beats li { font-size: 13px; color: var(--text); }
  .btitle { font-weight: 700; font-size: 13.5px; margin-bottom: 2px; }
  .bsum { font-size: 13px; color: var(--muted); }
  .bmeta { color: var(--faint); font-size: 11.5px; margin-left: 6px; }

  /* ── Cast editing ────────────────────────────────────────────────────────── */
  .cast-chip { display: inline-flex; align-items: center; gap: 3px; }
  .cast-chip.locked { opacity: .8; }
  .cast-rm {
    width: 13px; height: 13px; padding: 0; border-radius: 50%;
    background: none; border: none;
    color: var(--muted); font-size: 11px; line-height: 1;
    cursor: pointer; display: grid; place-items: center; flex: none;
  }
  .cast-rm:hover { color: var(--bad, #ff7a7a); }

  .cast-add-wrap { position: relative; }
  .cast-add {
    width: 20px; height: 20px; padding: 0; border-radius: 50%;
    background: var(--elev); border: 1px dashed var(--border);
    color: var(--muted); font-size: 14px; line-height: 1;
    cursor: pointer; display: grid; place-items: center;
  }
  .cast-add:hover { border-color: var(--accent); color: var(--accent); }

  .cast-picker {
    position: absolute; top: calc(100% + 4px); right: 0; z-index: 50;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 9px; padding: 4px; min-width: 140px;
    box-shadow: 0 4px 16px rgba(0,0,0,.25);
    display: flex; flex-direction: column; gap: 1px;
  }
  .cast-pick-item {
    padding: 6px 10px; border-radius: 6px; font-size: 12.5px;
    color: var(--text); background: none; border: none;
    cursor: pointer; text-align: left;
  }
  .cast-pick-item:hover { background: var(--elev); }
  .cast-pick-empty { font-size: 11.5px; color: var(--faint); padding: 6px 10px; }

  /* ── View toggle ────────────────────────────────────────────────────────── */
  .view-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 7px;
    overflow: hidden;
  }
  .vt-btn {
    padding: 3px 8px;
    font-size: 13px;
    background: none;
    border: none;
    color: var(--faint);
    cursor: pointer;
    line-height: 1;
  }
  .vt-btn:hover { color: var(--text); background: var(--elev); }
  .vt-btn.active { color: var(--accent); background: rgba(109,140,255,.12); }

  /* ── Spinner ──────────────────────────────────────────────────────────────── */
  .spin {
    width: 12px; height: 12px; flex: none;
    border-radius: 50%;
    border: 2px solid rgba(109,140,255,.3);
    border-top-color: var(--accent);
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

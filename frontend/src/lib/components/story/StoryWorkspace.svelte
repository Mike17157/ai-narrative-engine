<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { charName, chars, loadChars } from '$lib/characters.svelte.js';
  import { stories, deleteStory, expandArc, generateTimelines, loadStory, setStoryMode, persistCurrent } from '$lib/stories.svelte.js';
  import { patch, put, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ChapterCard from '$lib/components/story/ChapterCard.svelte';
  import SceneModal  from '$lib/components/story/SceneModal.svelte';
  import ArcModal    from '$lib/components/story/ArcModal.svelte';
  import CharacterModal from '$lib/components/story/CharacterModal.svelte';
  import PlacesEditor from '$lib/components/story/PlacesEditor.svelte';
  import NovelChapters from '$lib/components/story/NovelChapters.svelte';
  import SceneRoutes from '$lib/components/story/SceneRoutes.svelte';
  import Section from '$lib/components/story/Section.svelte';
  import DefaultPersonas from '$lib/components/story/DefaultPersonas.svelte';
  import ArcPanel from '$lib/components/story/ArcPanel.svelte';
  import RelationshipFlow from '$lib/components/story/RelationshipFlow.svelte';
  import LocationsPanel from '$lib/components/story/LocationsPanel.svelte';
  import QueueButton from '$lib/components/story/QueueButton.svelte';
  import WorkflowModal from '$lib/components/story/WorkflowModal.svelte';
  import SectionChat from '$lib/components/story/SectionChat.svelte';
  import { workflow, closeWorkflow } from '$lib/workflow.svelte.js';

  // The active tab IS the editable section (overview/map/relationships/plot all map to a card layer).
  const SECTION_LABEL = { overview: 'Premise & theme', map: 'World', relationships: 'Cast & bonds', plot: 'Arc & scenes' };
  let editable = $derived(['overview', 'map', 'relationships', 'plot'].includes(tab));
  import ConditionsPanel from '$lib/components/story/ConditionsPanel.svelte';

  let st = $derived(stories.current);
  loadChars();

  // ── Places & scenes: story-authored containers + character-anchored spots ───
  // Enriched cast: name + role + base image (for the relationship-graph character cards).
  let castOptions = $derived((st?.cast || []).map((m) => {
    const ci = chars.list.find((c) => c.key === m.character);
    return {
      key: m.character, name: charName(m.character) || m.character, primary: m.primary,
      role: ci?.fields?.role || '', home: m.home || '',
      img: ci?.reference || ci?.avatar || (ci?.images || []).map((im) => im?.url).find(Boolean) || null,
    };
  }));

  // ── Relationships roster: characters grouped by home location (replaces the ring). ──
  function setCharHome(charKey, locId) {
    stories.current.cast = (st.cast || []).map((m) => m.character === charKey ? { ...m, home: locId } : m);
    saveSoon();
  }
  function addCharToLocation(locId, charKey) {
    if ((st.cast || []).some((m) => m.character === charKey)) { setCharHome(charKey, locId); return; }
    stories.current.cast = [...(st.cast || []), { character: charKey, primary: false, home: locId }];
    saveSoon();
  }
  // Bonds save TARGETED (not through editPayload) so an unrelated overview save can never
  // clobber relationship edits with stale client state.
  function saveBonds(next) {
    stories.current.relationships = next;
    put(`/stories/${st.key}`, { relationships: next });
  }
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

  // Locations: the Map tab is a hierarchical LocationsPanel (edit + scene images). These are the
  // structural ops it calls back into; field edits mutate the loc objects + saveSoon directly.
  let locUid = 0;
  function addLocation(parent = '') {
    const id = `loc_${Date.now().toString(36)}_${locUid++}`;
    stories.current.locations = [...(st.locations || []), { id, name: 'New location', description: '', background_prompt: '', parent }];
    saveSoon();
  }
  function removeLocationById(id) {
    stories.current.locations = (st.locations || []).filter((l) => l.id !== id)
      .map((l) => l.parent === id ? { ...l, parent: '' } : l);   // orphaned children float to top level
    if (st.start === id) stories.current.start = null;
    saveSoon();
  }
  function setStart(id) { stories.current.start = id; saveSoon(); }
  // Setting conditions: replace the list wholesale (generate/accept/edit/remove) + persist.
  function setConditions(next) { stories.current.conditions = next; saveSoon(); }

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

  // The four surfaces (Overview · Plot · Relationships · Map) are subnav tabs driven by the URL's
  // ?tab= (the bar lives in the app subnav now — see storiesTree). Bare /structure canonicalises to
  // overview so the subnav has something to highlight.
  let tab = $derived($page.url.searchParams.get('tab') || 'overview');
  $effect(() => {
    if (st && !$page.url.searchParams.get('tab')) {
      goto(`/stories/${st.key}/structure?tab=overview`, { replaceState: true, keepFocus: true, noScroll: true });
    }
  });

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
  // Character detail: clicking a character card opens the modal.
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

{#if editable}
  <SectionChat storyKey={st.key} layer={tab} layerLabel={SECTION_LABEL[tab] || tab} />
{/if}

<div class="page" class:withchat={editable}><div class="col">

  <!-- Header row: title (edit in place) + format badge + actions, one line -->
  <div class="title-row">
    <input class="ip ip-title" bind:value={st.name} oninput={saveSoon} placeholder="Untitled story" />
    <span class="type-badge">{st.type === 'vn' ? '🎴 Visual novel' : '📖 Novel'}</span>
    <QueueButton storyKey={st.key} />
    <button class="ghost sm del" onclick={() => deleteStory(st.key)}>Delete</button>
  </div>

  <!-- The ONE workflow modal — opened from the To-do button (or any deep-link) to walk sections -->
  {#if workflow.open && workflow.storyKey === st.key}
    <WorkflowModal storyKey={st.key} startLayer={workflow.startLayer} onClose={closeWorkflow} />
  {/if}

  {#if tab === 'overview'}
  <!-- Overview = basic configuration + structure. The premise COMPONENTS (philosophy, lie,
       inciting…) and other AI-drafted work live in the To-do queue, not on this page. -->
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

  <!-- Tone + themes (edit in place) -->
  <div class="ip-meta">
    <input class="ip ip-tone" bind:value={st.tone} oninput={saveSoon} placeholder="tone (e.g. melancholy, hopeful)" />
    <input class="ip ip-themes" bind:value={themesStr} oninput={commitThemes} placeholder="themes, comma separated" />
  </div>

  <!-- Art style — LAYER 0 of the image card: every image this story renders (sprites AND
       location scenes) opens with this line. Decided here; stacked visibly in Cast ≣ Layers. -->
  <Section icon="🎨" title="Art style" count={st.art_style ? 'set' : 'global default'}>
    <p class="hint">The first layer of <b>every image</b> in this story — character sprites and location
      scenes lead with it, so the whole world renders in one style. Leave empty to use the global cast
      style. See the full stack per sprite via <b>≣ Layers</b> in Cast.</p>
    <textarea class="ip ip-style" use:autosize={st.art_style} bind:value={st.art_style} oninput={saveSoon}
              placeholder="e.g. Polished 2D anime illustration, crisp lineart, rich cel shading…"></textarea>
  </Section>

  <!-- Cast roster — membership lives here (the Cast section is the catalogue/outfit surface) -->
  <Section icon="👥" title="Cast" count={(st.cast || []).length || ''}>
    <p class="hint">The characters in this story. ★ marks the protagonist — click a chip to open the card, or manage looks in the <b>Cast</b> section.</p>
    <div class="cast-roster">
      {#each st.cast || [] as m, i (m.character)}
        <button class="chip cast-chip lg" class:locked={m.primary} onclick={() => openCharModal(m.character)} title={m.primary ? 'Protagonist' : 'Open card'}>
          {m.primary ? '★ ' : ''}{charName(m.character) || m.character}
          {#if !m.primary}<span class="cast-rm" role="button" tabindex="0" onclick={(e) => { e.stopPropagation(); removeCastMember(i); }} title="Remove from cast">×</span>{/if}
        </button>
      {/each}
    </div>
    <div class="addrow">
      <Combobox items={castAddItems} bind:value={addPick} placeholder="add character…" />
      <button class="ghost sm" onclick={addCastMember} disabled={!addPick}>＋ Add</button>
    </div>
  </Section>

  <!-- Default personas — the playable "you" cards this story suggests -->
  <DefaultPersonas />

  <!-- Memory window — how far the narrative thread keeps turns verbatim -->
  <Section icon="🧠" title="Memory window" count={`${st.recent_window ?? 8} turns`}>
    <p class="hint">How many of the most recent play turns the story keeps <b>verbatim</b>. Older turns get compressed into each character’s memory when the player sleeps or dies. Smaller = leaner context; larger = more recent detail carried forward.</p>
    <div class="win-row">
      <input class="win-slider" type="range" min="2" max="40" step="1"
        value={st.recent_window ?? 8} oninput={(e) => saveWindow(e.currentTarget.value)} />
      <input class="win-num" type="number" min="2" max="40"
        value={st.recent_window ?? 8} oninput={(e) => saveWindow(e.currentTarget.value)} />
      <span class="win-unit">turns</span>
    </div>
  </Section>

  {:else if tab === 'plot'}
  <!-- The ARC — the planned progression play steers through (view + plan) -->
  <ArcPanel storyKey={st.key} conditions={st.conditions || []} />

  <!-- Storyboard outline — the beat plan, whatever the story format (novels draft chapters
       FROM these; the rail counts them, so they must be visible here). -->
  {#if st.storyboard?.beats?.length}
    <Section icon="🧭" title="Outline" count={`${st.storyboard.beats.length} beats`}>
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
    </Section>
  {/if}

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
            <button class="ghost sm" onclick={() => handleGenerateTimelines(arc)}
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
              <button class="primary sm"
                onclick={() => handleGenerateTimelines(arc)}
                disabled={!!generatingTimelineArc || !!expandingArc}>
                ⑂ Generate timelines
              </button>
              <button class="soft sm"
                onclick={() => handleExpand(arc)}
                disabled={!!expandingArc || !!generatingTimelineArc}>
                ⊕ Expand flat
              </button>
            </div>
          {/if}
        </div>
      {/each}
    </div>
    {:else if !st.storyboard?.beats?.length}
    <div class="empty-plot">No plot yet — draft arcs or generate a storyboard.</div>
  {/if}

  {:else if tab === 'relationships'}
    <!-- The MC-rooted relationship FLOW: every bond radiates from the lead; the left→right seam is
         the depth axis (surface → known → secret, coming next). Click a face → card, a bond → edit. -->
    <RelationshipFlow storyKey={st.key} cast={castOptions} locations={st.locations || []}
                      relationships={st.relationships || []}
                      onSelect={openCharModal} onSaveBonds={saveBonds} onSetHome={setCharHome} />

  {:else if tab === 'map'}
    <!-- Setting stages: the recurring conditions the world moves through (situational content keys to these) -->
    <Section icon="🌐" title="Setting stages" count={(st.conditions || []).length || ''}>
      <ConditionsPanel storyKey={st.key} conditions={st.conditions || []} onChange={setConditions} />
    </Section>

    <!-- The world's locations, grouped by area — each edits in place + carries its scene image. -->
    <LocationsPanel storyKey={st.key} locations={st.locations || []} start={st.start || ''}
                    onChange={saveSoon} onAdd={addLocation} onRemove={removeLocationById} onSetStart={setStart} />

    <Section icon="🗺" title="Places & scenes" count={(st.places || []).length || ''}>
      <p class="hint">The world’s spots — a <b>place</b> (the house) holds character <b>scenes</b> (mom in the kitchen, sister’s room). The director places characters in their spots automatically. Mark one a <b>🏠 home slot</b> and an embodied persona’s home stands in for it.</p>
      <PlacesEditor storyKey={st.key} places={st.places || []} cast={castOptions} onChange={savePlaces} />
    </Section>
  {/if}

</div></div>

<!-- Character detail modal — opens on a node click or when a character card is clicked -->
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
  .page.withchat { padding-left: 336px; }   /* room for the fixed left section editor (320px) */
  @media (max-width: 1100px) { .page.withchat { padding-left: 0; } }
  .col  { display: flex; flex-direction: column; gap: 14px; }

  /* ── Actions ──────────────────────────────────────────────────────────────── */
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
  .ip-style   { font-size: 12.5px; color: var(--muted); line-height: 1.55; resize: none; }
  /* ── premise components — editable, AI-draftable ── */
  .cov { margin: 4px 0 14px; }
  .cov-head { display: flex; align-items: center; gap: 10px; }
  .cov-t { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .cov-status { font-size: 11.5px; color: var(--faint); }
  .cov-err { font-size: 11.5px; color: var(--bad); }
  .pfill { font-size: 11.5px; font-weight: 600; padding: 4px 11px; border-radius: 999px; background: none;
           border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .pfill:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .cov-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 8px; margin-top: 8px; }
  .cov-item { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; border-radius: 9px;
              border: 1px solid var(--border-soft); background: var(--elev); transition: border-color .15s; }
  .cov-item.gap { border-style: dashed; }
  .cov-item:focus-within { border-color: var(--accent); }
  .cov-top { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .cov-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .pbtn { width: 22px; height: 22px; flex: none; padding: 0; border-radius: 6px; display: grid; place-items: center;
          background: none; border: 1px solid var(--border-soft); color: var(--faint); font-size: 11px; cursor: pointer; }
  .pbtn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .cov-input { width: 100%; box-sizing: border-box; background: transparent; border: none; resize: none;
               font: inherit; font-size: 12.5px; color: var(--text); line-height: 1.5; padding: 0; min-height: 20px; }
  .cov-input:focus { outline: none; }
  .cov-input::placeholder { color: var(--faint); font-style: italic; }
  .title-row { display: flex; align-items: center; gap: 10px; }
  .title-row .ip-title { flex: 1; }
  .type-badge { flex: none; font-size: 11.5px; color: var(--muted); padding: 3px 9px; border-radius: 999px;
    border: 0.5px solid var(--border); background: var(--elev); white-space: nowrap; }
  p.hint { margin: 0 0 4px; line-height: 1.5; }
  .cast-chip.lg { font-size: 12.5px; padding: 5px 12px; cursor: pointer; }
  .cast-chip.lg:hover { border-color: var(--accent); color: var(--text); }
  .empty-plot { padding: 28px 4px; color: var(--faint); font-size: 13px; }
  .ip-meta    { display: flex; flex-wrap: wrap; gap: 8px; margin-left: -8px; }
  .ip-tone    { flex: 0 0 220px; font-size: 12.5px; }
  .ip-themes  { flex: 1; min-width: 200px; font-size: 12.5px; }
  textarea.ip { resize: vertical; min-height: 38px; line-height: 1.55; }

  /* ── Cast roster ──────────────────────────────────────────────────────────── */
  .cast-roster { display: flex; flex-wrap: wrap; gap: 6px; }
  .addrow { display: flex; gap: 8px; align-items: center; margin-top: 8px; }

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

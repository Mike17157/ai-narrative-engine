<script>
  import { goto } from '$app/navigation';
  import { charName } from '$lib/characters.svelte.js';
  import { stories, deleteStory, regenStory, expandArc, generateTimelines, loadStory, setStoryMode, persistCurrent } from '$lib/stories.svelte.js';
  import { patch } from '$lib/api.js';
  import ChapterCard from '$lib/components/story/ChapterCard.svelte';
  import SceneModal  from '$lib/components/story/SceneModal.svelte';
  import ArcModal    from '$lib/components/story/ArcModal.svelte';
  import StorySettingsModal from '$lib/components/story/StorySettingsModal.svelte';
  import StoryGraph  from '$lib/components/graph/StoryGraph.svelte';
  import SpineDisplay from '$lib/components/story/SpineDisplay.svelte';

  let st = $derived(stories.current);

  // Arc expansion state
  let expandingArc  = $state(null);
  let expandStream  = $state('');

  // SceneModal state
  let regenModal    = $state(null);   // { arcIdx, nodeId, chapter, chapterIdx }

  // Cast picker state
  let castPickerArcId = $state(null);  // arc id whose picker is open

  // Whole-story canvas mode (persisted in the store): 'graph' | 'list'
  let mode = $derived(stories.view[st?.key]?.mode || 'graph');

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
  // Story-settings modal (title / premise / tone / cast / locations / chapters)
  let settingsOpen = $state(false);

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

<div class="page"><div class="col">

  <!-- Actions row -->
  <div class="vacts">
    {#if st.arcs?.length || st.storyboard?.beats?.length}
      <div class="view-toggle">
        <button class="vt-btn" class:active={mode === 'graph'} onclick={() => setStoryMode(st.key, 'graph')} title="Map view">⊞ Map</button>
        <button class="vt-btn" class:active={mode === 'list'} onclick={() => setStoryMode(st.key, 'list')} title="List view">☰ List</button>
      </div>
    {/if}
    <span class="sp"></span>
    <button class="ghost sm" onclick={() => goto(`/stories/${st.key}/workshop`)} title="Talk through changes; revise the story graph">⚒ Iterate</button>
    <button class="ghost sm" onclick={() => settingsOpen = true}>✎ Edit</button>
    <button class="ghost sm" onclick={() => regenStory(st)}>↻ Regenerate</button>
    <button class="ghost sm del" onclick={() => deleteStory(st.key)}>Delete</button>
  </div>

  <!-- Heart callout -->
  {#if st.storyboard?.heart}
    <div class="heart-callout">
      <span class="heart-icon">♡</span>
      <span class="heart-text">{st.storyboard.heart}</span>
    </div>
  {/if}

  <!-- Logline -->
  {#if st.storyboard?.logline}
    <p class="logline">{st.storyboard.logline}</p>
  {/if}

  <!-- Premise -->
  {#if st.premise}
    <p class="prem">{st.premise}</p>
  {/if}

  <!-- Tone + themes -->
  <div class="meta-row">
    {#if st.tone}
      <span class="chip tone-chip">{st.tone}</span>
    {/if}
    {#each (st.themes || []) as t}
      <span class="chip">{t}</span>
    {/each}
  </div>

  <!-- Spine — emotional psychology skeleton -->
  {#if st.spine?.wound}
    <details class="spine-details">
      <summary class="spine-summary">⟳ Emotional Spine</summary>
      <div style="margin-top:10px">
        <SpineDisplay spine={st.spine} compact />
      </div>
    </details>
  {/if}

  <!-- Intended ending -->
  {#if st.intended_ending}
    <div class="ending-callout">
      <span class="ec-label">🏁 Intended ending</span>
      <span class="ec-text">{st.intended_ending}</span>
    </div>
  {/if}

  <!-- ── Whole-story canvas, or per-arc / flat list ───────────────────── -->
  {#if st.arcs?.length || st.storyboard?.beats?.length}
    {#if mode === 'graph'}
      <StoryGraph story={st} storyKey={st.key} onSelectNode={selectGraphNode} onSelectArc={openArcModal} />
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
    {:else}
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
    {/if}
  {/if}

</div></div>

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

<!-- Story-settings modal (replaces the old Edit tab) -->
{#if settingsOpen}
  <StorySettingsModal onClose={() => { settingsOpen = false; }} />
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
  .col  { display: flex; flex-direction: column; gap: 14px; }

  /* ── Actions ──────────────────────────────────────────────────────────────── */
  .vacts { display: flex; gap: 8px; align-items: center; }
  .del:hover { color: var(--bad, #ff7a7a); border-color: var(--bad, #ff7a7a); filter: none; }
  .arc-edit {
    width: 22px; height: 22px; flex: none; padding: 0; border-radius: 6px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--faint);
    font-size: 11px; line-height: 1; cursor: pointer; box-shadow: none;
  }
  .arc-edit:hover { color: var(--accent); border-color: var(--accent); filter: none; }

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

  /* ── Logline / premise ────────────────────────────────────────────────────── */
  .logline { font-size: 14.5px; color: var(--text); font-style: italic; margin: 0; }
  .prem    { font-size: 13.5px; color: var(--muted); margin: 0; line-height: 1.6; }

  /* ── Meta row ─────────────────────────────────────────────────────────────── */
  .meta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
    margin: 0;
  }

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
  .tone-chip {
    background: rgba(109,140,255,.11);
    border-color: rgba(109,140,255,.25);
    color: var(--accent);
    font-weight: 600;
  }
  .df {
    background: rgba(109,140,255,.14);
    border-color: rgba(109,140,255,.28);
    color: var(--accent);
    font-size: 10.5px;
    font-weight: 600;
  }
  .cast-chip { font-size: 10.5px; }

  /* ── Spine details ─────────────────────────────────────────────────────────── */
  .spine-details { margin: 8px 0; }
  .spine-summary {
    cursor: pointer; user-select: none; list-style: none;
    font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--faint, #555b78); padding: 4px 0;
  }
  .spine-summary::-webkit-details-marker { display: none; }
  .spine-summary:hover { color: var(--muted, #8a92b0); }

  /* ── Ending callout ───────────────────────────────────────────────────────── */
  .ending-callout {
    display: flex;
    flex-direction: column;
    gap: 4px;
    background: rgba(255,200,80,.07);
    border: 1px solid rgba(255,200,80,.2);
    border-radius: 9px;
    padding: 10px 13px;
  }
  .ec-label {
    font-size: 11px;
    font-weight: 700;
    color: rgba(220,170,40,.9);
    text-transform: uppercase;
    letter-spacing: .4px;
  }
  .ec-text {
    font-size: 13.5px;
    color: var(--text);
    line-height: 1.6;
  }

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
    color: var(--text); cursor: pointer; box-shadow: none;
  }
  .expand-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); filter: none; }
  .expand-btn:disabled { opacity: .45; cursor: not-allowed; }
  .expand-btn.primary {
    background: rgba(109,140,255,.12); border-color: rgba(109,140,255,.35);
    color: var(--accent);
  }
  .expand-btn.primary:hover:not(:disabled) { background: rgba(109,140,255,.22); filter: none; }
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
    background: none; border: none; box-shadow: none;
    color: var(--muted); font-size: 11px; line-height: 1;
    cursor: pointer; display: grid; place-items: center; flex: none;
  }
  .cast-rm:hover { color: var(--bad, #ff7a7a); filter: none; }

  .cast-add-wrap { position: relative; }
  .cast-add {
    width: 20px; height: 20px; padding: 0; border-radius: 50%;
    background: var(--elev); border: 1px dashed var(--border);
    color: var(--muted); font-size: 14px; line-height: 1;
    cursor: pointer; display: grid; place-items: center; box-shadow: none;
  }
  .cast-add:hover { border-color: var(--accent); color: var(--accent); filter: none; }

  .cast-picker {
    position: absolute; top: calc(100% + 4px); right: 0; z-index: 50;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 9px; padding: 4px; min-width: 140px;
    box-shadow: 0 4px 16px rgba(0,0,0,.25);
    display: flex; flex-direction: column; gap: 1px;
  }
  .cast-pick-item {
    padding: 6px 10px; border-radius: 6px; font-size: 12.5px;
    color: var(--text); background: none; border: none; box-shadow: none;
    cursor: pointer; text-align: left;
  }
  .cast-pick-item:hover { background: var(--elev); filter: none; }
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
    box-shadow: none;
    color: var(--faint);
    cursor: pointer;
    line-height: 1;
  }
  .vt-btn:hover { color: var(--text); filter: none; background: var(--elev); }
  .vt-btn.active { color: var(--accent); background: rgba(109,140,255,.12); filter: none; }

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

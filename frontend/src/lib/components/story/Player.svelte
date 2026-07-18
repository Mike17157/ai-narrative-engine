<script>
  import { get, post, put } from '$lib/api.js';
  import { goto } from '$app/navigation';
  import { app, setActivePlayerChar } from '$lib/app.svelte.js';
  import { openConfigModal } from '$lib/configModal.svelte.js';
  import { formatChat } from '$lib/chat-format.js';
  import Manuscript from './Manuscript.svelte';

  let { storyKey } = $props();
  const leanStoryMode = import.meta.env.VITE_LEAN_STORY === '1';
  const exitPlay = () => goto(`/stories/${storyKey}`);

  // Lorebooks attached to THIS play thread (world/RPG/etc. books from the manager).
  // Persisted under a dedicated `play-<key>` session so it survives reloads and never
  // collides with the workshop's session for the same story.
  const playSid = `play-${storyKey}`;
  let lorebooks = $state([]);
  async function setLorebooks(v) {
    lorebooks = v;
    await put(`/stories/${storyKey}/session`, { lorebooks: v });
  }
  function openConfig(tab = 'lorebooks') {
    openConfigModal({ tab, lorebooks, onLorebooks: setLorebooks });
  }

  // World-state engine: the mutable model of this playthrough, evolved each turn by
  // the director's state_deltas and surfaced in a side panel.
  let worldState = $state(null);
  let stateLevels = $state(null);   // {level: {size}} across the unified State doc
  let stateRev = $state(0);
  let showState = $state(false);
  let showBeat = $state(false);      // 🧠 Logic panel — the consequence reasoning behind the last turn
  let showMs = $state(false);        // 📖 Manuscript — read/edit the playthrough as literature
  let lastBeat = $state('');
  // The narrative as PAGES you step through (VN-style next/back): the prologue's sections first,
  // then each play turn's narration. `cursor` is where you're reading.
  let pages = $state([]);        // { kind:'prologue'|'turn', title?, text, present?, emotions? }
  let cursor = $state(0);
  let prologueBusy = $state(false);
  let primaryKey = $state('');
  let curPage = $derived(pages[cursor] || null);
  let atEnd = $derived(cursor >= pages.length - 1);
  let pgPresent = $derived(curPage?.present || (curPage?.kind === 'prologue' && primaryKey ? [primaryKey] : []));
  let pgEmotions = $derived(curPage?.emotions || {});
  function nextPage() { if (cursor < pages.length - 1) cursor++; }
  function backPage() { if (cursor > 0) cursor--; }

  // ── VN line stepping — a fresh turn reveals its lines one at a time (click to advance);
  // the current line's SPEAKER pulls sprite focus (their sprite lit, the others dimmed).
  // Only the live edge steps; reading back shows whole pages at once.
  let lineCursor = $state(Infinity);
  let pgLines = $derived(curPage?.lines || []);
  let visibleLines = $derived(atEnd && pgLines.length ? pgLines.slice(0, lineCursor + 1) : pgLines);
  let linesLeft = $derived(atEnd && pgLines.length ? Math.max(0, pgLines.length - visibleLines.length) : 0);
  function advanceLine() { if (linesLeft > 0) lineCursor += 1; }
  let nameToKey = $derived(Object.fromEntries(
    Object.entries(names).map(([k, n]) => [String(n).toLowerCase(), k])));
  // Who we see THROUGH this turn: the POV character, else the player's own puppet. In a
  // first-person or narrator view you don't watch yourself — the stage rotates to show the
  // LAST OTHER character (whoever last spoke), never the viewpoint holder's own sprite.
  let selfKey = $derived(puppet?.key || primaryKey);
  let povKey = $derived(curPage?.pov ? (nameToKey[String(curPage.pov).toLowerCase()] || null) : null);
  let viewerKey = $derived(povKey || selfKey);
  let firstPerson = $derived(!povKey || povKey === selfKey);
  let lastSpeakerKey = $derived.by(() => {
    const l = [...visibleLines].reverse().find((x) => x.kind === 'dialogue' && x.speaker);
    return l ? nameToKey[l.speaker.toLowerCase()] || null : null;
  });
  let lastOtherKey = $derived.by(() => {
    const spoke = [...visibleLines].reverse().find((x) => x.kind === 'dialogue' && x.speaker
        && nameToKey[x.speaker.toLowerCase()] && nameToKey[x.speaker.toLowerCase()] !== viewerKey);
    if (spoke) return nameToKey[spoke.speaker.toLowerCase()];
    const others = pgPresent.filter((k) => k !== viewerKey);
    return others[others.length - 1] || null;
  });
  // Sprites on stage: first-person/narrator → only the last other character (rotates as the
  // exchange moves); a third-person character POV → everyone present but the viewpoint holder.
  let stageKeys = $derived(firstPerson
    ? (lastOtherKey ? [lastOtherKey] : [])
    : pgPresent.filter((k) => k !== viewerKey));
  // The lit sprite: the current speaker if they're on stage, else the single staged character.
  let speakingKey = $derived((lastSpeakerKey && stageKeys.includes(lastSpeakerKey))
    ? lastSpeakerKey : (stageKeys.length === 1 ? stageKeys[0] : null));
  // Per-line EMOTION: as lines reveal, each dialogue line's emotion becomes its speaker's
  // current sprite expression (latest revealed line wins). '' before the first dialogue line.
  let lineEmotions = $derived.by(() => {
    const m = {};
    for (const l of visibleLines) {
      if (l.kind === 'dialogue' && l.speaker && l.emotion) {
        const k = nameToKey[l.speaker.toLowerCase()];
        if (k) m[k] = l.emotion;
      }
    }
    return m;
  });
  let lastGuard = $state(null);
  // The world-state engine owns the `world` level; the panel also surfaces the sibling
  // levels (graph / sim / facts) so the whole State doc is visible at a glance.
  const LEVEL_LABEL = { canon: 'Canon', graph: 'Graph', draft: 'Draft', world: 'World', sim: 'Sim', facts: 'Facts', log: 'Log' };
  let levelChips = $derived(Object.entries(stateLevels || {})
    .filter(([, v]) => (v?.size || 0) > 0)
    .map(([k, v]) => ({ key: k, label: LEVEL_LABEL[k] || k, size: v.size })));
  async function loadState() {
    const r = await get(`/stories/${storyKey}/state?sid=${playSid}`);
    worldState = r?.state || null;
    stateLevels = r?.levels || null;
    stateRev = r?.revision || 0;
  }
  async function resetState() {
    if (!confirm('Reset the world state for this playthrough? (lore and transcript are kept)')) return;
    const r = await post(`/stories/${storyKey}/state/reset`, { sid: playSid });
    worldState = r.data?.state || null;
  }
  const relText = (rels) => Object.entries(rels || {}).map(([t, v]) => `${t} ${v > 0 ? '+' : ''}${v}`).join(', ');

  let story = $state(null);
  let locs = $state({});       // id -> { name, description, background }
  let names = $state({});      // char key -> name
  let refs = $state({});       // char key -> reference image url
  let sprites = $state({});    // char key -> { emotion -> url }
  let heights = $state({});    // char key -> height_cm (for sprite scaling)
  let bust = 0;

  // Stature → sprite scale. Height can't render in a solo full-body sprite (it fills the frame),
  // so it's stored as height_cm metadata and applied HERE: a taller character's sprite is drawn
  // taller, feet bottom-aligned on the shared floor. 172cm ≈ the 96% baseline; clamped so a child
  // isn't invisible and a giant doesn't overflow. Missing height → baseline.
  const REF_CM = 172, BASE = 96;
  function spriteH(k) {
    const h = Number(heights[k]);
    if (!h) return BASE;
    return (BASE * Math.max(0.72, Math.min(1.14, h / REF_CM))).toFixed(1);
  }

  let history = $state([]);    // { role:'user'|'assistant', text }
  let scene = $state({ location: null, present: [], emotions: {}, movement: false });
  let busy = $state(false);
  let input = $state('');
  let err = $state(null);
  // The storymaster's FEVER-DREAM: when the player sleeps, the cast's latent pressures surface as a
  // single foreboding, oblique portent (not options) — shown as a dream overlay until dismissed.
  let dream = $state('');

  // ── The DAY — three scene slots (morning → evening → night). Each slot holds ONE scene,
  // OFFERED not imposed: 🎬 fetches three suggestions grounded in the arc + whereabouts; pick
  // one (or ignore them and free-play). Ending a slot is deliberate; night ends only by
  // sleeping, which fires the dream/consolidation pass and turns the day over.
  let day = $state(null);              // {n, slot} — maintained by the server
  let offers = $state([]);             // suggested scenes for the current slot
  let offersBusy = $state(false);
  const SLOTS = ['morning', 'evening', 'night'];
  const SLOT_ICON = { morning: '☀', evening: '🌆', night: '🌙' };
  async function suggestScenes(advance = false) {
    if (offersBusy) return;
    offersBusy = true; err = null;
    const r = await post(`/stories/${storyKey}/day/suggest`, { sid: playSid, advance });
    offersBusy = false;
    if (r.ok) { day = { n: r.data.day, slot: r.data.slot }; offers = r.data.options || []; }
    else err = r.data?.error || 'no scene offers';
  }
  async function pickScene(o) {
    if (busy) return;
    offers = [];
    curScene = null;
    history = [...history, { role: 'user', text: `(Scene: ${o.title})` }];
    await turn({ history, location: scene.location, scene_seed: { ...o, slot: day?.slot } });
  }
  async function sleepNow() {
    if (busy) return;
    offers = [];
    history = [...history, { role: 'user', text: '(I turn in for the night and sleep.)' }];
    await turn({ history, location: scene.location });
  }

  // Who YOU are this playthrough. A "puppet" is any character card flagged `playable`;
  // you embody it (its backstory + lorebook flow into the director's context) and drive
  // its choices. The binding is per-session, not stored on the story → the puppet ports.
  let roster = $state([]);                         // all character cards (for the picker)
  let playable = $derived(roster.filter((c) => c.playable));
  let puppet = $derived(roster.find((c) => c.key === app.activePlayerChar) || null);
  let showPuppet = $state(false);
  function pickPuppet(key) { setActivePlayerChar(key); showPuppet = false; }
  // Personas this story SUGGESTS (story.default_personas) float to the top of the picker.
  let suggestedKeys = $derived(story?.default_personas || []);
  let suggested = $derived(playable.filter((c) => suggestedKeys.includes(c.key)));
  let others = $derived(playable.filter((c) => !suggestedKeys.includes(c.key)));

  // Places navigation — locations with character-anchored scenes (the orbits). You can hop
  // to any spot; the director narrates arrival and brings the anchor on-stage. The current
  // scene (UI-tracked) drives the background (scene → location → flat backdrop).
  let placeLocs = $derived((story?.locations || []).filter((l) => (l.scenes || []).length));
  // The embodied puppet's OWN portable home scenes — added to the navigator as "Your home".
  let homeScenes = $derived(puppet?.home_scenes || []);
  let curScene = $state(null);              // scene id you're currently standing in
  let showPlaces = $state(false);
  let activeScene = $derived(
    [...placeLocs.flatMap((l) => (l.scenes || []).map((s) => ({ ...s, _place: l }))), ...homeScenes]
      .find((s) => s.id === curScene) || null
  );

  async function loadAssets() {
    const sess = await get(`/stories/${storyKey}/session`);
    if (Array.isArray(sess?.lorebooks)) lorebooks = sess.lorebooks;
    await loadState();
    story = await get(`/stories/${storyKey}`);
    for (const l of story.locations) locs[l.id] = { name: l.name, description: l.description, background: l.background };
    scene.location = worldState?.location || story.start || story.locations[0]?.id || null;
    const all = await get(`/stories/${storyKey}/cast`);
    roster = all;
    for (const c of all) {
      names[c.key] = c.name;
      if (c.reference) refs[c.key] = c.reference;
      if (c.fields?.height_cm) heights[c.key] = c.fields.height_cm;
    }
    primaryKey = story.cast.find((m) => m.primary)?.character || story.cast[0]?.character || '';
    for (const m of story.cast) {
      try {
        const p = await get(`/stories/${storyKey}/cast/${m.character}/portraits`);
        const o = p.outfits?.[0];
        if (o?.expressions) sprites[m.character] = o.expressions;
      } catch { /* no sprites yet */ }
    }
    await loadPrologue();   // the novel opens on a prologue you read through; play continues from it
  }
  loadAssets();

  async function loadPrologue() {
    prologueBusy = true;
    const r = await post(`/stories/${storyKey}/prologue`, { sid: playSid });   // model = the narrator role
    prologueBusy = false;
    const secs = r.ok ? (r.data?.sections || []) : [];
    if (secs.length) {
      pages = secs.map((s) => ({ kind: 'prologue', title: s.title, text: s.text }));
      // A compiled loop starts from its explicit opening state.  A prewritten
      // prologue is readable material, not context that every person in loop
      // one magically remembers.
      history = story?.fields?.status === 'active' ? [] : secs.map((s) => ({ role: 'assistant', text: s.text }));
      cursor = 0;
    } else {
      await turn({ history: [], location: scene.location });   // no prologue → live opening turn
    }
  }

  async function turn(payload) {
    busy = true; err = null;
    // Who you are this turn. Prefer an EMBODIED playable character (send its key → the
    // director pulls its backstory + per-character lorebook server-side). Otherwise fall
    // back to the legacy thin persona so old playthroughs keep working.
    if (puppet) {
      payload = { ...payload, player: { character: puppet.key, name: puppet.name } };
    } else {
      const persona = app.personas.find((p) => p.id === app.activePersona);
      if (persona) payload = { ...payload, player: { name: persona.name, description: persona.description || '' } };
    }
    if (lorebooks.length) payload = { ...payload, lorebooks };
    const r = await post(`/stories/${storyKey}/play`, payload);
    busy = false;
    if (!r.ok) { err = r.data?.error || 'director error'; return; }
    const d = r.data;
    // A real loop reset must cut the client transcript too.  Otherwise the
    // next /play request reintroduces the entire dead loop through `history`.
    history = d.reset_history ? [] : [...history, { role: 'assistant', text: d.reply }];
    scene = { location: d.location, present: d.present || [], emotions: d.emotions || {}, movement: !!d.movement };
    pages = [...pages, { kind: 'turn', text: d.reply, lines: d.lines || [], present: d.present || [], emotions: d.emotions || {}, pov: d.pov || '' }];
    cursor = pages.length - 1;       // a new turn jumps you to the live edge
    lineCursor = d.lines?.length ? 0 : Infinity;   // a lined turn starts its VN reveal
    if (d.beat) lastBeat = d.beat;   // the consequence reasoning behind this turn (🧠 Logic panel)
    if (d.state?.state) worldState = d.state.state;
    if (typeof d.state?.revision === 'number') stateRev = d.state.revision;
    lastGuard = d.guard || null;
    if (d.consolidation?.dream) dream = d.consolidation.dream;   // the player slept → a fever-dream rises
    if (d.day) day = d.day;                                      // the slot rhythm follows the server
    if (d.loop_reset) { offers = []; curScene = null; dream = ''; }
    if (showState) loadState();   // refresh sibling-level counts (facts/sim grow as you play)
  }
  async function send() {
    const t = input.trim(); if (!t || busy) return;
    input = '';
    history = [...history, { role: 'user', text: t }];
    await turn({ history, location: scene.location });
  }
  async function moveTo(locId) {
    if (busy) return;
    curScene = null;                          // back to a flat location → drop the scene bg
    history = [...history, { role: 'user', text: `(Go to ${locs[locId]?.name || locId}.)` }];
    await turn({ history, location: scene.location, choice: locId });
  }
  async function moveToScene(place, s) {
    if (busy) return;
    curScene = s.id;
    showPlaces = false;
    const where = (s.name || s.id) + (place ? ` in ${place.name}` : '');
    history = [...history, { role: 'user', text: `(Go to ${where}.)` }];
    await turn({ history, location: scene.location, choice: s.id });
  }
  // Sprite for a character: the current REVEALED line's emotion wins (VN expression changes
  // per line); an unknown per-line key falls back to the turn emotion, then the ref image.
  const spriteOf = (k) => {
    const le = lineEmotions[k];
    const emo = (le && sprites[k]?.[le]) ? le : pgEmotions[k];
    return sprites[k]?.[emo] || refs[k] || null;
  };
  let bg = $derived.by(() => {
    const sb = activeScene?.background || activeScene?._place?.background;
    if (sb) return `${sb}?b=${bust}`;
    return scene.location && locs[scene.location]?.background ? `${locs[scene.location].background}?b=${bust}` : null;
  });
  let moveOptions = $derived(story ? story.locations.filter((l) => l.id !== scene.location) : []);
  let lastReply = $derived([...history].reverse().find((m) => m.role === 'assistant')?.text || '');
</script>

<div class="player">
  {#if dream}
    <!-- The fever-dream: a foreboding portent that rises when the player sleeps. Tap to wake. -->
    <div class="dreamveil" role="button" tabindex="0" onclick={() => (dream = '')}
         onkeydown={(e) => (e.key === 'Enter' || e.key === 'Escape') && (dream = '')}>
      <div class="dreamlabel">a dream</div>
      <p class="dreamtext">{dream}</p>
      <div class="dreamwake">tap to wake</div>
    </div>
  {/if}
  <div class="topbar">
    <button class="ghost sm" onclick={exitPlay}>← Exit</button>
    <span class="title">{story?.name || 'Story'}</span>

    <!-- Playing as: which playable card you embody this session (the "you" puppet). -->
    <div class="puppet-wrap">
      <button class="puppet-btn" class:embodied={!!puppet} onclick={() => (showPuppet = !showPuppet)}
        title="Who you're playing as">
        {#if puppet?.reference}
          <img class="pp-av" src={puppet.reference} alt={puppet.name} />
        {:else}
          <span class="pp-av ph">🎭</span>
        {/if}
        <span class="pp-name">{puppet ? puppet.name : 'Pick a character'}</span>
        <span class="pp-caret">▾</span>
      </button>
      {#if showPuppet}
        <div class="puppet-menu">
          {#snippet pmItem(c)}
            <button class="pm-item" class:on={c.key === app.activePlayerChar} onclick={() => pickPuppet(c.key)}>
              {#if c.reference}<img class="pm-av" src={c.reference} alt={c.name} />{:else}<span class="pm-av ph">🎭</span>{/if}
              <span class="pm-nm">{c.name}</span>
              {#if c.key === app.activePlayerChar}<span class="pm-dot">●</span>{/if}
            </button>
          {/snippet}
          {#if playable.length}
            {#if suggested.length}
              <div class="pm-head">Suggested for this story</div>
              {#each suggested as c (c.key)}{@render pmItem(c)}{/each}
              {#if others.length}<div class="pm-head">Other personas</div>{/if}
            {:else}
              <div class="pm-head">Play as…</div>
            {/if}
            {#each others as c (c.key)}{@render pmItem(c)}{/each}
          {:else}
            <div class="pm-empty">No playable characters yet. Make one in Characters ▸ Personas.</div>
          {/if}
          {#if puppet}<button class="pm-clear" onclick={() => pickPuppet('')}>Use default persona</button>{/if}
        </div>
      {/if}
    </div>

    {#if placeLocs.length || homeScenes.length}
      <button class="ghost sm" class:on={showPlaces} onclick={() => (showPlaces = !showPlaces)}
        title="Move to a place / scene">🗺 Places</button>
    {/if}
    {#if !leanStoryMode}
      <button class="ghost sm" onclick={() => openConfig('lorebooks')}
        title="Attach lorebooks to this playthrough">📚 {lorebooks.length || ''}</button>
    {/if}
    <button class="ghost sm" class:on={showState} onclick={() => (showState = !showState)}
      title="World state — the evolving model of this playthrough">🧠 State{#if lastGuard?.used_fallback} <span class="fb" title="primary model refused; used fallback">⤵</span>{/if}</button>
    {#if lastBeat}
      <button class="ghost sm" class:on={showBeat} onclick={() => (showBeat = !showBeat)}
        title="What the story reasoned would happen this turn (before it was written)">⚙︎ Logic</button>
    {/if}
    <button class="ghost sm" onclick={() => (showMs = true)}
      title="Read and edit this playthrough as literature — scenes, beats, paragraphs">📖 Manuscript</button>
    {#if !leanStoryMode}
      <button class="gear" onclick={() => openConfig('models')} title="Models, configs & connections">⚙</button>
    {/if}
    <span class="loc">{scene.location ? (locs[scene.location]?.name || scene.location) : ''}</span>
  </div>

  <div class="stage" style={bg ? `background-image:url('${bg}')` : ''} class:nobg={!bg}>
    {#if showPlaces}
      <div class="placespanel">
        <div class="pphead"><b>🗺 Places</b><button class="x" onclick={() => (showPlaces = false)}>✕</button></div>
        {#if homeScenes.length}
          <div class="ppplace">
            <div class="ppname">🏠 Your home <span class="ppyou">{puppet?.name || ''}</span></div>
            <div class="ppscenes">
              {#each homeScenes as s (s.id)}
                <button class="ppscene" class:on={s.id === curScene} disabled={busy} onclick={() => moveToScene(null, s)}>
                  {#if s.background}<img class="ppthumb" src={s.background} alt="" />{:else}<span class="ppthumb ph">🏠</span>{/if}
                  <span class="ppmeta"><span class="ppsname">{s.name || s.id}</span></span>
                </button>
              {/each}
            </div>
          </div>
        {/if}
        {#each placeLocs as p (p.id)}
          <div class="ppplace">
            <div class="ppname">{p.name}</div>
            {#if p.description}<div class="ppdesc">{p.description}</div>{/if}
            <div class="ppscenes">
              {#each p.scenes || [] as s (s.id)}
                <button class="ppscene" class:on={s.id === curScene} disabled={busy} onclick={() => moveToScene(p, s)}>
                  {#if s.background}<img class="ppthumb" src={s.background} alt="" />{:else}<span class="ppthumb ph">{s.role === 'persona_home' ? '🏠' : '○'}</span>{/if}
                  <span class="ppmeta">
                    <span class="ppsname">{s.name || s.id}</span>
                    {#if s.character}<span class="ppanchor">{names[s.character] || s.character}</span>{/if}
                  </span>
                </button>
              {:else}
                <span class="ppempty">No scenes here.</span>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    {/if}
    {#if showState}
      <div class="statepanel">
        <div class="sphead"><b>State</b>{#if stateRev}<span class="sprev" title="State doc revision">r{stateRev}</span>{/if}<button class="reset" onclick={resetState} title="Reset state">↺</button><button class="x" onclick={() => (showState = false)}>✕</button></div>
        {#if levelChips.length}
          <div class="splevels" title="Levels of this thread's State doc">
            {#each levelChips as c (c.key)}<span class="splevel" class:world={c.key === 'world'}>{c.label} {c.size}</span>{/each}
          </div>
        {/if}
        {#if !worldState || (!Object.keys(worldState.entities || {}).length && !Object.keys(worldState.flags || {}).length && !(worldState.inventory || []).length && !(worldState.log || []).length)}
          <div class="spempty">No state yet — it builds as you play.</div>
        {:else}
          {#if Object.keys(worldState.entities || {}).length}
            <div class="spsec">Characters</div>
            {#each Object.entries(worldState.entities) as [nm, e]}
              <div class="spent">
                <span class="spname">{nm}</span>
                <span class="spmeta">{[e.mood, e.location, relText(e.relationships)].filter(Boolean).join(' · ')}</span>
              </div>
            {/each}
          {/if}
          {#if (worldState.inventory || []).length}
            <div class="spsec">Inventory</div><div class="spline">{worldState.inventory.join(', ')}</div>
          {/if}
          {#if Object.keys(worldState.flags || {}).length}
            <div class="spsec">Flags</div>
            <div class="spline">{Object.entries(worldState.flags).map(([k, v]) => `${k}=${v}`).join('  ·  ')}</div>
          {/if}
          {#if (worldState.log || []).length}
            <div class="spsec">Recently</div>
            {#each worldState.log.slice(-5) as l}<div class="splog">• {l}</div>{/each}
          {/if}
        {/if}
      </div>
    {/if}
    {#if showBeat}
      <div class="beatpanel">
        <div class="sphead"><b>⚙︎ Logic — what the story worked out this turn</b><button class="x" onclick={() => (showBeat = false)}>✕</button></div>
        <div class="beattext">{lastBeat}</div>
      </div>
    {/if}
    {#if showMs}
      <Manuscript {storyKey} sid={playSid} onclose={() => (showMs = false)} />
    {/if}
    {#if day}
      <div class="story-time" aria-label={`Day ${day.n}, ${day.slot}`}>
        <span class="dlab">Day {day.n}</span>
        {#each SLOTS as s (s)}
          <span class="slot" class:on={day.slot === s}>{SLOT_ICON[s]} {s}</span>
        {/each}
      </div>
    {/if}
    <div class="cast">
      {#each stageKeys as k (k)}
        {#if spriteOf(k)}
          <div class="sprite" class:speaking={speakingKey === k}
               class:dimmed={speakingKey && speakingKey !== k}
               style="height:{spriteH(k)}%"><img src={spriteOf(k)} alt={names[k] || k} /></div>
        {/if}
      {/each}
    </div>

    <div class="dialogue">
      {#if err}<div class="err">⚠ {err}</div>{/if}
      {#if curPage?.kind === 'prologue'}<div class="ptag">Prologue · {curPage.title}</div>{/if}
      {#if pgLines.length}
        <!-- VN presentation: lines reveal one by one at the live edge; click to advance.
             The current speaker's sprite is focused above. -->
        <div class="vnlines" role="button" tabindex="0" onclick={advanceLine}
             onkeydown={(e) => e.key === 'Enter' && advanceLine()}>
          {#each visibleLines as ln, i (i)}
            {#if ln.kind === 'dialogue'}
              <div class="vnline dlg">{#if ln.speaker}<span class="vnwho">{ln.speaker}</span>{/if}<span class="vntext">{ln.text}</span></div>
            {:else if ln.kind === 'thought'}
              <div class="vnline tht"><span class="vntext">{ln.text}</span></div>
            {:else}
              <div class="vnline"><span class="vntext">{ln.text}</span></div>
            {/if}
          {/each}
          {#if linesLeft > 0}<div class="vnmore">▼ <span class="vnleft">{linesLeft} more</span></div>
          {:else if busy && atEnd}<span class="loading"> …</span>{/if}
        </div>
      {:else}
        <p class="narr">
          {#if prologueBusy}<span class="loading">Writing the prologue…</span>
          {:else if busy && atEnd}{@html formatChat(curPage?.text || '')}<span class="loading"> …</span>
          {:else}{@html formatChat(curPage?.text || '')}{/if}
        </p>
      {/if}

      {#if atEnd && !prologueBusy}
        <div class="dayrow">
          {#if !day}
            <button class="dbtn" onclick={() => suggestScenes(false)} disabled={offersBusy || busy}>{offersBusy ? '…' : '🎬 Start the day'}</button>
          {:else if day.slot === 'night'}
            <button class="dbtn" onclick={() => suggestScenes(false)} disabled={offersBusy || busy}>{offersBusy ? '…' : '🎬 Scenes'}</button>
            <button class="dbtn zz" onclick={sleepNow} disabled={busy}>😴 Sleep</button>
          {:else}
            <button class="dbtn" onclick={() => suggestScenes(false)} disabled={offersBusy || busy}>{offersBusy ? '…' : '🎬 Scenes'}</button>
            <button class="dbtn" onclick={() => suggestScenes(true)} disabled={offersBusy || busy}>→ end {day.slot}</button>
          {/if}
        </div>
        {#if offers.length}
          <div class="offers">
            {#each offers as o, i (i)}
              <button class="offer" onclick={() => pickScene(o)} disabled={busy}>
                <b>{o.title}</b>
                <span class="owhere">{locs[o.location]?.name || o.location}{o.who?.length ? ` · ${o.who.join(', ')}` : ''}</span>
                <span class="ohook">{o.hook}</span>
              </button>
            {/each}
          </div>
        {/if}
      {/if}

      {#if scene.movement && moveOptions.length && atEnd}
        <div class="choices">
          <span class="clab">Where to?</span>
          {#each moveOptions as l (l.id)}
            <button class="choice" onclick={() => moveTo(l.id)} disabled={busy}>{l.name}</button>
          {/each}
        </div>
      {/if}

      <!-- VN navigation: step through the narrative both ways -->
      <div class="navrow">
        <button class="nav" onclick={backPage} disabled={cursor <= 0} title="Back">‹ Back</button>
        <span class="pageno">{pages.length ? cursor + 1 : 0} / {pages.length}</span>
        {#if atEnd}
          <span class="edge">— now —</span>
        {:else}
          <button class="nav" onclick={nextPage} title="Next">Next ›</button>
        {/if}
      </div>

      {#if atEnd}
        <div class="inputrow">
          <input class="say" placeholder={pages.length ? 'What do you do?' : 'Say or do something…'} bind:value={input}
            onkeydown={(e) => e.key === 'Enter' && send()} disabled={busy || prologueBusy} />
          <button onclick={send} disabled={busy || prologueBusy || !input.trim()}>{busy ? '…' : 'Send'}</button>
        </div>
      {:else}
        <div class="readback" role="button" tabindex="0" onclick={() => (cursor = pages.length - 1)}
          onkeydown={(e) => e.key === 'Enter' && (cursor = pages.length - 1)}>reading back — jump to now ⤓</div>
      {/if}
    </div>
  </div>
</div>

<style>
  .player { display: flex; flex-direction: column; gap: 10px; }
  .topbar { display: flex; align-items: center; gap: 12px; }
  .topbar .title { font-weight: 700; font-size: 15px; }
  .topbar .loc { margin-left: auto; font-size: 12px; color: var(--accent); }
  .ghost.sm.on { color: var(--accent); border-color: var(--accent); }
  .gear { width: 30px; height: 30px; display: grid; place-items: center; padding: 0; font-size: 14px;
    border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); color: var(--muted); cursor: pointer; }
  .gear:hover { color: var(--accent); border-color: var(--accent); }
  .fb { color: var(--accent); }

  /* Playing-as puppet picker */
  .puppet-wrap { position: relative; }
  .puppet-btn {
    display: flex; align-items: center; gap: 7px; padding: 3px 9px 3px 4px; height: 30px;
    border: 1px solid var(--border-soft); border-radius: 999px; background: var(--elev);
    color: var(--muted); cursor: pointer; font-size: 12.5px;
  }
  .puppet-btn:hover { color: var(--text); border-color: var(--border); }
  .puppet-btn.embodied { color: var(--text); border-color: color-mix(in srgb, var(--accent) 50%, transparent); }
  .pp-av { width: 22px; height: 22px; border-radius: 50%; object-fit: cover; flex: none; }
  .pp-av.ph { display: grid; place-items: center; font-size: 12px; background: var(--elev-2); }
  .pp-name { max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pp-caret { font-size: 9px; color: var(--faint); }

  .puppet-menu {
    position: absolute; z-index: 20; top: 36px; left: 0; width: 240px; padding: 6px;
    background: rgba(14,17,24,.97); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: 0 16px 40px rgba(0,0,0,.5); display: flex; flex-direction: column; gap: 2px;
  }
  .pm-head { font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--faint); padding: 4px 8px 6px; }
  .pm-item {
    display: flex; align-items: center; gap: 9px; padding: 6px 8px; border-radius: 8px;
    background: none; border: none; color: var(--text); font-size: 13px; cursor: pointer; text-align: left;
  }
  .pm-item:hover { background: var(--elev); }
  .pm-item.on { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .pm-av { width: 28px; height: 28px; border-radius: 6px; object-fit: cover; flex: none; }
  .pm-av.ph { display: grid; place-items: center; font-size: 14px; background: var(--elev-2); }
  .pm-nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pm-dot { color: var(--accent); font-size: 9px; }
  .pm-empty { font-size: 12px; color: var(--muted); padding: 8px; line-height: 1.45; }
  .pm-clear {
    margin-top: 4px; font-size: 12px; color: var(--muted); background: none; border: none;
    border-top: 1px solid var(--border-soft); border-radius: 0; padding: 8px 8px 4px; text-align: left; cursor: pointer;
  }
  .pm-clear:hover { color: var(--text); }

  .statepanel {
    position: absolute; z-index: 5; top: 10px; right: 10px; width: 290px; max-height: calc(100% - 90px);
    overflow: auto; background: rgba(12,15,22,.92); border: 1px solid var(--border); border-radius: 12px;
    padding: 10px 12px; font-size: 12px; color: #e7ecf5; backdrop-filter: blur(4px);
  }

  /* Places navigator (left side, mirrors the state panel) */
  .placespanel {
    position: absolute; z-index: 6; top: 10px; left: 10px; width: 268px; max-height: calc(100% - 90px);
    overflow: auto; background: rgba(12,15,22,.92); border: 1px solid var(--border); border-radius: 12px;
    padding: 10px 12px; color: #e7ecf5; backdrop-filter: blur(4px);
  }
  .pphead { display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }
  .pphead b { flex: 1; font-size: 12.5px; }
  .pphead .x { width: 22px; height: 22px; padding: 0; border-radius: 6px; background: none; border: 1px solid var(--border); color: var(--muted); font-size: 11px; }
  .ppplace { margin-bottom: 10px; }
  .ppname { font-size: 12px; font-weight: 700; color: #fff; }
  .ppyou { font-weight: 400; color: var(--accent); font-size: 11px; }
  .ppdesc { font-size: 11px; color: #aab2c5; margin: 1px 0 5px; line-height: 1.4; }
  .ppscenes { display: flex; flex-direction: column; gap: 4px; }
  .ppscene {
    display: flex; align-items: center; gap: 8px; padding: 4px; border-radius: 8px; text-align: left;
    background: rgba(255,255,255,.04); border: 1px solid transparent; color: #e7ecf5; cursor: pointer;
  }
  .ppscene:hover:not(:disabled) { background: rgba(255,255,255,.09); }
  .ppscene.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .ppscene:disabled { opacity: .5; cursor: default; }
  .ppthumb { width: 40px; height: 30px; flex: none; border-radius: 5px; object-fit: cover; }
  .ppthumb.ph { display: grid; place-items: center; font-size: 13px; background: rgba(255,255,255,.06); color: #8a92b0; }
  .ppmeta { display: flex; flex-direction: column; min-width: 0; }
  .ppsname { font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ppanchor { font-size: 10.5px; color: var(--accent); }
  .ppempty { font-size: 11px; color: #8a92b0; padding: 2px 4px; }
  .sphead { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .sphead b { flex: 1; font-size: 12.5px; }
  .sphead .sprev { color: var(--faint); font-size: 10px; font-variant-numeric: tabular-nums; }
  .sphead .reset, .sphead .x { background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 13px; padding: 0 2px; }
  .sphead .reset:hover, .sphead .x:hover { color: #fff; }
  .splevels { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
  .splevel { font-size: 10px; padding: 1px 6px; border-radius: 999px; background: rgba(255,255,255,.06);
             color: var(--muted); border: 1px solid rgba(255,255,255,.08); }
  .splevel.world { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, transparent); }
  .spempty { color: var(--faint); }
  .spsec { margin-top: 8px; font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--accent); }
  .spent { display: flex; flex-direction: column; padding: 3px 0; border-bottom: 1px solid rgba(255,255,255,.06); }
  .spname { font-weight: 600; }
  .spmeta { color: var(--muted); font-size: 11px; }
  .spline { color: #cdd6e6; padding: 2px 0; }
  .splog { color: var(--muted); font-size: 11px; line-height: 1.4; padding: 1px 0; }

  /* NOVEL/VN hybrid: a PORTRAIT window — sprites stand tall up top, the prose is a short strip
     at the foot (a novel wants little text on screen at once). Also the sprite/scene testbed. */
  .stage {
    position: relative; height: 86vh; aspect-ratio: 4 / 5; max-width: 100%; margin: 0 auto;
    border-radius: 14px; overflow: hidden;
    background-size: cover; background-position: center; border: 1px solid var(--border);
    display: flex; flex-direction: column; justify-content: flex-end;
  }
  .stage.nobg { background: linear-gradient(160deg, #2a2f3e, #14171f); }
  /* Time belongs to the story surface, not the action controls. It is an overlay so the
     dialogue's height and type scale never shift as the day advances. */
  .story-time { position: absolute; top: 12px; right: 12px; z-index: 3; display: flex; align-items: center;
    gap: 2px; max-width: calc(100% - 24px); padding: 4px 6px 4px 9px; border-radius: 999px;
    background: rgba(11,13,19,.66); border: 1px solid rgba(255,255,255,.14); backdrop-filter: blur(5px);
    box-shadow: 0 3px 12px rgba(0,0,0,.2); pointer-events: none; }
  /* sprites stand full-height; the opaque text box overlays their lower body (VN style) */
  .cast { position: absolute; inset: 2% 0 0 0; display: flex; align-items: flex-end; justify-content: center; gap: 5%; pointer-events: none; }
  .sprite { height: 96%; }   /* fallback; per-character height set inline from height_cm */
  .sprite img { height: 100%; width: auto; object-fit: contain; filter: drop-shadow(0 6px 18px rgba(0,0,0,.5)); }
  /* the current line's speaker holds the stage; everyone else recedes (VN focus) */
  .sprite { transition: filter .25s, transform .25s; }
  .sprite.dimmed img { filter: brightness(.5) saturate(.75) drop-shadow(0 6px 18px rgba(0,0,0,.5)); }
  .sprite.speaking { transform: translateY(-4px); }
  .sprite.speaking img { filter: brightness(1.06) drop-shadow(0 8px 22px rgba(0,0,0,.6)); }

  /* OPAQUE text box at the foot — sized so a normal turn fits without scrolling */
  .dialogue {
    position: relative; z-index: 2; padding: 15px 18px;
    background: rgba(11,13,19,.55); backdrop-filter: blur(4px); border-top: 1px solid rgba(255,255,255,.1);
    display: flex; flex-direction: column; gap: 10px;
  }
  .narr { margin: 0; font-size: 14.5px; line-height: 1.55; color: #f4f6fb; white-space: pre-wrap; min-height: 1.4em;
          max-height: 40vh; overflow: auto;
          text-shadow: -1px -1px 1px #000, 1px -1px 1px #000, -1px 1px 1px #000, 1px 1px 1px #000; }
  .ptag { font-size: 10.5px; text-transform: uppercase; letter-spacing: 2px; color: var(--muted); }
  .loading { color: var(--muted); font-style: italic; }
  .navrow { display: flex; align-items: center; gap: 12px; }
  .nav { font-size: 12.5px; padding: 5px 12px; border-radius: 999px; background: rgba(255,255,255,.06);
         border: 1px solid var(--border); color: #e6e8ec; }
  .nav:hover:not(:disabled) { background: rgba(255,255,255,.12); }
  .nav:disabled { opacity: .35; }
  .pageno { font-size: 11.5px; color: var(--muted); min-width: 48px; text-align: center; }
  .edge { font-size: 11px; color: var(--accent); letter-spacing: 1px; }
  .readback { font-size: 12px; color: var(--accent); cursor: pointer; padding: 4px 0; opacity: .85; }
  .readback:hover { opacity: 1; }
  /* 🧠 Logic — the consequence reasoning, an overlay so it doesn't push the window taller */
  .beatpanel { position: absolute; inset: 8px 8px auto 8px; z-index: 5; max-height: 60%; overflow: auto;
    background: rgba(12,14,20,.95); border: 1px solid var(--accent); border-radius: 10px; padding: 8px 11px; }
  .beattext { font-size: 12px; line-height: 1.5; color: #cdd4e2; white-space: pre-wrap; }
  .err { font-size: 12.5px; }
  .vnlines { display: flex; flex-direction: column; gap: 9px; cursor: pointer; }
  .vnmore { font-size: 12px; color: var(--accent); animation: vnpulse 1.4s ease-in-out infinite; }
  .vnleft { color: var(--faint); font-size: 10.5px; }
  @keyframes vnpulse { 0%, 100% { opacity: .55; } 50% { opacity: 1; } }
  /* black outline on every line so text stays legible over the see-through box */
  .vnline { font-size: 14.5px; line-height: 1.65; color: #d7dcea;
            text-shadow: -1px -1px 1px #000, 1px -1px 1px #000, -1px 1px 1px #000, 1px 1px 1px #000; }
  .vnline.dlg { color: #f4f6fb; }
  .vnwho { display: inline-block; margin-right: 9px; padding: 1px 9px; border-radius: 999px;
           font-size: 11px; font-weight: 800; letter-spacing: .3px; vertical-align: 2px; text-shadow: none;
           background: rgba(124,109,255,.32); border: 1px solid rgba(124,109,255,.6); color: #fff; }
  .vnline.tht .vntext { font-style: italic; color: #c2c9dc; }
  .dayrow { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; justify-content: flex-end; }
  .dlab { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .5px; color: var(--faint); }
  .slot { font-size: 11px; padding: 2px 9px; border-radius: 999px; color: var(--faint);
          border: 1px solid transparent; }
  .slot.on { color: var(--text); border-color: var(--accent);
             background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .dbtn { font-size: 11.5px; padding: 4px 11px; border-radius: 999px; cursor: pointer;
          background: rgba(124,109,255,.14); border: 1px solid rgba(124,109,255,.35); color: var(--text); }
  .dbtn:hover:not(:disabled) { background: var(--accent); color: #0b0e14; }
  .dbtn.zz { border-color: rgba(255,255,255,.25); background: rgba(255,255,255,.07); }
  .offers { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; }
  .offer { display: flex; flex-direction: column; gap: 3px; text-align: left; padding: 9px 11px;
           border-radius: 10px; cursor: pointer; background: rgba(124,109,255,.10);
           border: 1px solid rgba(124,109,255,.3); color: var(--text); }
  .offer:hover:not(:disabled) { border-color: var(--accent); background: rgba(124,109,255,.2); }
  .offer b { font-size: 12.5px; }
  .owhere { font-size: 10.5px; color: var(--accent); }
  .ohook { font-size: 11.5px; color: var(--muted); line-height: 1.45; }
  .choices { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .clab { font-size: 12px; color: var(--muted); }
  .choice { font-size: 12.5px; padding: 6px 12px; border-radius: 999px; background: rgba(124,109,255,.18);
            border: 1px solid var(--accent); color: #fff; }
  .choice:hover:not(:disabled) { background: var(--accent); color: #0b0e14; }
  /* the storymaster's fever-dream — a foreboding portent on sleep, not options */
  .dreamveil { position: fixed; inset: 0; z-index: 60; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 22px; padding: 8vh 10vw; cursor: pointer;
    background: radial-gradient(120% 120% at 50% 40%, rgba(20,8,30,.86), rgba(4,2,10,.97));
    backdrop-filter: blur(7px); animation: dreamin 1.4s ease both; }
  .dreamlabel { font-size: 10px; text-transform: uppercase; letter-spacing: 5px; color: rgba(200,180,230,.5); }
  .dreamtext { max-width: 620px; text-align: center; font-family: var(--serif, Georgia, serif);
    font-style: italic; font-size: clamp(17px, 2.4vw, 24px); line-height: 1.7; color: rgba(226,216,240,.92);
    text-shadow: 0 0 26px rgba(150,110,200,.45); animation: dreamdrift 9s ease-in-out infinite alternate; }
  .dreamwake { font-size: 11px; letter-spacing: 2px; color: rgba(200,180,230,.4); }
  @keyframes dreamin { from { opacity: 0; } to { opacity: 1; } }
  @keyframes dreamdrift { from { transform: translateY(-4px); } to { transform: translateY(4px); } }
  .inputrow { display: flex; gap: 8px; }
  .say { flex: 1; padding: 9px 12px; font-size: 13.5px; border-radius: 9px; background: rgba(20,24,34,.85);
         border: 1px solid var(--border); color: var(--text); }
  .say:focus { border-color: var(--accent); outline: none; }
</style>

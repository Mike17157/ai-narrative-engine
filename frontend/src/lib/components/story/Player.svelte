<script>
  import { get, post, put } from '$lib/api.js';
  import { goto } from '$app/navigation';
  import { app } from '$lib/app.svelte.js';
  import { openConfigModal } from '$lib/configModal.svelte.js';
  import { formatChat } from '$lib/chat-format.js';

  let { storyKey } = $props();
  const exitPlay = () => goto(`/stories/${storyKey}/overview`);

  // Lorebooks attached to THIS play thread (world/RPG/etc. books from the manager).
  // Persisted under a dedicated `play-<key>` session so it survives reloads and never
  // collides with the workshop's session for the same story.
  const playSid = `play-${storyKey}`;
  let lorebooks = $state([]);
  async function setLorebooks(v) {
    lorebooks = v;
    await put(`/stories/session/${playSid}`, { lorebooks: v });
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

  async function loadAssets() {
    const sess = await get(`/stories/session/${playSid}`);
    if (Array.isArray(sess?.lorebooks)) lorebooks = sess.lorebooks;
    await loadState();
    story = await get(`/stories/${storyKey}`);
    for (const l of story.locations) locs[l.id] = { name: l.name, description: l.description, background: l.background };
    scene.location = story.start || story.locations[0]?.id || null;
    const all = await get('/characters');
    for (const c of all) {
      names[c.key] = c.name;
      if (c.reference) refs[c.key] = c.reference;
      if (c.fields?.height_cm) heights[c.key] = c.fields.height_cm;
    }
    for (const m of story.cast) {
      try {
        const p = await get(`/characters/${m.character}/portraits`);
        const o = p.outfits?.[0];
        if (o?.expressions) sprites[m.character] = o.expressions;
      } catch { /* no sprites yet */ }
    }
    await turn({ history: [], location: scene.location });   // opening
  }
  loadAssets();

  async function turn(payload) {
    busy = true; err = null;
    // Send the active persona so the director narrates to a named protagonist
    // (who *you* are) rather than a generic "Player". Falls back server-side if absent.
    const persona = app.personas.find((p) => p.id === app.activePersona);
    if (persona) payload = { ...payload, player: { name: persona.name, description: persona.description || '' } };
    if (lorebooks.length) payload = { ...payload, lorebooks };
    const r = await post(`/stories/${storyKey}/play`, payload);
    busy = false;
    if (!r.ok) { err = r.data?.error || 'director error'; return; }
    const d = r.data;
    history = [...history, { role: 'assistant', text: d.reply }];
    scene = { location: d.location, present: d.present || [], emotions: d.emotions || {}, movement: !!d.movement };
    if (d.state?.state) worldState = d.state.state;
    if (typeof d.state?.revision === 'number') stateRev = d.state.revision;
    lastGuard = d.guard || null;
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
    history = [...history, { role: 'user', text: `(Go to ${locs[locId]?.name || locId}.)` }];
    await turn({ history, location: scene.location, choice: locId });
  }
  const spriteOf = (k) => (sprites[k]?.[scene.emotions[k]] || refs[k] || null);
  let bg = $derived(scene.location && locs[scene.location]?.background ? `${locs[scene.location].background}?b=${bust}` : null);
  let moveOptions = $derived(story ? story.locations.filter((l) => l.id !== scene.location) : []);
  let lastReply = $derived([...history].reverse().find((m) => m.role === 'assistant')?.text || '');
</script>

<div class="player">
  <div class="topbar">
    <button class="ghost sm" onclick={exitPlay}>← Exit</button>
    <span class="title">{story?.name || 'Story'}</span>
    <button class="ghost sm" onclick={() => openConfig('lorebooks')}
      title="Attach lorebooks to this playthrough">📚 {lorebooks.length || ''}</button>
    <button class="ghost sm" class:on={showState} onclick={() => (showState = !showState)}
      title="World state — the evolving model of this playthrough">🧠 State{#if lastGuard?.used_fallback} <span class="fb" title="primary model refused; used fallback">⤵</span>{/if}</button>
    <button class="gear" onclick={() => openConfig('models')} title="Models, configs & connections">⚙</button>
    <span class="loc">{scene.location ? (locs[scene.location]?.name || scene.location) : ''}</span>
  </div>

  <div class="stage" style={bg ? `background-image:url('${bg}')` : ''} class:nobg={!bg}>
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
    <div class="cast">
      {#each scene.present as k (k)}
        {#if spriteOf(k)}
          <div class="sprite" style="height:{spriteH(k)}%"><img src={spriteOf(k)} alt={names[k] || k} /></div>
        {/if}
      {/each}
    </div>

    <div class="dialogue">
      {#if err}<div class="err">⚠ {err}</div>{/if}
      <p class="narr">{#if busy && !lastReply}…{:else}{@html formatChat(lastReply)}{/if}</p>

      {#if scene.movement && moveOptions.length}
        <div class="choices">
          <span class="clab">Where to?</span>
          {#each moveOptions as l (l.id)}
            <button class="choice" onclick={() => moveTo(l.id)} disabled={busy}>{l.name}</button>
          {/each}
        </div>
      {/if}

      <div class="inputrow">
        <input class="say" placeholder="Say or do something…" bind:value={input}
          onkeydown={(e) => e.key === 'Enter' && send()} disabled={busy} />
        <button onclick={send} disabled={busy || !input.trim()}>{busy ? '…' : 'Send'}</button>
      </div>
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

  .statepanel {
    position: absolute; z-index: 5; top: 10px; right: 10px; width: 290px; max-height: calc(100% - 90px);
    overflow: auto; background: rgba(12,15,22,.92); border: 1px solid var(--border); border-radius: 12px;
    padding: 10px 12px; font-size: 12px; color: #e7ecf5; backdrop-filter: blur(4px);
  }
  .sphead { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; }
  .sphead b { flex: 1; font-size: 12.5px; }
  .sphead .sprev { color: var(--faint); font-size: 10px; font-variant-numeric: tabular-nums; }
  .sphead .reset, .sphead .x { background: none; border: 0; color: var(--muted); cursor: pointer; font-size: 13px; padding: 0 2px; box-shadow: none; }
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

  .stage {
    position: relative; aspect-ratio: 16 / 9; max-height: 70vh; border-radius: 14px; overflow: hidden;
    background-size: cover; background-position: center; border: 1px solid var(--border);
    display: flex; flex-direction: column; justify-content: flex-end;
  }
  .stage.nobg { background: linear-gradient(160deg, #2a2f3e, #14171f); }
  .cast { position: absolute; inset: 0 0 28% 0; display: flex; align-items: flex-end; justify-content: center; gap: 4%; pointer-events: none; }
  .sprite { height: 96%; }   /* fallback; per-character height set inline from height_cm */
  .sprite img { height: 100%; width: auto; object-fit: contain; filter: drop-shadow(0 6px 18px rgba(0,0,0,.5)); }

  .dialogue {
    position: relative; z-index: 2; margin: 0 0 0 0; padding: 14px 16px;
    background: linear-gradient(180deg, rgba(10,12,18,0), rgba(10,12,18,.78) 22%, rgba(10,12,18,.92));
    display: flex; flex-direction: column; gap: 10px;
  }
  .narr { margin: 0; font-size: 14.5px; line-height: 1.55; color: #f0f3f9; white-space: pre-wrap; min-height: 1.5em;
          max-height: 30vh; overflow: auto; text-shadow: 0 1px 3px rgba(0,0,0,.6); }
  .err { color: var(--bad); font-size: 12.5px; }
  .choices { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  .clab { font-size: 12px; color: var(--muted); }
  .choice { font-size: 12.5px; padding: 6px 12px; border-radius: 999px; background: rgba(124,109,255,.18);
            border: 1px solid var(--accent); color: #fff; box-shadow: none; }
  .choice:hover:not(:disabled) { background: var(--accent); color: #0b0e14; filter: none; }
  .inputrow { display: flex; gap: 8px; }
  .say { flex: 1; padding: 9px 12px; font-size: 13.5px; border-radius: 9px; background: rgba(20,24,34,.85);
         border: 1px solid var(--border); color: var(--text); }
  .say:focus { border-color: var(--accent); outline: none; }
</style>

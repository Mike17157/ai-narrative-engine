<script>
  import { get, post } from '$lib/api.js';
  import { goto } from '$app/navigation';
  import { app } from '$lib/app.svelte.js';

  let { storyKey } = $props();
  const exitPlay = () => goto(`/stories/${storyKey}/overview`);

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
    const r = await post(`/stories/${storyKey}/play`, payload);
    busy = false;
    if (!r.ok) { err = r.data?.error || 'director error'; return; }
    const d = r.data;
    history = [...history, { role: 'assistant', text: d.reply }];
    scene = { location: d.location, present: d.present || [], emotions: d.emotions || {}, movement: !!d.movement };
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
    <span class="loc">{scene.location ? (locs[scene.location]?.name || scene.location) : ''}</span>
  </div>

  <div class="stage" style={bg ? `background-image:url('${bg}')` : ''} class:nobg={!bg}>
    <div class="cast">
      {#each scene.present as k (k)}
        {#if spriteOf(k)}
          <div class="sprite" style="height:{spriteH(k)}%"><img src={spriteOf(k)} alt={names[k] || k} /></div>
        {/if}
      {/each}
    </div>

    <div class="dialogue">
      {#if err}<div class="err">⚠ {err}</div>{/if}
      <p class="narr">{busy && !lastReply ? '…' : lastReply}</p>

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

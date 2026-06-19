<script>
  import { goto } from '$app/navigation';
  import Combobox from '$lib/components/Combobox.svelte';
  import ChapterCard from '$lib/components/ChapterCard.svelte';
  import SceneModal from '$lib/components/SceneModal.svelte';
  import StoryWorkshop from '$lib/components/StoryWorkshop.svelte';
  import { autosize } from '$lib/autosize.js';
  import {
    stories, genStoryboard, regenStoryboard, genScenes, genCharacters, saveStory, cancelWizard, cancelGen,
    addBeat, removeBeat, moveBeat, addLocation, removeLocation, addNpc, removeNpc
  } from '$lib/stories.svelte.js';

  // One wizard step per route; `step` selects which to render. Transitions navigate.
  let { step = 'setup', charItems = [], modelItems = [] } = $props();
  let wz = $derived(stories.wizard);

  let streamBox = $state(null);
  $effect(() => { if (wz.streaming && wz.streamText && streamBox) streamBox.scrollTop = streamBox.scrollHeight; });

  const csv = (a) => (a || []).join(', ');
  function setCsv(beat, v) { beat.characters = v.split(',').map((s) => s.trim()).filter(Boolean); }
  let themesStr = $state('');
  let themesSeed = $state(null);
  $effect(() => { if (wz.board && themesSeed !== wz.board) { themesStr = (wz.board.themes || []).join(', '); themesSeed = wz.board; } });
  function commitThemes() { if (wz.board) wz.board.themes = themesStr.split(',').map((t) => t.trim()).filter(Boolean); }

  const at = (s) => goto(`/stories/new/${s}`);

  // Workshop: shown before storyboard generation on the setup step.
  let workshopOpen = $state(false);

  function openWorkshop() { workshopOpen = true; }

  function workshopGenerate(premise) {
    // Store the premise from the workshop into the wizard, then kick off storyboard.
    if (premise) {
      wz.board = wz.board || {};
      wz.workshopPremise = premise;
    }
    workshopOpen = false;
    genStoryboard(premise);
    at('storyboard');
  }

  function workshopSkip() {
    workshopOpen = false;
    start();
  }

  function start() { genStoryboard(); at('storyboard'); }          // fire stream, show it on the next route
  async function toScenes() { await genScenes(); if (wz.locations) at('scenes'); }
  async function toChars() { await genCharacters(); if (wz.cast !== null && wz.cast !== undefined) at('characters'); }

  // SceneModal state (per-chapter regen/edit).
  let sceneModalIdx = $state(null);   // null = closed; number = open for that beat index

  function openEdit(i) { sceneModalIdx = i; }
  function openRegen(i) { sceneModalIdx = i; }
  function closeSceneModal() { sceneModalIdx = null; }

  function saveChapter(updated) {
    if (sceneModalIdx !== null && wz.board?.beats) {
      wz.board.beats[sceneModalIdx] = { ...wz.board.beats[sceneModalIdx], ...updated };
    }
    sceneModalIdx = null;
  }
</script>

<div class="wiz">
  {#if wz.error}<div class="err">⚠ {wz.error}</div>{/if}

  {#if step === 'setup'}
    {#if workshopOpen}
      <div class="panel">
        <StoryWorkshop
          character={wz.character}
          charName={wz.charName}
          onGenerate={workshopGenerate}
          onSkip={workshopSkip}
        />
        <div class="acts" style="margin-top:8px">
          <button class="ghost" onclick={() => (workshopOpen = false)}>← Back to setup</button>
        </div>
      </div>
    {:else}
      <div class="panel">
        <h3>Build a story</h3>
        <p class="sub">Storyboard a plausible plot from a character, then extract its scenes and cast — each step reviewed before the next.</p>
        <label>Source character</label>
        <Combobox items={charItems} value={wz.character} placeholder="character…"
          onpick={(v) => { wz.character = v; wz.charName = charItems.find((c) => c.value === v)?.label || wz.charName; if (!wz.name) wz.name = wz.charName; }} />
        <p class="cfghint lo">Each stage's model &amp; system prompt are configured in <a href="/settings/story-gen?section=storyboard">Settings ▸ Story pipeline</a>.</p>
        <div class="acts">
          <button class="ghost" onclick={cancelWizard}>Cancel</button>
          <button class="ghost" onclick={openWorkshop} disabled={!wz.character}>Workshop first…</button>
          <button onclick={start} disabled={!wz.character}>Storyboard →</button>
        </div>
      </div>
    {/if}

  {:else if step === 'storyboard'}
    {#if wz.streaming}
      <div class="panel">
        <div class="streamhead"><span class="pulse"></span> Writing the storyboard… <button class="ghost sm" onclick={cancelGen}>Cancel</button></div>
        <pre class="streamtext" bind:this={streamBox}>{wz.streamText || ' '}</pre>
        <p class="lo" style="margin-top:8px">Beats become editable once it finishes.</p>
      </div>
    {:else if wz.board}
      <div class="panel">
        <h3>Storyboard</h3>
        <label>Story title</label>
        <input class="fld" bind:value={wz.name} />
        <label>Logline</label>
        <input class="fld" bind:value={wz.board.logline} />
        <div class="two"><div><label>Premise</label><textarea class="fld ta" use:autosize={wz.board.premise} bind:value={wz.board.premise}></textarea></div></div>
        <div class="two">
          <div><label>Tone</label><input class="fld" bind:value={wz.board.tone} /></div>
          <div><label>Themes</label><input class="fld" bind:value={themesStr} onblur={commitThemes} /></div>
        </div>

        {#if wz.board.heart}
          <div class="heart-callout">
            <span class="heart-icon">♡</span>
            <div class="heart-body">
              <span class="heart-label">The soul of the story</span>
              <span class="heart-text">{wz.board.heart}</span>
            </div>
          </div>
        {/if}

        <div class="blkhead"><label style="margin:0">Chapters</label><button class="ghost sm" onclick={addBeat}>＋ Chapter</button></div>
        {#each wz.board.beats as beat, i (i)}
          <div class="beatwrap">
            <ChapterCard
              chapter={beat}
              index={i}
              onRegen={openRegen}
              onEdit={openEdit}
            />
            <div class="beatctl">
              <button class="x" title="up" onclick={() => moveBeat(i, -1)} disabled={i === 0}>▲</button>
              <button class="x" title="down" onclick={() => moveBeat(i, 1)} disabled={i === wz.board.beats.length - 1}>▼</button>
              <button class="x" title="remove" onclick={() => removeBeat(i)}>✕</button>
            </div>
          </div>
        {/each}
        <div class="acts">
          <button class="ghost" onclick={cancelWizard}>Cancel</button>
          <button class="ghost" onclick={regenStoryboard} disabled={wz.busy}>↻ Regenerate</button>
          <button onclick={toScenes} disabled={wz.busy}>{wz.busy ? 'Extracting…' : 'Extract scenes →'}</button>
        </div>
      </div>

      <!-- SceneModal for per-chapter regen/edit -->
      {#if sceneModalIdx !== null && wz.board?.beats}
        <SceneModal
          chapter={wz.board.beats[sceneModalIdx]}
          index={sceneModalIdx}
          board={wz.board}
          charKey={wz.character}
          onSave={saveChapter}
          onClose={closeSceneModal}
        />
      {/if}
    {/if}

  {:else if step === 'scenes' && wz.locations}
    <div class="panel">
      <h3>Scenes <span class="lo">— neutral locations; pure backgrounds</span></h3>
      <div class="blkhead"><span class="lo">{wz.locations.length} location{wz.locations.length === 1 ? '' : 's'}</span><button class="ghost sm" onclick={addLocation}>＋ Location</button></div>
      {#each wz.locations as loc, i (loc.id)}
        <div class="loc" class:start={wz.start === loc.id}>
          <div class="loctop">
            <input class="fld title" placeholder="name" bind:value={loc.name} />
            <code class="lid">{loc.id}</code>
            <label class="startsel"><input type="radio" name="startloc" value={loc.id} bind:group={wz.start} /> start</label>
            <button class="x" onclick={() => removeLocation(i)}>✕</button>
          </div>
          <input class="fld" placeholder="description (the place, objectively)" bind:value={loc.description} />
          <textarea class="fld ta" use:autosize={loc.background_prompt} placeholder="background prompt — pure environment, no characters" bind:value={loc.background_prompt}></textarea>
        </div>
      {/each}
      <div class="acts">
        <button class="ghost" onclick={() => at('storyboard')}>← Back</button>
        <button class="ghost" onclick={genScenes} disabled={wz.busy}>↻ Regenerate</button>
        <button onclick={toChars} disabled={wz.busy}>{wz.busy ? 'Extracting…' : 'Extract characters →'}</button>
      </div>
    </div>

  {:else if step === 'characters' && wz.cast}
    <div class="panel">
      <h3>Characters <span class="lo">— the whole cast; the ★ main character is saved as the primary, with the reference image</span></h3>

      <div class="blkhead"><span class="lo">{wz.cast.length} character{wz.cast.length === 1 ? '' : 's'}</span><button class="ghost sm" onclick={addNpc}>＋ Character</button></div>
      {#each wz.cast as c, i (i)}
        <div class="npc" class:lead={c.primary}>
          <div class="npctop">
            {#if c.primary}<span class="leadbadge">★ main</span>{/if}
            <input class="fld nm" placeholder="name" bind:value={c.name} />
            <input class="fld role" placeholder="role" bind:value={c.role} />
            {#if !c.primary}<button class="x" onclick={() => removeNpc(i)}>✕</button>{/if}
          </div>
          <textarea class="fld ta" use:autosize={c.base_prompt || ''} placeholder="✨ image prompt — booru tags (from the appearance pipeline)" bind:value={c.base_prompt}></textarea>
          <textarea class="fld ta" use:autosize={c.persona} placeholder="persona" bind:value={c.persona}></textarea>
        </div>
      {/each}
      {#if !wz.cast.length}<p class="lo">No characters yet.</p>{/if}
      <div class="acts">
        <button class="ghost" onclick={() => at('scenes')}>← Back</button>
        <button class="ghost" onclick={genCharacters} disabled={wz.busy || stories.saving}>↻ Regenerate</button>
        <button onclick={saveStory} disabled={wz.busy || stories.saving}>{stories.saving ? 'Saving…' : '✓ Save story'}</button>
      </div>
    </div>
  {/if}
</div>

<style>
  .wiz { max-width: 760px; }
  .panel h3 { margin: 0 0 4px; font-size: 18px; }
  .sub { margin: 0 0 14px; font-size: 12.5px; color: var(--muted); }
  label { display: block; font-size: 11px; color: var(--muted); margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .ta { line-height: 1.5; font-family: inherit; min-height: 38px; overflow: hidden; resize: none; }
  .cfghint { margin: 14px 0 0; font-size: 12px; }
  .two { display: flex; gap: 12px; } .two > div { flex: 1; min-width: 0; }
  .blkhead { display: flex; align-items: center; justify-content: space-between; margin: 16px 0 4px; }
  .err { font-size: 12.5px; color: var(--bad); background: rgba(255,122,122,.1); border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px 11px; margin-bottom: 12px; }
  .acts { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; }
  .acts .ghost:first-child { margin-right: auto; }
  /* Legacy single-beat layout (kept for reference; replaced by ChapterCard in storyboard step) */
  .beat { display: flex; gap: 10px; align-items: flex-start; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; margin-top: 8px; }
  .beatno { width: 22px; height: 22px; flex: none; border-radius: 50%; display: grid; place-items: center; font-size: 11px; font-weight: 700; background: var(--accent); color: #0b0e14; margin-top: 4px; }
  .beatbody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .chtitle { font-weight: 650; }
  /* Beat wrapper: ChapterCard + vertical control buttons side by side */
  .beatwrap { display: flex; gap: 6px; align-items: flex-start; margin-top: 8px; }
  .beatwrap :global(.cc) { flex: 1; min-width: 0; }
  .beatctl { display: flex; flex-direction: column; gap: 4px; padding-top: 4px; }
  /* Heart callout — the human truth at the top of the chapter list */
  .heart-callout {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin: 14px 0 4px;
    padding: 10px 14px;
    border-radius: 10px;
    background: rgba(109, 140, 255, .08);
    border: 1px solid rgba(109, 140, 255, .22);
  }
  .heart-icon { font-size: 18px; color: var(--accent); flex: none; margin-top: 1px; }
  .heart-body { display: flex; flex-direction: column; gap: 2px; }
  .heart-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--accent); }
  .heart-text { font-size: 13px; color: var(--text); line-height: 1.5; font-style: italic; }
  .x { width: 24px; height: 24px; flex: none; padding: 0; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); font-size: 10px; }
  .x:hover:not(:disabled) { color: var(--text); filter: none; } .x:disabled { opacity: .35; }
  .loc, .npc { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
  .loc.start { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .loctop, .npctop { display: flex; gap: 8px; align-items: center; }
  .loctop .title, .npctop .nm { flex: 1; } .npctop .role { flex: 2; }
  .lid { font-size: 11px; color: var(--faint); font-family: ui-monospace, monospace; background: var(--bg); padding: 2px 6px; border-radius: 5px; }
  .startsel { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--muted); margin: 0; text-transform: none; letter-spacing: 0; white-space: nowrap; }
  .primary { font-size: 13px; color: var(--accent); font-weight: 600; margin-top: 8px; }
  .npc.lead { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .leadbadge { flex: none; font-size: 10px; font-weight: 700; letter-spacing: .3px; text-transform: uppercase;
               color: var(--accent); background: rgba(109,140,255,.14); border-radius: 999px; padding: 2px 8px; }
  .streamhead { display: flex; align-items: center; gap: 9px; font-size: 13.5px; font-weight: 600; color: var(--text); margin-bottom: 10px; }
  .streamhead .ghost { margin-left: auto; }
  .pulse { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .4; transform: scale(.7); } }
  .streamtext { margin: 0; max-height: 50vh; overflow: auto; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, monospace; font-size: 12.5px; line-height: 1.55; color: var(--text);
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
</style>

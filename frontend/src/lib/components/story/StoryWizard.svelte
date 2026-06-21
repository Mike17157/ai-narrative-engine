<script>
  import { goto } from '$app/navigation';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import StoryWorkshop from '$lib/components/story/StoryWorkshop.svelte';
  import StoryConsole from '$lib/components/story/StoryConsole.svelte';
  import SpineDisplay from '$lib/components/story/SpineDisplay.svelte';
  import { autosize } from '$lib/autosize.js';
  import { boardToGraph, graphToBoard, graphToArcs } from '$lib/story_graph_model.js';
  import {
    stories, generateSpine, genStoryboard, regenStoryboard, genScenes, genCharacters, saveStory,
    draftFromGraph, cancelWizard, cancelGen,
    addLocation, removeLocation, addNpc, removeNpc
  } from '$lib/stories.svelte.js';

  // One wizard step per route; `step` selects which to render. Transitions navigate.
  let { step = 'setup', charItems = [], modelItems = [] } = $props();
  let wz = $derived(stories.wizard);

  let streamBox = $state(null);
  let boardGraph = $state(null);   // live graph from the storyboard-step console
  $effect(() => { if (wz.streaming && wz.streamText && streamBox) streamBox.scrollTop = streamBox.scrollHeight; });

  const csv = (a) => (a || []).join(', ');
  function setCsv(beat, v) { beat.characters = v.split(',').map((s) => s.trim()).filter(Boolean); }
  let themesStr = $state('');
  let themesSeed = $state(null);
  $effect(() => { if (wz.board && themesSeed !== wz.board) { themesStr = (wz.board.themes || []).join(', '); themesSeed = wz.board; } });
  function commitThemes() { if (wz.board) wz.board.themes = themesStr.split(',').map((t) => t.trim()).filter(Boolean); }

  const at = (s) => goto(`/stories/new/${s}`);

  // Setup hand-off. With a graph → faithfully expand it into the draft (→ storyboard).
  // Without one → seed a fresh spine the old way (→ spine step).
  async function workshopGenerate({ graph = null, premise = '' } = {}) {
    if (graph?.nodes?.length) {
      const p = draftFromGraph(graph);   // flips wz.streaming synchronously → layout lets storyboard through
      at('storyboard');
      await p;
      return;
    }
    if (premise) wz.workshopPremise = premise;
    generateSpine(premise);
    at('spine');
  }

  function start() { generateSpine(); at('spine'); }
  function skipSpine() { genStoryboard(); at('storyboard'); }   // bypass spine → straight to board
  async function toStoryboard() { genStoryboard(); at('storyboard'); }
  async function toScenes() { await genScenes(); if (wz.locations) at('scenes'); }
  async function toChars() { await genCharacters(); if (wz.cast !== null && wz.cast !== undefined) at('characters'); }

  // Storyboard step: the graph (incl. branches) is the source of truth. Apply syncs it
  // back to the flat board (for scene/character extraction) and to arcs (branch fidelity).
  function applyGraph(g) {
    if (!g) return;
    wz.board = graphToBoard(g, wz.board);
    wz.arcs = graphToArcs(g);
    themesSeed = null;   // re-seed the themes string from the refreshed board
  }
  function continueToScenes(g) { applyGraph(g); toScenes(); }
</script>

<div class="wiz" class:wide={step === 'setup' || step === 'storyboard'}>
  {#if wz.error}<div class="err">⚠ {wz.error}</div>{/if}

  {#if step === 'setup'}
    <div class="panel setup-panel">
      <div class="setup-top">
        <div class="setup-char">
          <label>Character</label>
          <Combobox items={charItems} value={wz.character} placeholder="Choose a character to begin…"
            onpick={(v) => { wz.character = v; wz.charName = charItems.find((c) => c.value === v)?.label || wz.charName; if (!wz.name) wz.name = wz.charName; }} />
        </div>
        <div class="setup-acts">
          <button class="ghost" onclick={cancelWizard}>Cancel</button>
          {#if wz.character}
            <button class="ghost sm" onclick={start} title="Skip the conversation and generate directly">Skip →</button>
          {/if}
        </div>
      </div>

      {#if wz.character}
        <StoryWorkshop
          character={wz.character}
          charName={wz.charName}
          sessionId={wz.sessionId || ''}
          onGenerate={workshopGenerate}
        />
      {:else}
        <div class="setup-placeholder">
          <p>Select a character above to begin the literary consultation.</p>
          <p class="lo">The consultant reads the character and opens with a developmental read — wound, misbelief, the shape of change — then builds the arc/spine with you, live, in the graph on the right.</p>
        </div>
      {/if}
    </div>

  {:else if step === 'spine'}
    {#if wz.streaming}
      <div class="panel">
        <div class="streamhead"><span class="pulse"></span> Mapping the emotional spine… <button class="ghost sm" onclick={cancelGen}>Cancel</button></div>
        <pre class="streamtext" bind:this={streamBox}>{wz.streamText || ' '}</pre>
        <p class="lo" style="margin-top:8px">Reading the psychology behind the persona.</p>
      </div>
    {:else if wz.spine}
      <div class="panel">
        <h3>Emotional Spine</h3>
        <p class="sub">The psychological skeleton every story chapter will hang from. Edit freely — the storyboard will be shaped to force these inflections.</p>
        <SpineDisplay spine={wz.spine} />

        <!-- Editable wound / lie / truth -->
        <div class="spine-edit">
          <label>Wound</label>
          <textarea class="fld ta" use:autosize={wz.spine.wound} bind:value={wz.spine.wound} placeholder="The unhealed hurt that drives all their behaviour…"></textarea>
          <label>Lie (false belief)</label>
          <textarea class="fld ta" use:autosize={wz.spine.lie} bind:value={wz.spine.lie} placeholder="The false story they tell themselves because of it…"></textarea>
          <label>Truth (must accept)</label>
          <textarea class="fld ta" use:autosize={wz.spine.truth} bind:value={wz.spine.truth} placeholder="What they must finally face to grow…"></textarea>
        </div>

        <div class="acts">
          <button class="ghost" onclick={cancelWizard}>Cancel</button>
          <button class="ghost" onclick={() => { generateSpine(); }} disabled={wz.busy}>↻ Regenerate spine</button>
          <button onclick={toStoryboard} disabled={wz.busy}>Storyboard →</button>
        </div>
      </div>
    {:else}
      <!-- No spine yet (e.g. resumed old draft or skipped) — show generator -->
      <div class="panel">
        <h3>Emotional Spine</h3>
        <p class="sub">Before writing chapters, map the psychology: wound → lie → truth. This becomes the skeleton every chapter hangs from.</p>
        <div class="acts" style="margin-top:8px">
          <button class="ghost" onclick={cancelWizard}>Cancel</button>
          <button class="ghost" onclick={skipSpine} disabled={wz.busy}>Skip — go straight to storyboard</button>
          <button onclick={() => generateSpine()} disabled={wz.busy || !wz.character}>{wz.busy ? 'Generating…' : 'Generate spine →'}</button>
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
      <div class="panel setup-panel">
        <div class="sb-meta">
          <div class="sb-title"><label>Story title</label><input class="fld" bind:value={wz.name} /></div>
          <div><label>Tone</label><input class="fld" bind:value={wz.board.tone} /></div>
          <div><label>Themes</label><input class="fld" bind:value={themesStr} onblur={commitThemes} /></div>
        </div>

        <StoryConsole
          character={wz.character}
          charName={wz.charName}
          sessionId={wz.sessionId ? `${wz.sessionId}-board` : ''}
          title="Storyboard"
          subtitle="Shape the beats, branches & decisions — then continue"
          initialGraph={boardToGraph(wz.board, wz.spine)}
          autostart={false}
          onGraphChange={(g) => (boardGraph = g)}
        >
          {#snippet actions({ graph, busy })}
            <button class="ghost" onclick={cancelWizard}>Cancel</button>
            <button class="ghost" onclick={regenStoryboard} disabled={wz.busy}>↻ Regenerate</button>
            <span style="flex:1"></span>
            <button class="ghost" disabled={busy} onclick={() => applyGraph(graph)} title="Sync graph edits into the storyboard">Apply</button>
            <button disabled={busy || wz.busy} onclick={() => continueToScenes(graph)}>{wz.busy ? 'Extracting…' : 'Extract scenes →'}</button>
          {/snippet}
        </StoryConsole>
      </div>
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
  .wiz.wide { max-width: 1180px; }
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
  .sb-meta { display: flex; gap: 12px; margin-bottom: 12px; }
  .sb-meta > div { flex: 1; min-width: 0; }
  .sb-meta .sb-title { flex: 1.4; }
  .sb-meta label { margin: 0 0 6px; }
  .setup-panel { padding-bottom: 0; }
  .setup-top { display: flex; align-items: flex-end; gap: 12px; margin-bottom: 14px; }
  .setup-char { flex: 1; min-width: 0; }
  .setup-char label { margin: 0 0 6px; }
  .setup-acts { display: flex; gap: 8px; align-items: center; flex: none; padding-bottom: 1px; }
  .setup-placeholder { padding: 28px 0 20px; text-align: center; }
  .setup-placeholder p { margin: 0 0 6px; font-size: 13px; color: var(--muted); }
  .spine-edit { display: flex; flex-direction: column; gap: 2px; margin-top: 14px; }
  .streamhead { display: flex; align-items: center; gap: 9px; font-size: 13.5px; font-weight: 600; color: var(--text); margin-bottom: 10px; }
  .streamhead .ghost { margin-left: auto; }
  .pulse { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 10px var(--accent); animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .4; transform: scale(.7); } }
  .streamtext { margin: 0; max-height: 50vh; overflow: auto; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, monospace; font-size: 12.5px; line-height: 1.55; color: var(--text);
    background: var(--bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
</style>

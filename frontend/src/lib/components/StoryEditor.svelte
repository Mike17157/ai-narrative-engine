<script>
  import Combobox from '$lib/components/Combobox.svelte';
  import { autosize } from '$lib/autosize.js';
  import { post } from '$lib/api.js';
  import {
    stories, finishEdit, scheduleEditSave,
    eAddLocation, eRemoveLocation, eAddBeat, eRemoveBeat, eMoveBeat, eRemoveCast, eAddCast
  } from '$lib/stories.svelte.js';

  let { charItems = [] } = $props();
  let e = $derived(stories.editing);

  // Auto-save: deep-watch the editing clone, debounce a quiet PUT. Skip the first
  // run (the initial load) so opening the editor doesn't fire a pointless save.
  let primed = false;
  $effect(() => {
    if (!e) { primed = false; return; }
    JSON.stringify(e);                 // touch every nested field so the effect tracks it
    if (!primed) { primed = true; return; }
    scheduleEditSave();
  });
  const nameOf = (k) => charItems.find((c) => c.value === k)?.label || k;
  let addPick = $state('');
  let castAddItems = $derived(charItems.filter((c) => !e?.cast?.some((m) => m.character === c.value)));

  let themesStr = $state(''); let seeded = $state(null);
  $effect(() => { if (e && seeded !== e) { themesStr = (e.themes || []).join(', '); seeded = e; } });
  function commitThemes() { e.themes = themesStr.split(',').map((t) => t.trim()).filter(Boolean); }
  const csv = (a) => (a || []).join(', ');
  function setCsv(beat, v) { beat.characters = v.split(',').map((s) => s.trim()).filter(Boolean); }
  function doAddCast() { if (addPick) { eAddCast(addPick); addPick = ''; } }

  // Convert a prose background prompt → Danbooru scenery tags in place.
  let tagging = $state({});
  async function tagifyBg(loc) {
    const text = (loc.background_prompt || '').trim(); if (!text || tagging[loc.id]) return;
    tagging[loc.id] = true;
    const r = await post('/tagify', { text, kind: 'scene' });
    tagging[loc.id] = false;
    if (r.ok && r.data?.tags) loc.background_prompt = r.data.tags;
  }
</script>

{#if e}
<div class="ed">
  <div class="edhead">
    <h3>Edit story</h3>
    <div class="acts">
      <span class="savest" class:err={stories.msg?.err}>{stories.saving ? 'Saving…' : (stories.msg?.err ? '⚠ not saved' : '✓ Auto-saves')}</span>
      <button onclick={finishEdit}>← Done</button>
    </div>
  </div>
  {#if stories.msg?.err}<div class="err">⚠ {stories.msg.text}</div>{/if}

  <label>Title</label><input class="fld" bind:value={e.name} />
  <label>Logline</label><input class="fld" bind:value={e.storyboard.logline} />
  <label>Premise</label><textarea class="fld ta" use:autosize={e.premise} bind:value={e.premise}></textarea>
  <div class="two">
    <div><label>Tone</label><input class="fld" bind:value={e.tone} /></div>
    <div><label>Themes</label><input class="fld" bind:value={themesStr} onblur={commitThemes} /></div>
  </div>

  <div class="blkhead"><label style="margin:0">Cast</label></div>
  {#each e.cast as m, i (m.character)}
    <div class="row">
      <span class="cn">{m.primary ? '★ ' : ''}{nameOf(m.character)}</span>
      {#if !m.primary}<button class="x" onclick={() => eRemoveCast(i)}>✕</button>{/if}
    </div>
  {/each}
  <div class="addrow">
    <Combobox items={castAddItems} bind:value={addPick} placeholder="add character…" />
    <button class="ghost sm" onclick={doAddCast} disabled={!addPick}>＋ Add</button>
  </div>

  <div class="blkhead"><label style="margin:0">Locations</label><button class="ghost sm" onclick={eAddLocation}>＋</button></div>
  {#each e.locations as loc, i (loc.id)}
    <div class="box" class:start={e.start === loc.id}>
      <div class="boxtop">
        <input class="fld title" bind:value={loc.name} />
        <code class="lid">{loc.id}</code>
        <label class="startsel"><input type="radio" name="estart" value={loc.id} bind:group={e.start} /> start</label>
        <button class="x" onclick={() => eRemoveLocation(i)}>✕</button>
      </div>
      <input class="fld" placeholder="description" bind:value={loc.description} />
      <div class="bgrow">
        <span class="bglab">background prompt — pure environment, Danbooru tags</span>
        <button class="ghost xs" onclick={() => tagifyBg(loc)} disabled={tagging[loc.id]} title="convert prose → tags">{tagging[loc.id] ? '…' : '⇥ tagify'}</button>
      </div>
      <textarea class="fld ta" use:autosize={loc.background_prompt} placeholder="background prompt — pure environment" bind:value={loc.background_prompt}></textarea>
    </div>
  {/each}

  <div class="blkhead"><label style="margin:0">Chapters</label><button class="ghost sm" onclick={eAddBeat}>＋</button></div>
  {#each e.storyboard.beats as beat, i (i)}
    <div class="beat">
      <div class="beatno">{i + 1}</div>
      <div class="beatbody">
        <input class="fld chtitle" placeholder="chapter title" bind:value={beat.title} />
        <textarea class="fld ta" use:autosize={beat.summary} placeholder="what this chapter accomplishes" bind:value={beat.summary}></textarea>
        <div class="two">
          <input class="fld" placeholder="location" bind:value={beat.location} />
          <input class="fld" placeholder="characters (comma)" value={csv(beat.characters)} onblur={(ev) => setCsv(beat, ev.target.value)} />
        </div>
      </div>
      <div class="beatctl">
        <button class="x" onclick={() => eMoveBeat(i, -1)} disabled={i === 0}>▲</button>
        <button class="x" onclick={() => eMoveBeat(i, 1)} disabled={i === e.storyboard.beats.length - 1}>▼</button>
        <button class="x" onclick={() => eRemoveBeat(i)}>✕</button>
      </div>
    </div>
  {/each}

  <div class="acts foot">
    <span class="savest" class:err={stories.msg?.err}>{stories.saving ? 'Saving…' : (stories.msg?.err ? '⚠ not saved' : '✓ Auto-saves as you edit')}</span>
    <button onclick={finishEdit}>← Done</button>
  </div>
</div>
{/if}

<style>
  .ed { max-width: 760px; }
  .edhead { display: flex; align-items: center; justify-content: space-between; }
  .edhead h3 { margin: 0; } .acts { display: flex; gap: 10px; align-items: center; } .foot { justify-content: flex-end; margin-top: 16px; }
  .savest { font-size: 12px; color: var(--muted); } .savest.err { color: var(--bad); }
  .err { font-size: 12.5px; color: var(--bad); background: rgba(255,122,122,.1); border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px 11px; margin: 10px 0; }
  label { display: block; font-size: 11px; color: var(--muted); margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .ta { line-height: 1.5; font-family: inherit; min-height: 38px; overflow: hidden; resize: none; }
  .two { display: flex; gap: 12px; } .two > div { flex: 1; min-width: 0; }
  .blkhead { display: flex; align-items: center; justify-content: space-between; margin: 16px 0 4px; }
  .row { display: flex; align-items: center; gap: 8px; padding: 6px 0; }
  .cn { font-size: 13.5px; color: var(--text); flex: 1; }
  .addrow { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
  .addrow :global(.cb) { flex: 1; }
  .box { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
  .box.start { border-color: var(--accent); }
  .boxtop { display: flex; gap: 8px; align-items: center; } .boxtop .title { flex: 1; }
  .lid { font-size: 11px; color: var(--faint); font-family: ui-monospace, monospace; background: var(--bg); padding: 2px 6px; border-radius: 5px; }
  .startsel { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--muted); margin: 0; text-transform: none; letter-spacing: 0; white-space: nowrap; }
  .beat { display: flex; gap: 10px; align-items: flex-start; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px; margin-top: 8px; }
  .beatno { width: 22px; height: 22px; flex: none; border-radius: 50%; display: grid; place-items: center; font-size: 11px; font-weight: 700; background: var(--accent); color: #0b0e14; margin-top: 4px; }
  .beatbody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .chtitle { font-weight: 650; }
  .beatctl { display: flex; flex-direction: column; gap: 4px; }
  .x { width: 24px; height: 24px; flex: none; padding: 0; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); font-size: 10px; }
  .x:hover:not(:disabled) { color: var(--text); filter: none; } .x:disabled { opacity: .35; }
  .bgrow { display: flex; align-items: center; gap: 8px; }
  .bglab { font-size: 10.5px; color: var(--faint); text-transform: uppercase; letter-spacing: .3px; }
  .xs { font-size: 11.5px; padding: 1px 7px; border-radius: 6px; margin-left: auto; box-shadow: none; }
</style>

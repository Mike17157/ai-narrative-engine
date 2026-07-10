<script>
  // The Overview = the story's frame, kept HIGH-LEVEL. The PREMISE is the anchor every story has;
  // a world PRINCIPLE (a defining law/trait) and a CENTRAL QUESTION (a tension the story turns on)
  // are OPTIONAL — plenty of stories are cozy, slice-of-life, or open-ended and need neither. All
  // three live under fields the overview agent OWNS (premise + premise_parts), so the section editor
  // can actually write them and flash the field as it does — see the `story:edited` listener.
  import { stories, persistCurrent } from '$lib/stories.svelte.js';
  import { put } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let st = $derived(stories.current);
  let w = $derived(st?.world || {});
  let pp = $derived(st?.premise_parts || {});

  // Principle reads premise_parts.root (what the interview/agent writes); falls back to legacy
  // world.pressure so pre-existing stories still show their principle. ponytail: edits go to root.
  let principle = $derived(pp.root ?? w.pressure ?? '');

  let wtimer = null;
  function saveWorld() { clearTimeout(wtimer); wtimer = setTimeout(() => put(`/stories/${st.key}`, { world: stories.current.world }), 500); }
  function setW(k, v) { stories.current.world = { ...(stories.current.world || {}), [k]: v }; saveWorld(); }

  let pptimer = null;
  function savePP() { clearTimeout(pptimer); pptimer = setTimeout(() => put(`/stories/${st.key}`, { premise_parts: stories.current.premise_parts }), 500); }
  function setPP(k, v) { stories.current.premise_parts = { ...(stories.current.premise_parts || {}), [k]: v }; savePP(); }

  let stimer = null;
  function saveStory() { clearTimeout(stimer); stimer = setTimeout(persistCurrent, 500); }

  // The agent "shows itself editing": when the section editor applies an op it dispatches
  // `story:edited` with the changed paths; we flash the matching field(s) and scroll the first
  // into view. Ops arrive atomically (structured), so this is a highlight, not a keystroke stream.
  let flashed = $state({});
  const FIELDS = ['premise', 'premise_parts/root', 'premise_parts/question'];
  const relate = (a, b) => a === b || a.startsWith(b + '/') || b.startsWith(a + '/');
  function onEdited(e) {
    const paths = e.detail?.paths || [];
    const hit = FIELDS.filter((f) => paths.some((p) => relate(f, p)));
    if (!hit.length) return;
    for (const f of hit) flashed[f] = true;
    flashed = { ...flashed };
    requestAnimationFrame(() => document.querySelector(`[data-field="${hit[0]}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
    setTimeout(() => { for (const f of hit) delete flashed[f]; flashed = { ...flashed }; }, 1400);
  }
  $effect(() => { window.addEventListener('story:edited', onEdited); return () => window.removeEventListener('story:edited', onEdited); });
</script>

<div class="doc">
  {#if st?.storyboard}
    <input class="ip logline" bind:value={st.storyboard.logline} oninput={saveStory} placeholder="One-line logline…" />
  {/if}

  <div class="setting">
    <input class="ip meta" value={w.place || ''} oninput={(e) => setW('place', e.target.value)} placeholder="place / setting" />
    <input class="ip meta sm" value={w.genre || ''} oninput={(e) => setW('genre', e.target.value)} placeholder="genre" />
    <input class="ip meta sm" value={w.tone || ''} oninput={(e) => setW('tone', e.target.value)} placeholder="tone" />
  </div>

  <section class="premise">
    <div class="klabel">Premise <span class="knote">what this story is about</span></div>
    <textarea class="ip prem-in" data-field="premise" class:flash={flashed['premise']} use:autosize={st?.premise}
              bind:value={st.premise} oninput={saveStory} placeholder="what this story is about"></textarea>
  </section>

  <section class="opt">
    <div class="klabel">Principle <span class="knote">optional — a defining law or trait of the world, if it has one</span></div>
    <textarea class="ip opt-in" data-field="premise_parts/root" class:flash={flashed['premise_parts/root']} use:autosize={principle}
              value={principle} oninput={(e) => setPP('root', e.target.value)}
              placeholder="e.g. reality runs on a life-energy that can be spent and stolen — leave blank if the story doesn't turn on one"></textarea>
  </section>

  <section class="opt">
    <div class="klabel">Central question <span class="knote">optional — a tension the story turns on, if any</span></div>
    <textarea class="ip opt-in" data-field="premise_parts/question" class:flash={flashed['premise_parts/question']} use:autosize={pp.question}
              value={pp.question || ''} oninput={(e) => setPP('question', e.target.value)}
              placeholder="a question the story explores — leave blank for cozy, slice-of-life, or open-ended stories"></textarea>
  </section>
</div>

<style>
  .doc { display: flex; flex-direction: column; gap: 4px; max-width: 860px; }
  .ip { width: 100%; box-sizing: border-box; background: transparent; color: var(--text); font: inherit;
        border: 1px solid transparent; border-radius: 8px; padding: 5px 8px; transition: border-color .12s, background .12s; }
  .ip:hover { border-color: var(--border-soft); }
  .ip:focus { outline: none; border-color: var(--accent); background: var(--elev); }
  textarea.ip { resize: none; line-height: 1.55; }
  .logline { font-size: 15px; font-style: italic; color: var(--muted); margin-left: -8px; }
  .setting { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 10px -8px; }
  .meta { font-size: 12.5px; color: var(--muted); }
  .meta.sm { flex: 0 0 160px; }
  .setting .meta:first-child { flex: 1; min-width: 220px; }

  .klabel { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin: 2px 0 2px 2px; }
  .knote { text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--faint); font-size: 10.5px; margin-left: 8px; }

  /* Premise leads — the anchor every story has. */
  .premise { margin: 6px 0 4px; }
  .prem-in { font-size: 15px; color: var(--text); line-height: 1.6; margin-left: -8px; }

  /* Principle + Central question are OPTIONAL and quietly styled — present when useful, ignorable otherwise. */
  .opt { margin: 12px 0 0; }
  .opt-in { font-size: 13px; color: var(--muted); line-height: 1.55; margin-left: -8px; }

  /* The agent editing a field: a brief accent pulse so the writer sees the write land. */
  .ip.flash { animation: fieldflash 1.4s ease; }
  @keyframes fieldflash {
    0% { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 20%, transparent); }
    100% { border-color: transparent; background: transparent; }
  }
</style>

<script>
  // The Overview = the WORLD DOCUMENT, kept HIGH-LEVEL on purpose. A world is designed from the top
  // down: the PRINCIPLE that governs it (its defining trait/law) and the CONFLICT that principle
  // forces — then the premise that follows. The granular buckets (forces / traditions / people /
  // fragments) were removed: enumerating them made the system fill a form instead of think. Those
  // particulars belong to the cast, locations, and scenes, generated when the story needs them.
  import { stories, persistCurrent } from '$lib/stories.svelte.js';
  import { put } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let st = $derived(stories.current);
  let w = $derived(st?.world || {});
  let pp = $derived(st?.premise_parts || {});

  let wtimer = null;
  function saveWorld() { clearTimeout(wtimer); wtimer = setTimeout(() => put(`/stories/${st.key}`, { world: stories.current.world }), 500); }
  function setW(k, v) { stories.current.world = { ...(stories.current.world || {}), [k]: v }; saveWorld(); }

  let pptimer = null;
  function savePP() { clearTimeout(pptimer); pptimer = setTimeout(() => put(`/stories/${st.key}`, { premise_parts: stories.current.premise_parts }), 500); }
  function setPP(k, v) { stories.current.premise_parts = { ...(stories.current.premise_parts || {}), [k]: v }; savePP(); }

  let stimer = null;
  function saveStory() { clearTimeout(stimer); stimer = setTimeout(persistCurrent, 500); }
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

  <section class="key">
    <div class="klabel">Principle <span class="knote">the law this world runs on — its one defining trait</span></div>
    <textarea class="ip key-in" use:autosize={w.pressure} value={w.pressure || ''}
              oninput={(e) => setW('pressure', e.target.value)}
              placeholder="e.g. reality runs on a life-energy that can be spent and stolen; the gods are dead and magic answers to whoever takes it"></textarea>
  </section>

  <section class="key conflict">
    <div class="klabel">Conflict <span class="knote">the question the principle forces — two defensible answers</span></div>
    <textarea class="ip key-in" use:autosize={pp.question} value={pp.question || ''}
              oninput={(e) => setPP('question', e.target.value)}
              placeholder="the unresolvable choice this world puts to a person"></textarea>
  </section>

  <section class="premise">
    <div class="klabel">Premise <span class="knote">what the story is — earned from the principle and its conflict</span></div>
    <textarea class="ip prem-in" use:autosize={st?.premise} bind:value={st.premise} oninput={saveStory}
              placeholder="what this story is about"></textarea>
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

  .key { margin: 8px 0 14px; padding: 12px 14px; border-radius: 12px;
         background: color-mix(in srgb, var(--accent) 7%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent); }
  .key .key-in { font-size: 15px; color: var(--text); line-height: 1.55; }
  .conflict { background: color-mix(in srgb, var(--accent) 4%, transparent); }

  .premise { margin-top: 8px; padding-top: 14px; border-top: 1px solid var(--border-soft); }
  .prem-in { font-size: 13.5px; color: var(--text); line-height: 1.6; margin-left: -8px; }
</style>

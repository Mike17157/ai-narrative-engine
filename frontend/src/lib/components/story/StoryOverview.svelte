<script>
  // The Overview = the WORLD DOCUMENT. Fields are DIRECTLY TYPE-EDITABLE in place (debounced save);
  // the TARGETED buttons (add/remove list items) are gone — structural ops go to the section editor.
  // Config (art style, memory, personas, cast) lives in the Settings pane, not here.
  import { stories, persistCurrent } from '$lib/stories.svelte.js';
  import { put } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let st = $derived(stories.current);
  let w = $derived(st?.world || {});

  let wtimer = null;
  function saveWorld() { clearTimeout(wtimer); wtimer = setTimeout(() => put(`/stories/${st.key}`, { world: stories.current.world }), 500); }
  function setW(k, v) { stories.current.world = { ...(stories.current.world || {}), [k]: v }; saveWorld(); }
  function setItem(k, i, field, v) {
    const list = [...(w[k] || [])];
    list[i] = { ...list[i], [field]: v };
    setW(k, list);
  }
  let stimer = null;
  function saveStory() { clearTimeout(stimer); stimer = setTimeout(persistCurrent, 500); }

  const LISTS = [
    { k: 'forces', title: 'Forces', icon: '⚔', sub: 'the factions', a: 'name', b: 'stance' },
    { k: 'traditions', title: 'Traditions', icon: '🕯', sub: 'the old rites', a: 'name', b: 'logic' },
    { k: 'people', title: 'People', icon: '👤', sub: 'lives the world is lived through', a: 'name', b: 'life' },
  ];
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

  <section class="ache">
    <div class="klabel">The pressure <span class="knote">the one standing force the whole world is built to feel</span></div>
    <textarea class="ip ache-in" use:autosize={w.pressure} value={w.pressure || ''}
              oninput={(e) => setW('pressure', e.target.value)}
              placeholder="the ache at the centre of the world — a condition, not an event"></textarea>
  </section>

  {#each LISTS as L (L.k)}
    <section class="wsec">
      <div class="whead"><span class="wt">{L.icon} {L.title}</span><span class="wsub">{L.sub}</span></div>
      {#if (w[L.k] || []).length}
        <div class="items">
          {#each w[L.k] as it, i (i)}
            <div class="item">
              <input class="ip iname" value={it[L.a] || ''} oninput={(e) => setItem(L.k, i, L.a, e.target.value)} placeholder={L.a} />
              <textarea class="ip ibody" use:autosize={it[L.b]} value={it[L.b] || ''} oninput={(e) => setItem(L.k, i, L.b, e.target.value)} placeholder={L.b}></textarea>
            </div>
          {/each}
        </div>
      {:else}
        <p class="none">None yet — ask the editor to add {L.title.toLowerCase()}.</p>
      {/if}
    </section>
  {/each}

  <section class="wsec">
    <div class="whead"><span class="wt">✶ Fragments</span><span class="wsub">the world shown, not specified</span></div>
    {#if (w.fragments || []).length}
      <div class="frags">
        {#each w.fragments as f, i (i)}
          <div class="frag">
            <input class="ip fkind" value={f.kind || ''} oninput={(e) => setItem('fragments', i, 'kind', e.target.value)} placeholder="kind" />
            <textarea class="ip ftext" use:autosize={f.text} value={f.text || ''} oninput={(e) => setItem('fragments', i, 'text', e.target.value)} placeholder="a concrete, voiced moment of the world…"></textarea>
          </div>
        {/each}
      </div>
    {:else}
      <p class="none">None yet — ask the editor to add fragments.</p>
    {/if}
  </section>

  <section class="premise">
    <div class="klabel">Premise <span class="knote">distilled from the world above</span></div>
    <textarea class="ip prem-in" use:autosize={st?.premise} bind:value={st.premise} oninput={saveStory}
              placeholder="what this story is about — earned from the world"></textarea>
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

  .ache { margin: 8px 0 18px; padding: 12px 14px; border-radius: 12px;
          background: color-mix(in srgb, var(--accent) 7%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 22%, transparent); }
  .ache .ache-in { font-size: 15px; color: var(--text); line-height: 1.55; }

  .wsec { margin: 0 0 18px; }
  .whead { display: flex; align-items: baseline; gap: 9px; margin-bottom: 8px; }
  .wt { font-size: 13px; font-weight: 800; color: var(--text); }
  .wsub { font-size: 11px; color: var(--faint); }
  .items, .frags { display: flex; flex-direction: column; gap: 8px; }
  .item, .frag { display: grid; grid-template-columns: 150px 1fr; gap: 8px; align-items: start;
                 padding: 8px 10px; border-radius: 10px; border: 1px solid var(--border-soft); background: var(--panel); }
  .frag { grid-template-columns: 100px 1fr; }
  .iname, .fkind { font-size: 12.5px; font-weight: 600; }
  .fkind { font-size: 11px; color: var(--accent); }
  .ibody, .ftext { font-size: 12.5px; color: var(--muted); }
  .none { font-size: 12px; color: var(--faint); margin: 0; padding: 4px 2px; }

  .premise { margin-top: 8px; padding-top: 14px; border-top: 1px solid var(--border-soft); }
  .prem-in { font-size: 13.5px; color: var(--text); line-height: 1.6; margin-left: -8px; }
</style>

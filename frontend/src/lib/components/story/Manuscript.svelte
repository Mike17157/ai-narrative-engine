<script>
  // The MANUSCRIPT pane — read the playthrough as literature. Left rail lists the scenes
  // (location + each page's beat line); the main pane shows the selected scene's prose as
  // paragraph blocks you can click and edit in place (saved to the manuscript + transcript,
  // so the narrator's own history window sees your corrected prose).
  import { get, post } from '$lib/api.js';

  let { storyKey, sid, onclose } = $props();
  const leanStoryMode = import.meta.env.VITE_LEAN_STORY === '1';

  let doc = $state(null);          // { prologue:[{title,text}], scenes:[{loc,pages:[{text,beat}]}] }
  let sel = $state(0);             // selected entry in the rail: 0 = prologue (if any), then scenes
  let editing = $state(null);      // { kind:'prologue'|'page', i, j? }
  let draft = $state('');
  let saving = $state(false);
  let baking = $state(false);
  let illustrating = $state(false);

  const hasPro = $derived((doc?.prologue || []).length > 0);
  const entries = $derived([
    ...(hasPro ? [{ kind: 'prologue', label: 'Prologue', beats: (doc.prologue || []).map((s) => s.title) }] : []),
    ...((doc?.scenes || []).map((sc, i) => ({
      kind: 'scene', i, label: sc.loc,
      beats: sc.pages.map((p) => p.beat).filter(Boolean),
    }))),
  ]);
  const cur = $derived(entries[sel]);

  async function load() {
    const r = await get(`/stories/${storyKey}/manuscript?sid=${sid}`);   // get() returns the JSON itself
    if (r?.scenes || r?.prologue) doc = r;
  }
  load();

  function beginEdit(kind, i, j, text) {
    editing = { kind, i, j };
    draft = text;
  }

  async function saveEdit() {
    if (!editing || !draft.trim()) { editing = null; return; }
    saving = true;
    const body = editing.kind === 'prologue'
      ? { prologue: editing.i, text: draft }
      : { sid, scene: editing.i, page: editing.j, text: draft };
    const r = await post(`/stories/${storyKey}/manuscript/edit`, body);
    saving = false;
    if (r.ok) {
      if (editing.kind === 'prologue') doc.prologue[editing.i].text = draft;
      else doc.scenes[editing.i].pages[editing.j].text = draft;
      editing = null;
    }
  }

  async function bake() {
    baking = true;
    const r = await post(`/stories/${storyKey}/manuscript/bake`, { sid });
    baking = false;
    if (r?.chapters?.length) {
      doc = { prologue: [], scenes: r.chapters.map((c) => ({ loc: c.title, pages: [{ text: c.text, beat: 'Baked chapter' }] })) };
      sel = 0;
    }
  }

  async function illustrate() {
    illustrating = true;
    const r = await post(`/stories/${storyKey}/manuscript/illustrate`, { sid });
    illustrating = false;
    if (r?.chapters?.length) {
      doc = { prologue: [], scenes: r.chapters.map((c) => ({ loc: c.title, pages: [{ text: c.text, beat: 'Baked chapter', image: c.image }] })) };
    }
  }
</script>

<div class="msveil" role="dialog" aria-label="Manuscript">
  <div class="mswrap">
    <div class="mshead">
      <b>📖 Manuscript</b>
      <span class="hint">click a paragraph block to edit it</span>
      <button class="soft" onclick={bake} disabled={baking}>{baking ? 'Baking…' : 'Bake prose'}</button>
      {#if !leanStoryMode}
        <button class="soft" onclick={illustrate} disabled={illustrating}>{illustrating ? 'Illustrating…' : 'Illustrate'}</button>
      {/if}
      <button class="x" onclick={onclose}>✕</button>
    </div>
    <div class="msbody">
      <nav class="msrail">
        {#each entries as e, i}
          <button class="msent" class:on={i === sel} onclick={() => { sel = i; editing = null; }}>
            <span class="msloc">{e.kind === 'prologue' ? '⁋' : `${e.i + 1}.`} {e.label}</span>
            {#each e.beats as b}<span class="msbeat">· {b}</span>{/each}
          </button>
        {/each}
        {#if !entries.length}<div class="hint pad">Nothing written yet — play a few turns.</div>{/if}
      </nav>
      <article class="mspage">
        {#if cur?.kind === 'prologue'}
          {#each doc.prologue as s, i}
            <h3>{s.title}</h3>
            {#if editing?.kind === 'prologue' && editing.i === i}
              <textarea bind:value={draft} rows={Math.min(24, draft.split('\n').length + 3)}></textarea>
              <div class="msact">
                <button onclick={saveEdit} disabled={saving}>{saving ? '…' : 'Save'}</button>
                <button class="soft" onclick={() => (editing = null)}>Cancel</button>
              </div>
            {:else}
              <div class="msblock" role="button" tabindex="0"
                onclick={() => beginEdit('prologue', i, null, s.text)}
                onkeydown={(e) => e.key === 'Enter' && beginEdit('prologue', i, null, s.text)}>
                {#each s.text.split(/\n\n+/) as para}<p>{para}</p>{/each}
              </div>
            {/if}
          {/each}
        {:else if cur}
          <h3>{cur.label}</h3>
          {#each doc.scenes[cur.i].pages as p, j}
            {#if p.image}<img class="chapterart" src={p.image} alt={`Illustration for ${cur.label}`} />{/if}
            {#if p.beat}<div class="msbeat inpage">— {p.beat}</div>{/if}
            {#if editing?.kind === 'page' && editing.i === cur.i && editing.j === j}
              <textarea bind:value={draft} rows={Math.min(24, draft.split('\n').length + 3)}></textarea>
              <div class="msact">
                <button onclick={saveEdit} disabled={saving}>{saving ? '…' : 'Save'}</button>
                <button class="soft" onclick={() => (editing = null)}>Cancel</button>
              </div>
            {:else}
              <div class="msblock" role="button" tabindex="0"
                onclick={() => beginEdit('page', cur.i, j, p.text)}
                onkeydown={(e) => e.key === 'Enter' && beginEdit('page', cur.i, j, p.text)}>
                {#each p.text.split(/\n\n+/) as para}<p>{para}</p>{/each}
              </div>
            {/if}
          {/each}
        {/if}
      </article>
    </div>
  </div>
</div>

<style>
  .msveil { position: fixed; inset: 0; z-index: 60; background: rgba(0, 0, 0, 0.55); display: flex; }
  .mswrap { margin: 3vh auto; width: min(1150px, 94vw); height: 94vh; display: flex; flex-direction: column;
    background: var(--panel, #16161c); border: 1px solid var(--border, #2a2a33); border-radius: 10px; overflow: hidden; }
  .mshead { display: flex; align-items: center; gap: 10px; padding: 10px 14px; border-bottom: 1px solid var(--border, #2a2a33); }
  .mshead .x { margin-left: auto; }
  .msbody { flex: 1; display: flex; min-height: 0; }
  .msrail { width: 270px; overflow-y: auto; border-right: 1px solid var(--border, #2a2a33); padding: 8px; }
  .msent { display: block; width: 100%; text-align: left; background: none; border: none; border-radius: 8px;
    padding: 8px 10px; cursor: pointer; }
  .msent.on { background: var(--panel-2, #1f1f28); }
  .msloc { display: block; font-weight: 600; font-size: 13px; }
  .msbeat { display: block; font-size: 11px; color: var(--muted, #8b8b98); margin-top: 2px; line-height: 1.35; }
  .msbeat.inpage { margin: 14px 0 4px; font-style: italic; }
  .mspage { flex: 1; overflow-y: auto; padding: 22px 34px; font-size: 15px; line-height: 1.75;
    font-family: Georgia, 'Times New Roman', serif; }
  .mspage h3 { font-family: inherit; margin: 18px 0 8px; }
  .msblock { border-radius: 6px; padding: 2px 8px; margin: 0 -8px; cursor: text; }
  .msblock:hover { background: rgba(255, 255, 255, 0.04); outline: 1px dashed var(--border, #2a2a33); }
  .mspage textarea { width: 100%; font: inherit; line-height: inherit; background: var(--panel-2, #1f1f28);
    color: inherit; border: 1px solid var(--accent, #7aa2f7); border-radius: 6px; padding: 8px; }
  .msact { display: flex; gap: 8px; margin: 6px 0 14px; }
  .pad { padding: 10px; }
  .chapterart { display: block; width: min(100%, 720px); max-height: 440px; object-fit: cover; margin: 0 0 18px; border-radius: 8px; }
</style>

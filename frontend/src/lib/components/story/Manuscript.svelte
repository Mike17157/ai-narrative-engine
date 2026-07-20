<script>
  // The MANUSCRIPT pane — read the playthrough as literature. Left rail lists the scenes
  // (location + each page's beat line); the main pane shows the selected scene's prose as
  // paragraph blocks you can click and edit in place (saved to the manuscript + transcript,
  // so the narrator's own history window sees your corrected prose). The right dock hosts
  // the manga production agent: it knows the story's structure and can propose the next
  // pipeline step (bake prose → plan manga → render manga), which you confirm here.
  import { get, post } from '$lib/api.js';

  let { storyKey, sid, onclose } = $props();

  let doc = $state(null);          // { prologue:[{title,text}], scenes:[{loc,pages:[{text,beat}]}] }
  let sel = $state(0);             // selected entry in the rail: 0 = prologue (if any), then scenes
  let editing = $state(null);      // { kind:'prologue'|'page', i, j? }
  let draft = $state('');
  let saving = $state(false);
  let baking = $state(false);
  let planningManga = $state(false);
  let renderingManga = $state(false);
  let mangaPanels = $state([]);

  // Manga production agent chat dock.
  let agentHistory = $state([]);       // [{ role:'user'|'assistant', text }]
  let agentSuggestions = $state([]);
  let agentAction = $state(null);      // { kind:'bake'|'manga-plan'|'manga-render', note } | null
  let agentBusy = $state(false);
  let agentInput = $state('');
  let agentBooted = false;             // bootstrap once, not on every load()

  const ACTION_LABELS = { bake: 'Bake prose', 'manga-plan': 'Plan manga', 'manga-render': 'Render manga' };
  const actionBusy = $derived(baking || planningManga || renderingManga || agentBusy);

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
    if (r?.scenes || r?.prologue) {
      doc = r;
      mangaPanels = Array.isArray(r?.manga_plan?.panels) ? r.manga_plan.panels : [];
    }
  }

  async function bootstrapAgent() {
    if (agentBooted) return;
    agentBooted = true;
    agentBusy = true;
    const r = await post(`/stories/${storyKey}/manuscript/agent/turn`, { sid, message: '' });
    agentBusy = false;
    if (r?.reply) agentHistory = [...agentHistory, { role: 'assistant', text: r.reply }];
    if (Array.isArray(r?.suggestions)) agentSuggestions = r.suggestions;
  }

  load();
  bootstrapAgent();

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
      mangaPanels = [];
      sel = 0;
    }
  }

  async function planManga() {
    planningManga = true;
    const r = await post(`/stories/${storyKey}/manuscript/manga-plan`, { sid });
    planningManga = false;
    if (Array.isArray(r?.plan?.panels)) mangaPanels = r.plan.panels;
  }

  async function renderManga() {
    renderingManga = true;
    const r = await post(`/stories/${storyKey}/manuscript/manga-render`, { sid });
    renderingManga = false;
    if (Array.isArray(r?.plan?.panels)) mangaPanels = r.plan.panels;
  }

  async function sendAgent(text) {
    const message = (text || '').trim();
    if (!message || agentBusy) return;
    agentInput = '';
    agentHistory = [...agentHistory, { role: 'user', text: message }];
    agentBusy = true;
    const r = await post(`/stories/${storyKey}/manuscript/agent/turn`, { sid, message });
    agentBusy = false;
    if (r?.reply) agentHistory = [...agentHistory, { role: 'assistant', text: r.reply }];
    else if (r?.error) agentHistory = [...agentHistory, { role: 'assistant', text: `⚠ ${r.error}` }];
    agentSuggestions = Array.isArray(r?.suggestions) ? r.suggestions : [];
    agentAction = r?.action?.kind ? r.action : null;
  }

  const DONE_NOTES = {
    bake: 'Done — the chapters are baked.',
    'manga-plan': 'Done — the plan is updated.',
    'manga-render': 'Done — the panels are rendered.',
  };

  async function runAgentAction() {
    const action = agentAction;
    if (!action || actionBusy) return;
    agentAction = null;
    if (action.kind === 'bake') await bake();
    else if (action.kind === 'manga-plan') await planManga();
    else if (action.kind === 'manga-render') await renderManga();
    await load();
    agentHistory = [...agentHistory, { role: 'assistant', text: DONE_NOTES[action.kind] || 'Done.' }];
  }
</script>

<div class="msveil" role="dialog" aria-label="Manuscript">
  <div class="mswrap">
    <div class="mshead">
      <b>📖 Manuscript</b>
      <span class="hint">click a paragraph block to edit it</span>
      <button class="soft" onclick={bake} disabled={baking}>{baking ? 'Baking…' : 'Bake prose'}</button>
      <button class="soft" onclick={planManga} disabled={planningManga || renderingManga}>{planningManga ? 'Planning…' : 'Plan manga'}</button>
      <button class="soft" onclick={renderManga} disabled={!mangaPanels.length || renderingManga || planningManga}>{renderingManga ? 'Rendering…' : 'Render manga'}</button>
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
          {#each mangaPanels.filter((panel) => panel.chapter === cur.i) as panel (panel.id)}
            <section class="manga-panel">
              {#if panel.image}<img class="chapterart" src={panel.image} alt={`Manga panel for ${cur.label}`} />{/if}
              <div class="manga-plan"><b>{panel.shot}</b> · {panel.characters?.join(', ')}<br />{panel.moment}<br />
                <span>{panel.layout?.map((entry) => `${entry.character}: ${entry.slot}, ${entry.depth}, ${Math.round(entry.scale * 100)}%`).join(' · ')}</span><br />
                <span>{panel.render_pipeline || panel.body_lora_note}</span></div>
            </section>
          {/each}
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
      <aside class="msagent">
        <div class="aghead">🎬 Manga production agent</div>
        <div class="aglog">
          {#each agentHistory as m}
            <div class="agmsg" class:me={m.role === 'user'}>{m.text}</div>
          {/each}
          {#if agentBusy}<div class="agmsg thinking">thinking…</div>{/if}
        </div>
        {#if agentAction}
          <div class="agaction">
            {#if agentAction.note}<div class="agnote">{agentAction.note}</div>{/if}
            <button class="agrun" onclick={runAgentAction} disabled={actionBusy}>
              ▶ Run: {ACTION_LABELS[agentAction.kind] || agentAction.kind}
            </button>
          </div>
        {/if}
        {#if agentSuggestions.length}
          <div class="agchips">
            {#each agentSuggestions as chip}
              <button class="agchip" onclick={() => sendAgent(chip)} disabled={actionBusy}>{chip}</button>
            {/each}
          </div>
        {/if}
        <div class="aginput">
          <input bind:value={agentInput} placeholder="Ask about the next step…" disabled={actionBusy}
            onkeydown={(e) => e.key === 'Enter' && sendAgent(agentInput)} />
          <button onclick={() => sendAgent(agentInput)} disabled={actionBusy || !agentInput.trim()}>➤</button>
        </div>
      </aside>
    </div>
  </div>
</div>

<style>
  .msveil { position: fixed; inset: 0; z-index: 60; background: rgba(0, 0, 0, 0.55); display: flex; }
  .mswrap { margin: 3vh auto; width: min(1450px, 96vw); height: 94vh; display: flex; flex-direction: column;
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
  .manga-panel { margin: 0 0 20px; }
  .manga-panel .chapterart { margin-bottom: 7px; }
  .manga-plan { padding: 8px 10px; border-left: 2px solid var(--accent, #7aa2f7); color: var(--muted, #8b8b98); font-family: ui-sans-serif, system-ui, sans-serif; font-size: 12px; line-height: 1.45; }
  .manga-plan b { color: var(--text, #eee); }
  .manga-plan span { font-size: 11px; opacity: .8; }
  .msagent { width: 310px; border-left: 1px solid var(--border, #2a2a33); display: flex; flex-direction: column; min-height: 0; }
  .aghead { padding: 10px 12px; font-weight: 600; font-size: 13px; border-bottom: 1px solid var(--border, #2a2a33); }
  .aglog { flex: 1; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
  .agmsg { background: var(--panel-2, #1f1f28); border-radius: 8px; padding: 7px 10px; font-size: 12.5px; line-height: 1.45; white-space: pre-wrap; }
  .agmsg.me { background: rgba(122, 162, 247, 0.16); align-self: flex-end; }
  .agmsg.thinking { color: var(--muted, #8b8b98); font-style: italic; }
  .agaction { padding: 8px 12px; border-top: 1px solid var(--border, #2a2a33); }
  .agnote { font-size: 11.5px; color: var(--muted, #8b8b98); margin-bottom: 6px; }
  .agrun { width: 100%; border: 1px solid var(--accent, #7aa2f7); background: rgba(122, 162, 247, 0.14);
    border-radius: 8px; padding: 7px 10px; cursor: pointer; font-weight: 600; font-size: 12.5px; }
  .agrun:disabled { opacity: 0.5; cursor: default; }
  .agchips { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 12px; border-top: 1px solid var(--border, #2a2a33); }
  .agchip { background: var(--panel-2, #1f1f28); border: 1px solid var(--border, #2a2a33); border-radius: 999px;
    padding: 4px 10px; font-size: 11.5px; cursor: pointer; }
  .agchip:hover { border-color: var(--accent, #7aa2f7); }
  .agchip:disabled { opacity: 0.5; cursor: default; }
  .aginput { display: flex; gap: 6px; padding: 8px 12px; border-top: 1px solid var(--border, #2a2a33); }
  .aginput input { flex: 1; background: var(--panel-2, #1f1f28); color: inherit; border: 1px solid var(--border, #2a2a33);
    border-radius: 8px; padding: 7px 10px; font-size: 12.5px; }
  .aginput input:focus { outline: none; border-color: var(--accent, #7aa2f7); }
</style>

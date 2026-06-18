<script>
  import { onMount } from 'svelte';
  import { app, setActivePersona, refreshPersonas, startJob, limitedPost } from '$lib/app.svelte.js';
  import { post, del } from '$lib/api.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import { askNewPersona } from '$lib/newpersona.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';

  // Personas are server-backed; app.personas is the API-fed cache (each row's `id` == server key).
  // The page reads from it directly and re-pulls after any mutation so disk + cache stay in sync.
  const CANDIDATES = 3;   // one ✨ render fires three candidates (fresh seed each)

  // The grid shows every persona; the EDITOR below works on the selected one (defaults to active).
  let editId = $state(null);
  let editor = $derived(app.personas.find((p) => p.id === (editId ?? app.activePersona)) || app.personas[0] || null);
  // A fresh snapshot of the editor's fields, so typing into the grid's selected card doesn't fight
  // the textarea bindings. Reset whenever the edited persona changes.
  let draft = $state({ name: '', description: '', summary: '', appearance: '' });
  let loadedFor = $state(null);
  $effect(() => {
    if (editor && editor.id !== loadedFor) {
      loadedFor = editor.id;
      draft = { name: editor.name || '', description: editor.description || '',
                summary: editor.summary || '', appearance: editor.appearance || '' };
      snaps[editor.id] = sig(draft);
    }
  });

  function selectForEdit(id) { editId = id; }

  // -- creation (modal) / deletion ------------------------------------------
  async function newPersona() {
    // Opens the NewPersonaModal (mounted in the root layout). Resolves { name, description } or null.
    const res = await askNewPersona();
    if (!res) return;
    const r = await post('/personas', { name: res.name, description: res.description });
    if (r.data?.key) {
      setActivePersona(r.data.key);
      editId = r.data.key;
      await refreshPersonas();
    }
  }
  async function removePersona(p) {
    await del(`/personas/${p.id}`);
    if (editId === p.id) editId = null;
    await refreshPersonas();
    if (app.activePersona === p.id && app.personas[0]) setActivePersona(app.personas[0].id);
  }

  // -- field edits (debounced autosave, mirroring the character card editor) --
  // `draft` is the source of truth in the editor; on each change we debounce-save to the server.
  const snaps = $state({});        // key -> last-saved JSON signature
  const timers = {};               // key -> debounce handle
  function sig(d) { return JSON.stringify({ name: d.name, description: d.description, summary: d.summary, appearance: d.appearance }); }

  async function saveFields(p, d) {
    const r = await post(`/personas/${p.id}`, {
      name: d.name, description: d.description, summary: d.summary, appearance: d.appearance
    });
    if (r.data?.ok) snaps[p.id] = sig(d);
    else p._msg = r.data?.error || 'save failed';
    // Keep the grid card name in sync after a rename.
    if (r.data?.ok) { p.name = d.name; p.description = d.description; p.summary = d.summary; p.appearance = d.appearance; }
  }
  function onFieldInput(p) {
    if (!p) return;
    const cur = sig(draft);
    if (snaps[p.id] === undefined) snaps[p.id] = cur;
    if (cur === snaps[p.id]) return;
    clearTimeout(timers[p.id]);
    timers[p.id] = setTimeout(() => saveFields(p, draft), 700);
  }

  // -- avatar upload (the manual 'change picture' path) ---------------------
  let picFor = $state(null);
  let picInput = $state();
  function choosePic(id) { picFor = id; picInput?.click(); }
  async function onPic(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !picFor) return;
    const fd = new FormData(); fd.append('file', file);
    await fetch(`/api/personas/${picFor}/avatar`, { method: 'POST', body: fd });
    picFor = null;
    await refreshPersonas();
  }

  // -- ✨ generate: describe → appearance/summary → 3 full-body candidates --
  const pkey = (id) => `persona:${id}`;
  function pslot(id) { return rget(pkey(id)); }
  let descEl = $state();   // the description textarea — focused when Generate is clicked empty

  async function generate(p) {
    if (!p) return;
    // No description yet → don't disable the button (that reads as broken); instead focus the
    // description field and nudge the user. The button stays clickable so the path forward is obvious.
    if (!draft.description?.trim()) { p._msg = 'Add a description first, then ✨ generate.'; descEl?.focus(); return; }
    p._msg = null;
    const slot = rensure(pkey(p.id)); slot.busy = true; slot.cands = []; slot.err = null;
    // 1) Describe: summary + appearance tags from the long description. Preview into the draft,
    //    then save (so the appearance is on disk before the render reads it).
    const dr = await post('/personas/describe', { description: draft.description, name: draft.name });
    if (!dr.ok) { slot.busy = false; p._msg = dr.data?.error || 'describe failed'; return; }
    if (dr.data?.summary) draft.summary = dr.data.summary;
    if (dr.data?.appearance) draft.appearance = dr.data.appearance;
    snaps[p.id] = sig(draft);                       // mark clean so the autosave doesn't double-fire
    await saveFields(p, draft);
    if (!draft.appearance) { slot.busy = false; p._msg = 'no appearance produced — check your description'; return; }
    // 2) Render CANDIDATES full-body portraits (fresh seed each → varied). Cancellable via the
    //    Activity card; limitedPost caps concurrency across all in-flight renders.
    const job = startJob('Persona portrait', draft.name || 'persona', 'settings/personas', CANDIDATES);
    for (let i = 0; i < CANDIDATES; i++) {
      const r = await limitedPost(`/personas/${p.id}/image-candidate`, {}, {}, job);
      if (r.ok && r.data?.image) { rensure(pkey(p.id)).cands = [...rensure(pkey(p.id)).cands, r.data.image]; job.done = i + 1; }
      else { slot.err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    slot.busy = false;
  }
  async function pick(p, dataUri) {
    const r = await post(`/personas/${p.id}/avatar/from-data`, { data: dataUri });
    if (r.data?.ok) { rensure(pkey(p.id)).cands = []; await refreshPersonas(); }
  }

  onMount(() => { refreshPersonas(); });
</script>

<div class="page">
  <div class="col wide">
    <div class="head">
      <div>
        <h2>Personas</h2>
        <p class="lede">Who <em>you</em> are in the chat — the {'{{'}user{'}}'} side. Describe yourself, then
          ✨ generate a short chat description and a full-body picture. The active one is used as you.</p>
      </div>
      <button class="newp" onclick={newPersona}>＋ New persona</button>
    </div>

    <!-- The grid: every persona as a card. Click to edit; the active one is highlighted. -->
    <div class="pgrid">
      {#each app.personas as p (p.id)}
        <button type="button" class="pcard" class:sel={p.id === editor?.id} class:active={p.id === app.activePersona}
          onclick={() => selectForEdit(p.id)} title={p.name}>
          <div class="pcpic">
            {#if p.avatar}<img src={p.avatar} alt="" />{:else}<span class="pph">{(p.name?.[0] || '?').toUpperCase()}</span>{/if}
          </div>
          <div class="pcmeta">
            <div class="pcname">{p.name || 'Unnamed'}</div>
            <div class="pcsum">{p.summary || p.description || 'No description yet'}</div>
          </div>
          {#if p.id === app.activePersona}<span class="pctag">active</span>{/if}
        </button>
      {/each}
      <button type="button" class="pcard add" onclick={newPersona}>
        <div class="pcpic ph"><span>＋</span></div>
        <div class="pcmeta"><div class="pcname muted">New persona</div></div>
      </button>
    </div>

    <!-- The editor for the selected persona. -->
    {#if editor}
      {@const slot = pslot(editor.id)}
      <div class="prow2 sel">
        <button type="button" class="ppic" onclick={() => choosePic(editor.id)} title="Upload a picture">
          {#if editor.avatar}<img src={editor.avatar} alt="" />{:else}<span class="ppic-ph">{(draft.name?.[0] || '?').toUpperCase()}</span>{/if}
          <span class="ppic-edit">change</span>
        </button>
        <div class="pbody">
          <div class="prow2-head">
            <input class="pname" bind:value={draft.name} oninput={() => onFieldInput(editor)} placeholder="Name" />
            {#if editor.id === app.activePersona}
              <span class="badge">active</span>
            {:else}
              <button class="ghost sm" onclick={() => setActivePersona(editor.id)}>Use</button>
            {/if}
            <button class="ghost sm" onclick={() => removePersona(editor)} title="Delete persona" aria-label="Delete">✕</button>
          </div>

          <label class="fld-lbl" for="pf-desc">Description <span class="lo">— who you are, in your own words. The source for ✨ generation.</span></label>
          <textarea id="pf-desc" class="pdescr" bind:this={descEl} bind:value={draft.description} oninput={() => onFieldInput(editor)} rows="3"
            placeholder="Describe yourself — appearance, personality, how you carry yourself…"></textarea>

          <div class="lblrow">
            <label class="fld-lbl" for="pf-sum">Summary <span class="lo">— the short blurb used in chat (generated, editable)</span></label>
            <button class="ghost xsm gen" onclick={() => generate(editor)} disabled={slot?.busy}>
              {slot?.busy ? '✨ Generating…' : '✨ Generate'}
            </button>
          </div>
          <textarea id="pf-sum" class="pdescr sm" bind:value={draft.summary} oninput={() => onFieldInput(editor)} rows="2"
            placeholder="A short description of who you are — click ✨ Generate, or write your own."></textarea>

          {#if draft.appearance}
            <details class="app">
              <summary>Appearance tags <span class="lo">— drives the picture</span></summary>
              <textarea class="pdescr app" bind:value={draft.appearance} oninput={() => onFieldInput(editor)} rows="2"></textarea>
            </details>
          {/if}

          {#if editor._msg}<span class="pmsg err">{editor._msg}</span>{/if}
          {#if slot?.err}<span class="pmsg err">⚠ {slot.err}</span>{/if}
          {#if slot?.cands?.length}
            <div class="cands">
              {#each slot.cands as img, i (i)}
                <div class="cand">
                  <ZoomImage src={img} caption={`${draft.name || 'persona'} candidate ${i + 1}`} />
                  <button class="usec" onclick={() => pick(editor, img)}>Use</button>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      </div>
    {:else}
      <p class="empty">No persona selected. Click a card above, or create a new one.</p>
    {/if}
  </div>
  <input type="file" accept="image/*" bind:this={picInput} onchange={onPic} hidden />
</div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .lede { color: var(--muted); font-size: 13px; margin: -8px 0 4px; max-width: 620px; }
  .newp { padding: 9px 14px; font-weight: 560; font-size: 13px; flex: none; }

  /* the grid */
  .pgrid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px;
    margin: 18px 0 22px;
  }
  .pcard {
    display: flex; flex-direction: column; gap: 8px; padding: 10px;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px;
    box-shadow: none; text-align: left; cursor: pointer; transition: border-color .12s, background .12s;
    position: relative;
  }
  .pcard:hover { border-color: var(--border); background: var(--elev-2); }
  .pcard.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  .pcard.active::after {
    content: ''; position: absolute; top: 8px; right: 8px; width: 8px; height: 8px; border-radius: 50%;
    background: var(--good); box-shadow: 0 0 0 3px var(--elev);
  }
  /* Portrait box — tall enough (2:3) that a full-body image fills it without heavy cropping. */
  .pcpic { width: 100%; aspect-ratio: 2 / 3; border-radius: 9px; overflow: hidden;
    background: linear-gradient(135deg, var(--accent), #9a6dff); display: grid; place-items: center; }
  .pcpic img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .pcpic.ph { background: var(--elev-2); color: var(--faint); font-size: 26px; border: 1px dashed var(--border); }
  .pph { display: grid; place-items: center; width: 100%; height: 100%; font-size: 30px; font-weight: 700; color: #fff; }
  .pcmeta { min-width: 0; }
  .pcname { font-size: 13.5px; font-weight: 650; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pcsum { font-size: 12px; color: var(--muted); line-height: 1.4; margin-top: 2px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .pcard.add .pcmeta { align-self: flex-start; }
  .pctag { position: absolute; bottom: 8px; right: 8px; font-size: 9.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .3px; color: var(--good); background: rgba(87, 217, 163, .12);
    border: 1px solid rgba(87, 217, 163, .3); border-radius: 999px; padding: 1px 7px; }

  /* the editor */
  .prow2 { display: flex; gap: 14px; padding: 14px; background: var(--elev);
    border: 1px solid var(--border-soft); border-radius: 12px; align-items: stretch; }
  .prow2.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  /* The portrait fills the FULL HEIGHT of the card (stretches with the form beside it) so a
     full-body image reads whole, not cropped to a bust. Width keeps a portrait aspect. */
  .ppic { position: relative; width: 150px; flex: none; align-self: stretch; padding: 0; overflow: hidden;
    border-radius: 12px; border: 1px solid var(--border); box-shadow: none; cursor: pointer;
    background: linear-gradient(135deg, var(--accent), #9a6dff); }
  .ppic:hover { filter: brightness(1.05); }
  .ppic img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .ppic-ph { display: grid; place-items: center; width: 100%; height: 100%; font-size: 32px; font-weight: 700; color: #fff; }
  .ppic-edit { position: absolute; left: 0; right: 0; bottom: 0; padding: 2px; text-align: center;
    font-size: 10px; color: #fff; background: rgba(0, 0, 0, .55); opacity: 0; transition: opacity .12s; }
  .ppic:hover .ppic-edit { opacity: 1; }
  .pbody { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .prow2-head { display: flex; align-items: center; gap: 10px; }
  .pname { flex: 1; min-width: 0; font-weight: 600; }
  .badge { flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--accent); border: 1px solid var(--accent); border-radius: 999px; padding: 2px 8px; }
  .fld-lbl { display: block; font-size: 11px; color: var(--muted); margin: 12px 0 4px; text-transform: uppercase; letter-spacing: .3px; font-weight: 600; }
  .fld-lbl:first-child { margin-top: 8px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .lblrow { display: flex; align-items: flex-end; justify-content: space-between; gap: 10px; }
  .lblrow .fld-lbl { margin-bottom: 4px; }
  .pdescr { width: 100%; min-height: 48px; resize: vertical; font-size: 13.5px; line-height: 1.5; }
  .pdescr.sm { min-height: 36px; }
  .pdescr.app { margin-top: 6px; font-size: 12.5px; }
  .xsm { font-size: 11.5px; padding: 4px 10px; border-radius: 7px; }
  .gen { white-space: nowrap; }
  .app { margin-top: 8px; }
  .app summary { cursor: pointer; font-size: 11.5px; color: var(--muted); }
  .pmsg { font-size: 12px; margin-top: 6px; display: block; }
  .pmsg.err { color: var(--bad); }
  .cands { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
  .cand { display: flex; flex-direction: column; gap: 3px; width: 84px; }
  .cand :global(.zwrap) { aspect-ratio: 3 / 4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .cand :global(img) { border-radius: 8px; }
  .usec { font-size: 10px; padding: 3px 0; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); }
  .usec:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .empty { color: var(--muted); font-size: 13.5px; margin: 30px 0; }
  .muted { color: var(--muted); }
</style>

<script>
  import { onMount } from 'svelte';
  import { post } from '$lib/api.js';
  import { app, refreshPersonas, setActivePersona, limitedPost, startJob, finishJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import { askNewPersona } from '$lib/newpersona.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';

  let editKey = $state(app.activePersona || null);
  let persona = $derived(app.personas.find((p) => p.key === editKey) || null);

  let draft = $state({ name: '', description: '', summary: '', appearance: '' });
  let draftSnap = $state(null);
  let draftTimer = null;
  let saveMsg = $state(null);
  let loadedFor = $state(null);

  $effect(() => {
    if (persona && persona.key !== loadedFor) {
      loadedFor = persona.key;
      draft = {
        name: persona.name || '',
        description: persona.description || '',
        summary: persona.summary || '',
        appearance: persona.appearance || ''
      };
      draftSnap = JSON.stringify(draft);
      saveMsg = null;
    }
  });

  $effect(() => {
    const cur = JSON.stringify(draft);
    if (!persona || draftSnap === null || cur === draftSnap) return;
    clearTimeout(draftTimer);
    draftTimer = setTimeout(doSave, 700);
  });

  async function doSave() {
    if (!persona) return;
    clearTimeout(draftTimer);
    saveMsg = { text: 'Saving…' };
    const r = await post(`/personas/${persona.key}`, draft);
    if (r.ok && !r.data?.error) {
      saveMsg = { ok: true, text: '✓ Saved' };
      draftSnap = JSON.stringify(draft);
      await refreshPersonas();
    } else {
      saveMsg = { err: true, text: r.data?.error || 'save failed' };
    }
  }

  // Generate summary + appearance from description, then save
  let describing = $state(false);
  async function describe() {
    if (!persona || !draft.description.trim() || describing) return;
    describing = true;
    saveMsg = { text: 'Generating…' };
    const r = await post('/personas/describe', { name: draft.name, description: draft.description });
    describing = false;
    if (r.ok && r.data?.summary !== undefined) {
      draft.summary = r.data.summary || '';
      if (r.data.appearance) draft.appearance = r.data.appearance;
      await doSave();
    } else {
      saveMsg = { err: true, text: r.data?.error || 'generation failed' };
    }
  }

  // Image candidates
  const ckey = (k) => `persona:${k}`;
  const curSlot = $derived(persona ? rget(ckey(persona.key)) : { busy: false, cands: [], err: false });

  async function generateImages() {
    if (!persona) return;
    // Flush any pending draft changes first — backend reads appearance from disk
    clearTimeout(draftTimer);
    if (JSON.stringify(draft) !== draftSnap) await doSave();
    const key = persona.key;
    const s = rensure(ckey(key));
    s.cands = []; s.busy = true; s.err = false;
    const job = startJob('render', `Portrait · ${persona.name}`, 'personas', 3);
    await Promise.all([0, 1, 2].map(async () => {
      const r = await limitedPost(`/personas/${key}/image-candidate`, {}, {}, job);
      if (r.ok && r.data?.image) {
        rensure(ckey(key)).cands = [...rensure(ckey(key)).cands, r.data.image];
        job.done = (job.done || 0) + 1;
      } else if (r.data?.error) {
        rensure(ckey(key)).err = r.data.error;
      }
    }));
    rensure(ckey(key)).busy = false;
    finishJob(job, 'done');
  }

  async function pickCandidate(key, img) {
    const r = await post(`/personas/${key}/avatar/from-data`, { data: img });
    if (r.ok) {
      rensure(ckey(key)).cands = [];
      await refreshPersonas();
    }
  }

  // Avatar upload
  let fileInput = $state();
  let uploadFor = $state(null);
  function openUpload(key) { uploadFor = key; fileInput?.click(); }
  async function onFileChange(e) {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f || !uploadFor) return;
    const fd = new FormData(); fd.append('file', f);
    await fetch(`/api/personas/${uploadFor}/avatar`, { method: 'POST', body: fd });
    uploadFor = null;
    await refreshPersonas();
  }

  // Create / delete
  async function createPersona() {
    const data = await askNewPersona();
    if (!data) return;
    const r = await post('/personas', data);
    if (r.ok && r.data?.key) {
      await refreshPersonas();
      editKey = r.data.key;
    }
  }

  async function deletePersona() {
    if (!persona) return;
    if (!confirm(`Delete "${persona.name}"?`)) return;
    await fetch(`/api/personas/${persona.key}`, { method: 'DELETE' });
    const remaining = app.personas.filter((p) => p.key !== persona.key);
    editKey = remaining[0]?.key || null;
    await refreshPersonas();
  }

  onMount(() => { refreshPersonas(); });
</script>

<div class="personas">
  <!-- Sidebar: persona list -->
  <div class="plist">
    <div class="plhead">
      <span class="plttl">Personas</span>
      <button class="ghost sm" onclick={createPersona}>＋ New</button>
    </div>
    <div class="pcards">
      {#each app.personas as p (p.key)}
        <button class="pcard" class:sel={p.key === editKey} class:active={p.key === app.activePersona}
          onclick={() => (editKey = p.key)}>
          <div class="pcav">
            {#if p.avatar}
              <img src={p.avatar} alt={p.name} />
            {:else}
              <span class="pcav-ph">{(p.name?.[0] || '?').toUpperCase()}</span>
            {/if}
          </div>
          <div class="pcinfo">
            <div class="pcname">{p.name}</div>
            {#if p.summary}<div class="pcsumm">{p.summary}</div>{/if}
            {#if p.key === app.activePersona}<span class="actlabel">active</span>{/if}
          </div>
        </button>
      {/each}
      {#if !app.personas.length}
        <div class="empty-list">No personas yet.</div>
      {/if}
    </div>
  </div>

  <!-- Editor -->
  {#if persona}
    <div class="editor">
      <div class="ehead">
        <button class="avbtn" onclick={() => openUpload(persona.key)} title="Change avatar">
          {#if persona.avatar}
            <img src={persona.avatar} alt={persona.name} class="avimg" />
          {:else}
            <span class="avph">{(persona.name?.[0] || '?').toUpperCase()}</span>
          {/if}
          <span class="avov">change</span>
        </button>
        <div class="ehd">
          <input class="ename" bind:value={draft.name} placeholder="Name" />
          <div class="eacts">
            {#if persona.key !== app.activePersona}
              <button class="ghost sm" onclick={() => setActivePersona(persona.key)}>Set active</button>
            {:else}
              <span class="badge">active</span>
            {/if}
            <button class="ghost sm danger" onclick={deletePersona}>Delete</button>
          </div>
          {#if saveMsg}
            <span class="smsg" class:ok={saveMsg.ok} class:err={saveMsg.err}>{saveMsg.text}</span>
          {/if}
        </div>
      </div>

      <div class="ebody">
        <label class="flabel" for="p-desc">Description</label>
        <textarea id="p-desc" class="fdesc" bind:value={draft.description} rows="4"
          placeholder="Describe yourself — personality, background, appearance…"></textarea>

        <div class="flrow">
          <label class="flabel" for="p-summ">Summary</label>
          <button class="ghost xs" onclick={describe}
            disabled={describing || !draft.description.trim()}>
            {describing ? '…' : '✨ Generate'}
          </button>
        </div>
        <textarea id="p-summ" class="fsumm" bind:value={draft.summary} rows="2"
          placeholder="Short bio used in chat…"></textarea>

        <details class="fapp-wrap">
          <summary class="flabel">Appearance tags</summary>
          <textarea class="fapp" bind:value={draft.appearance} rows="3"
            placeholder="booru tags: 1girl, blue hair, green eyes…"></textarea>
        </details>

        {#if draft.appearance || persona.appearance}
          <div class="genrow">
            <button class="ghost sm" onclick={generateImages} disabled={curSlot.busy}>
              {curSlot.busy ? 'Rendering…' : '🎨 Generate portrait'}
            </button>
            {#if curSlot.err}<span class="errtxt">{curSlot.err}</span>{/if}
          </div>
          {#if curSlot.cands?.length}
            <div class="cands">
              {#each curSlot.cands as img, i (i)}
                <div class="cand">
                  <ZoomImage src={img} caption={`Candidate ${i + 1}`} />
                  <button class="ghost xs" onclick={() => pickCandidate(persona.key, img)}>Use</button>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
      </div>
    </div>
  {:else}
    <div class="empty">Select a persona or create one.</div>
  {/if}
</div>

<input type="file" accept="image/*" bind:this={fileInput} onchange={onFileChange} hidden />

<style>
  .personas {
    display: flex;
    gap: 20px;
    align-items: flex-start;
  }

  /* Sidebar */
  .plist {
    width: 210px;
    flex: none;
    display: flex;
    flex-direction: column;
    position: sticky;
    top: 0;
  }
  .plhead {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .plttl {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .5px;
    color: var(--muted);
  }
  .pcards {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .pcard {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px;
    border-radius: 8px;
    border: 1px solid transparent;
    background: none;
    cursor: pointer;
    text-align: left;
    transition: background .1s;
  }
  .pcard:hover { background: var(--elev); }
  .pcard.sel { background: var(--elev); border-color: var(--border); }
  .pcard.active { border-color: color-mix(in srgb, var(--accent) 50%, transparent); }

  .pcav {
    width: 44px;
    height: 58px;
    flex: none;
    border-radius: 6px;
    overflow: hidden;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
    display: grid;
    place-items: center;
  }
  .pcav img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .pcav-ph { font-size: 20px; font-weight: 700; color: #fff; }

  .pcinfo { flex: 1; min-width: 0; padding-top: 2px; }
  .pcname {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pcsumm {
    font-size: 11px;
    color: var(--muted);
    margin-top: 2px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    line-height: 1.4;
  }
  .actlabel {
    display: inline-block;
    margin-top: 4px;
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .4px;
    color: var(--accent);
  }
  .empty-list { padding: 12px 4px; font-size: 12px; color: var(--faint); }

  /* Editor */
  .editor {
    flex: 1;
    min-width: 0;
    background: var(--panel);
    border: 1px solid var(--border-soft);
    border-radius: 12px;
    overflow: hidden;
  }

  .ehead {
    display: flex;
    gap: 14px;
    padding: 16px 18px;
    border-bottom: 1px solid var(--border);
    align-items: flex-start;
  }

  .avbtn {
    position: relative;
    width: 76px;
    height: 102px;
    flex: none;
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid var(--border);
    background: linear-gradient(135deg, var(--accent), #9a6dff);
    padding: 0;
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  .avbtn:hover .avov { opacity: 1; }
  .avimg { width: 100%; height: 100%; object-fit: cover; display: block; }
  .avph { font-size: 30px; font-weight: 700; color: #fff; }
  .avov {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    font-size: 11px;
    color: #fff;
    background: rgba(0, 0, 0, .55);
    opacity: 0;
    transition: opacity .12s;
  }

  .ehd {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding-top: 4px;
  }
  .ename {
    font-size: 16px;
    font-weight: 700;
    background: none;
    border: none;
    border-bottom: 1px solid var(--border);
    border-radius: 0;
    padding: 4px 0;
    color: var(--text);
    width: 100%;
  }
  .ename:focus { outline: none; border-bottom-color: var(--accent); }

  .eacts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .danger { color: var(--bad) !important; }

  .smsg { font-size: 11px; color: var(--muted); }
  .smsg.ok { color: var(--good); }
  .smsg.err { color: var(--bad); }

  /* Form */
  .ebody {
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .flabel {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .4px;
    color: var(--muted);
    display: block;
    margin-bottom: 4px;
  }
  .flrow { display: flex; align-items: center; gap: 10px; margin-bottom: 0; }
  .flrow .flabel { flex: 1; margin-bottom: 0; }

  .fdesc, .fsumm, .fapp { width: 100%; resize: vertical; font-size: 13px; }

  .fapp-wrap { border: none; padding: 0; }
  .fapp-wrap > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 6px; }
  .fapp-wrap > summary::before { content: '▸'; font-size: 10px; color: var(--faint); }
  .fapp-wrap[open] > summary::before { content: '▾'; }
  .fapp { margin-top: 6px; }

  .genrow { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
  .errtxt { font-size: 12px; color: var(--bad); }

  .cands { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
  .cand { display: flex; flex-direction: column; gap: 4px; width: 90px; }
  .cand :global(.zwrap) { aspect-ratio: 3/4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }

  .empty {
    flex: 1;
    padding: 40px 20px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
    background: var(--panel);
    border: 1px solid var(--border-soft);
    border-radius: 12px;
  }
</style>

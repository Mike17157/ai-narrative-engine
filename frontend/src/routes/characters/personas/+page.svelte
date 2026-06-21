<script>
  import { onMount } from 'svelte';
  import { post } from '$lib/api.js';
  import { app, refreshPersonas, setActivePersona, limitedPost, startJob, finishJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import ZoomImage from '$lib/components/shared/ZoomImage.svelte';

  // ── Modal state ──────────────────────────────────────────────────────────
  let editKey = $state(null);
  let open = $derived(editKey !== null);
  let persona = $derived(app.personas.find((p) => p.key === editKey) ?? null);

  let draft = $state({ name: '', description: '', summary: '', appearance: '' });
  let draftSnap = $state(null);
  let draftTimer = null;
  let saveMsg = $state(null);
  let loadedFor = $state(null);

  $effect(() => {
    if (!persona) return;
    if (persona.key === loadedFor) return;
    loadedFor = persona.key;
    draft = {
      name: persona.name || '',
      description: persona.description || '',
      summary: persona.summary || '',
      appearance: persona.appearance || ''
    };
    draftSnap = JSON.stringify(draft);
    saveMsg = null;
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

  // ── Describe (generate summary + appearance) ─────────────────────────────
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

  // ── Portrait generation ──────────────────────────────────────────────────
  const ckey = (k) => `persona:${k}`;
  const curSlot = $derived(persona ? rget(ckey(persona.key)) : { busy: false, cands: [], err: false });

  async function generateImages() {
    if (!persona) return;
    // Flush pending draft first — backend reads appearance from disk
    clearTimeout(draftTimer);
    if (JSON.stringify(draft) !== draftSnap) await doSave();
    if (!draft.appearance) {
      saveMsg = { err: true, text: 'Generate summary & appearance first (✨ button above)' };
      return;
    }
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

  async function pickCandidate(img) {
    if (!persona) return;
    const r = await post(`/personas/${persona.key}/avatar/from-data`, { data: img });
    if (r.ok) {
      rensure(ckey(persona.key)).cands = [];
      await refreshPersonas();
    }
  }

  // ── Avatar upload ────────────────────────────────────────────────────────
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

  // ── Create / delete ──────────────────────────────────────────────────────
  async function createPersona() {
    const r = await post('/personas', { name: 'New persona', description: '' });
    if (r.ok && r.data?.key) {
      await refreshPersonas();
      editKey = r.data.key;
    }
  }

  async function deletePersona() {
    if (!persona) return;
    if (!confirm(`Delete "${persona.name}"?`)) return;
    await fetch(`/api/personas/${persona.key}`, { method: 'DELETE' });
    editKey = null;
    await refreshPersonas();
  }

  function closeModal() {
    clearTimeout(draftTimer);
    loadedFor = null;
    editKey = null;
  }

  function onBackdrop(e) { if (e.target === e.currentTarget) closeModal(); }
  function onKeydown(e) { if (e.key === 'Escape') closeModal(); }

  onMount(() => { refreshPersonas(); });
</script>

<svelte:window onkeydown={onKeydown} />

<!-- ── Persona grid ──────────────────────────────────────────────────────── -->
<div class="page">
  <div class="page-head">
    <h2 class="page-title">Personas</h2>
    <p class="page-sub">Who you are in the story. The active persona is what the director narrates to.</p>
    <button onclick={createPersona}>＋ New persona</button>
  </div>

  <div class="grid">
    {#each app.personas as p (p.key)}
      <button class="card" class:active={p.key === app.activePersona} onclick={() => (editKey = p.key)}>
        <div class="card-av">
          {#if p.avatar}
            <img src={p.avatar} alt={p.name} />
          {:else}
            <span class="av-ph">{(p.name?.[0] || '?').toUpperCase()}</span>
          {/if}
        </div>
        <div class="card-info">
          <div class="card-name">{p.name}</div>
          {#if p.summary}<div class="card-summ">{p.summary}</div>{/if}
        </div>
        {#if p.key === app.activePersona}<span class="active-dot" title="Active"></span>{/if}
      </button>
    {/each}
    {#if !app.personas.length}
      <div class="empty">No personas yet — create one to get started.</div>
    {/if}
  </div>
</div>

<!-- ── Edit modal ────────────────────────────────────────────────────────── -->
{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="backdrop" onclick={onBackdrop}>
    <div class="modal" role="dialog" aria-modal="true">

      <div class="modal-head">
        <!-- Avatar -->
        <button class="av-btn" onclick={() => persona && openUpload(persona.key)} title="Upload avatar">
          {#if persona?.avatar}
            <img src={persona.avatar} alt={persona.name} class="av-img" />
          {:else}
            <span class="av-ph lg">{(draft.name?.[0] || '?').toUpperCase()}</span>
          {/if}
          <span class="av-ov">upload</span>
        </button>

        <div class="modal-title-area">
          <input class="name-input" bind:value={draft.name} placeholder="Name" />
          <div class="modal-actions">
            {#if persona && persona.key !== app.activePersona}
              <button class="ghost sm" onclick={() => { setActivePersona(persona.key); }}>Set active</button>
            {:else if persona}
              <span class="active-badge">active</span>
            {/if}
            <button class="ghost sm danger" onclick={deletePersona}>Delete</button>
          </div>
          {#if saveMsg}
            <span class="save-msg" class:ok={saveMsg.ok} class:err={saveMsg.err}>{saveMsg.text}</span>
          {/if}
        </div>

        <button class="close-btn" onclick={closeModal} title="Close">✕</button>
      </div>

      <div class="modal-body">
        <!-- Description -->
        <div class="field">
          <div class="field-row">
            <label for="p-desc">Description</label>
            <button class="ghost xs" onclick={describe} disabled={describing || !draft.description.trim()}>
              {describing ? '…' : '✨ Generate summary & appearance'}
            </button>
          </div>
          <textarea id="p-desc" bind:value={draft.description} rows="4"
            placeholder="Describe yourself — personality, background, what you look like…"></textarea>
        </div>

        <!-- Summary -->
        <div class="field">
          <label for="p-summ">Summary <span class="dim">(shown in chat context)</span></label>
          <textarea id="p-summ" bind:value={draft.summary} rows="2"
            placeholder="Short bio…"></textarea>
        </div>

        <!-- Appearance -->
        <div class="field">
          <label for="p-app">Appearance tags <span class="dim">(booru tags for portrait generation)</span></label>
          <textarea id="p-app" bind:value={draft.appearance} rows="2"
            placeholder="1girl, blue hair, green eyes, …"></textarea>
        </div>

        <!-- Portrait generation -->
        <div class="gen-row">
          <button class="ghost sm" onclick={generateImages}
            disabled={curSlot.busy || !draft.appearance.trim()}>
            {curSlot.busy ? 'Rendering…' : '🎨 Generate portrait'}
          </button>
          {#if !draft.appearance.trim() && !curSlot.busy}
            <span class="gen-hint">Use ✨ above to generate appearance tags first</span>
          {/if}
          {#if curSlot.err}<span class="err">{curSlot.err}</span>{/if}
        </div>

        {#if curSlot.cands?.length}
          <div class="cands">
            {#each curSlot.cands as img, i (i)}
              <div class="cand">
                <ZoomImage src={img} caption={`Option ${i + 1}`} />
                <button class="ghost xs" onclick={() => pickCandidate(img)}>Use as avatar</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>

    </div>
  </div>
{/if}

<input type="file" accept="image/*" bind:this={fileInput} onchange={onFileChange} hidden />

<style>
  /* ── Page ─────────────────────────────────────────────────────────────── */
  .page { padding: 24px 28px; display: flex; flex-direction: column; gap: 24px; }

  .page-head { display: flex; align-items: flex-start; gap: 16px; }
  .page-title { margin: 0; font-size: 18px; font-weight: 700; }
  .page-sub { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; flex: 1; padding-top: 3px; }

  /* ── Grid ─────────────────────────────────────────────────────────────── */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }

  .card {
    position: relative;
    display: flex; flex-direction: column; align-items: center;
    gap: 10px; padding: 18px 14px 14px;
    background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: 14px; cursor: pointer; text-align: center;
    transition: border-color .12s, background .12s;
  }
  .card:hover { border-color: var(--border); background: var(--elev); }
  .card.active { border-color: color-mix(in srgb, var(--accent) 60%, transparent); }

  .card-av {
    width: 72px; height: 96px; border-radius: 10px; overflow: hidden; flex: none;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
    display: grid; place-items: center;
  }
  .card-av img { width: 100%; height: 100%; object-fit: cover; }
  .av-ph { font-size: 24px; font-weight: 700; color: #fff; }

  .card-info { display: flex; flex-direction: column; gap: 4px; width: 100%; }
  .card-name { font-size: 13px; font-weight: 650; color: var(--text); }
  .card-summ {
    font-size: 11px; color: var(--muted); line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }

  .active-dot {
    position: absolute; top: 10px; right: 10px;
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); box-shadow: 0 0 6px var(--accent);
  }

  .empty { grid-column: 1 / -1; padding: 32px; text-align: center; color: var(--muted); font-size: 13px; }

  /* ── Backdrop + modal ─────────────────────────────────────────────────── */
  .backdrop {
    position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, .6); backdrop-filter: blur(3px);
    display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }

  .modal {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 18px; width: 100%; max-width: 540px;
    max-height: 90vh; overflow-y: auto;
    display: flex; flex-direction: column;
    box-shadow: 0 24px 64px rgba(0, 0, 0, .6);
  }

  /* ── Modal header ─────────────────────────────────────────────────────── */
  .modal-head {
    display: flex; gap: 16px; align-items: flex-start;
    padding: 20px 22px; border-bottom: 1px solid var(--border-soft);
    position: sticky; top: 0; background: var(--panel); z-index: 1;
  }

  .av-btn {
    position: relative; width: 72px; height: 96px; flex: none;
    border-radius: 10px; overflow: hidden; border: 1px solid var(--border);
    background: linear-gradient(135deg, var(--accent), #9a6dff);
    padding: 0; cursor: pointer; display: grid; place-items: center;
  }
  .av-btn:hover .av-ov { opacity: 1; }
  .av-img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .av-ph.lg { font-size: 28px; font-weight: 700; color: #fff; }
  .av-ov {
    position: absolute; inset: 0; display: grid; place-items: center;
    font-size: 11px; color: #fff; background: rgba(0, 0, 0, .55);
    opacity: 0; transition: opacity .12s;
  }

  .modal-title-area { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding-top: 4px; }

  .name-input {
    font-size: 17px; font-weight: 700; background: none; border: none;
    border-bottom: 1px solid var(--border); border-radius: 0;
    padding: 4px 0; color: var(--text); width: 100%;
  }
  .name-input:focus { outline: none; border-bottom-color: var(--accent); }

  .modal-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .active-badge {
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent);
    padding: 2px 10px; border-radius: 999px;
  }
  .danger { color: var(--bad) !important; }

  .save-msg { font-size: 11.5px; color: var(--muted); }
  .save-msg.ok { color: var(--good); }
  .save-msg.err { color: var(--bad); }

  .close-btn {
    flex: none; width: 30px; height: 30px; border-radius: 50%;
    border: 1px solid var(--border); background: var(--elev);
    display: grid; place-items: center; font-size: 13px;
    cursor: pointer; color: var(--muted); transition: color .1s;
    box-shadow: none;
  }
  .close-btn:hover { color: var(--text); filter: none; }

  /* ── Modal body ───────────────────────────────────────────────────────── */
  .modal-body { padding: 20px 22px; display: flex; flex-direction: column; gap: 16px; }

  .field { display: flex; flex-direction: column; gap: 6px; }
  .field-row { display: flex; align-items: center; gap: 10px; }
  .field-row label { flex: 1; margin: 0; }

  label { font-size: 12px; font-weight: 600; color: var(--muted); display: block; }
  .dim { font-weight: 400; }
  textarea { width: 100%; resize: vertical; font-size: 13px; }

  .gen-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .gen-hint { font-size: 12px; color: var(--muted); font-style: italic; }
  .err { font-size: 12px; color: var(--bad); }

  .cands { display: flex; gap: 10px; flex-wrap: wrap; }
  .cand { display: flex; flex-direction: column; align-items: center; gap: 6px; width: 120px; }
  .cand :global(.zwrap) { aspect-ratio: 3/4; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); width: 100%; }
</style>

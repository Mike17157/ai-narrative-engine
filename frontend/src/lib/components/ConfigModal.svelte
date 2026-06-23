<script>
  // The reusable point-of-use ⚙ modal, opened at every chat/generation surface. Two tabs:
  //   Configs   — the active PRESET for this surface (scoped to its function group)
  //   Lorebooks — attach books to the thread + AI-augment a book
  // Deep management (presets, models, connections) lives in the Library. Mounted once in
  // the root layout; driven by the configModal store.
  import { onMount } from 'svelte';
  import { get, post, put } from '$lib/api.js';
  import { app, refreshAll } from '$lib/app.svelte.js';
  import { configModal, closeConfigModal } from '$lib/configModal.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import LorebookPicker from '$lib/components/shared/LorebookPicker.svelte';
  import ImagePresetPicker from '$lib/components/shared/ImagePresetPicker.svelte';
  import Modal from '$lib/components/shared/Modal.svelte';

  // Point-of-use picker: just the per-surface chat Config + attached Lorebooks. Models &
  // connections live INSIDE presets now (Library ▸ Presets), not here.
  const TABS = [
    { id: 'configs',   label: 'Configs' },
    { id: 'lorebooks', label: 'Lorebooks' },
  ];

  // ── Text-model items (Configs model picker + augment) ──
  let textModels = $state([]);
  let modelItems = $derived(textModels.map((m) => ({ value: m.id, label: m.name })));

  async function loadTextModels() {
    try { const r = await get('/text-models'); textModels = r.models || []; }
    catch { textModels = []; }
  }

  onMount(async () => { await refreshAll(); await loadTextModels(); await loadPresets(); await loadImagePreset(); });
  // Refresh model lists whenever the modal (re)opens, so a just-saved connection shows up.
  $effect(() => { if (configModal.open) { refreshAll(); loadTextModels(); loadImagePreset(); } });

  // ════════════════════════════ Configs tab — chat preset picker ════════════════════════════
  // The Configs tab is purely a PRESET picker now: free chat (and every surface) is driven by a
  // preset. Pipeline stages live on presets (Library), not here.
  let presetLib = $state({ active: '', presets: [] });
  let presetGroups = $derived.by(() => {
    const m = new Map();
    for (const p of [...presetLib.presets].sort((a, b) => (a.order || 0) - (b.order || 0))) {
      const g = p.group || 'Other';
      if (!m.has(g)) m.set(g, []);
      m.get(g).push(p);
    }
    return [...m.entries()]
      .map(([label, items]) => ({ label, items, ord: Math.min(...items.map((p) => p.order || 0)) }))
      .sort((a, b) => a.ord - b.ord);
  });
  // A surface scopes the picker to its own function group (e.g. chat → 'Chat'); '' = all.
  let scopedGroups = $derived(configModal.presetGroup
    ? presetGroups.filter((g) => g.label === configModal.presetGroup) : presetGroups);
  let scopedPresets = $derived(scopedGroups.flatMap((g) => g.items));
  let pickTitle = $derived(configModal.presetGroup ? `${configModal.presetGroup} preset` : 'Chat preset');
  async function loadPresets() {
    try { presetLib = await get('/presets'); } catch { /* none */ }
  }
  async function activatePreset(id) {
    const r = await post(`/presets/${id}/activate`);
    if (r.data?.active) presetLib = { ...presetLib, active: r.data.active };
  }

  // ── Provider/model toggle for the active preset (point-of-use) ──
  // The active preset drives this surface's model. Surface a Provider (connection) + Model
  // picker right here so you can flip between cloud and local without leaving the chat.
  // Local = the Ollama connection (auto-seeded server-side, always present).
  let activePreset = $derived(presetLib.presets.find((p) => p.id === presetLib.active)
    || scopedPresets.find((p) => p.id === presetLib.active) || scopedPresets[0] || null);
  let textConns = $derived((app.conns?.connections || []).filter((c) => c.kind === 'text'));
  const connLabel = (c) => c.provider === 'ollama'
    ? `${c.id} · local` : `${c.id} · ${c.provider}`;
  let connItems = $derived([{ value: '', label: 'Active text connection' },
    ...textConns.map((c) => ({ value: c.id, label: connLabel(c) }))]);
  let providerValue = $derived(activePreset?.connection || '');

  // Models available on the active preset's chosen connection.
  let connModels = $state([]);
  let connModelsFor = null;
  let connModelItems = $derived(connModels.map((m) => ({ value: m.id, label: m.name || m.id })));
  $effect(() => {
    const cid = providerValue || null;
    if (cid === connModelsFor) return;
    connModelsFor = cid;
    loadPresetModels(cid);
  });
  async function loadPresetModels(cid) {
    try {
      connModels = cid
        ? (await post(`/connections/${encodeURIComponent(cid)}/test`)).data?.models || []
        : (await get('/text-models')).models || [];
    } catch { connModels = []; }
  }

  async function savePreset(p) {
    const r = await post('/presets', $state.snapshot(p));
    if (r.data?.presets) presetLib = { ...presetLib, presets: r.data.presets };
  }
  function pickProvider(v) {
    if (!activePreset) return;
    activePreset.connection = v;
    activePreset.model = '';        // model belongs to the connection; reset on switch
    savePreset(activePreset);
  }
  function pickPresetModel(v) {
    if (!activePreset) return;
    activePreset.model = v;
    savePreset(activePreset);
  }

  // ── Image preset (the global-default LoRA-stack "look" applied to all image generation) ──
  let imgPresetActive = $state('none');
  async function loadImagePreset() {
    try { imgPresetActive = (await get('/image-presets')).active || 'none'; } catch {}
  }
  async function activateImagePreset(id) {
    const r = await post(`/image-presets/${id}/activate`);
    if (r.data?.active) imgPresetActive = r.data.active;
  }

  // ════════════════════════════ Lorebooks tab ════════════════════════════
  // Surface attachment (the chat/play thread's books) lives in the store; mirror locally.
  let attached = $state([]);
  $effect(() => { attached = configModal.lorebooks ? [...configModal.lorebooks] : []; });
  function setAttached(v) {
    attached = v;
    configModal.lorebooks = v;
    configModal.onLorebooks?.(v);
  }

  // AI augment of a chosen book.
  let books = $state([]);
  let augBook = $state('');
  let augModel = $state('');
  let augInstr = $state('');
  let augBusy = $state(false);
  let augMsg = $state(null);
  let suggestions = $state([]);   // [{ title, keywords, content, _add }]
  let bookItems = $derived(books.map((b) => ({ value: b.id, label: `${b.name}${b.rating === 'nsfw' ? ' · nsfw' : ''}` })));
  // Default the augment model to a DeepSeek option if the connection exposes one.
  let augModelItems = $derived([{ value: '', label: 'Default (chat model)' }, ...modelItems]);

  async function loadBooks() {
    try { const r = await get('/lorebooks'); books = r.books || []; if (!augBook && books[0]) augBook = books[0].id; }
    catch { books = []; }
    if (!augModel) { const ds = textModels.find((m) => /deepseek/i.test(m.id)); if (ds) augModel = ds.id; }
  }
  $effect(() => { if (configModal.open && configModal.tab === 'lorebooks') loadBooks(); });

  async function suggest() {
    if (!augBook || !augInstr.trim()) return;
    augBusy = true; augMsg = { text: 'Thinking…' }; suggestions = [];
    const r = await post(`/lorebooks/${augBook}/augment`, { instruction: augInstr.trim(), model: augModel || undefined });
    augBusy = false;
    if (r.data?.suggestions) {
      suggestions = r.data.suggestions.map((s) => ({ ...s, _add: true }));
      augMsg = { ok: true, text: `✓ ${suggestions.length} suggested` };
    } else augMsg = { err: true, text: r.data?.error || 'failed' };
  }
  async function addSelected() {
    const picked = suggestions.filter((s) => s._add);
    if (!picked.length) return;
    augMsg = { text: 'Adding…' };
    for (const s of picked) {
      await put(`/lorebooks/${augBook}/entries`, { title: s.title, keywords: s.keywords || [], content: s.content });
    }
    augMsg = { ok: true, text: `✓ added ${picked.length}` };
    suggestions = [];
    augInstr = '';
    await loadBooks();
  }
</script>

<Modal open={configModal.open} onClose={closeConfigModal} flush width="760px" height="min(88vh, 680px)">
      <div class="head">
        <div class="tabs">
          {#each TABS as t}
            <button class="tab" class:on={configModal.tab === t.id} onclick={() => (configModal.tab = t.id)}>{t.label}</button>
          {/each}
        </div>
        <button class="x" onclick={closeConfigModal} title="Close">✕</button>
      </div>

      <div class="body">
        <!-- ══ Configs ══ -->
        {#if configModal.tab === 'configs'}
          <!-- This surface is driven by a PRESET — scoped to its function group. Only show a
               picker when there's a real choice; one option needs no picking. -->
          {#if scopedPresets.length}
            <section class="card presetpick">
              <div class="pphead"><h4>{pickTitle}</h4><a class="liblink" href="/library/presets" onclick={closeConfigModal}>Edit in Library →</a></div>
              {#if scopedPresets.length === 1}
                {@const only = scopedPresets[0]}
                <p class="hint">Using <b>{only.name}</b> — the only {configModal.presetGroup || 'chat'} preset.
                  {#if only.id !== presetLib.active}<button class="link" onclick={() => activatePreset(only.id)}>Activate</button>{/if}
                </p>
              {:else}
                <p class="hint">Drives this surface's model, mode, prompt slots & sampling.</p>
                {#each scopedGroups as g (g.label)}
                  {#if scopedGroups.length > 1}<div class="pgroup">{g.label}</div>{/if}
                  <div class="prow-wrap">
                    {#each g.items as p (p.id)}
                      <button class="prow" class:on={p.id === presetLib.active} onclick={() => activatePreset(p.id)} title={p.description}>
                        <span class="pname">{p.name}</span>
                        {#if p.id === presetLib.active}<span class="badge">active</span>{/if}
                      </button>
                    {/each}
                  </div>
                {/each}
              {/if}
            </section>
          {/if}

          <!-- Provider/model toggle for the active preset — flip cloud ⇄ local here. -->
          {#if activePreset}
            <section class="card presetpick">
              <div class="pphead"><h4>Model</h4><a class="liblink" href="/library/presets" onclick={closeConfigModal}>Edit in Library →</a></div>
              <p class="hint">Provider &amp; model for <b>{activePreset.name}</b>. Pick a <b>· local</b> connection (Ollama) to run on your machine instead of the cloud.</p>
              <div class="erow">
                <label>Provider</label>
                <Combobox items={connItems} value={providerValue} placeholder="Active text connection" onpick={pickProvider} />
              </div>
              <div class="erow">
                <label>Model</label>
                <Combobox items={connModelItems} value={activePreset.model || ''} placeholder={connModelItems.length ? 'Connection default' : 'connect to list models'} onpick={pickPresetModel} />
              </div>
            </section>
          {/if}

          <!-- Image preset: the global-default LoRA-stack "look" for all image generation. -->
          <section class="card presetpick">
            <div class="pphead"><h4>Image preset</h4><a class="liblink" href="/library/image-presets" onclick={closeConfigModal}>Edit in Library →</a></div>
            <p class="hint">The LoRA stack applied to generated images. “None” = the base model as-is.</p>
            <ImagePresetPicker value={imgPresetActive} onchange={activateImagePreset} />
          </section>

        <!-- ══ Lorebooks ══ -->
        {:else if configModal.tab === 'lorebooks'}
          {#if configModal.onLorebooks}
            <section class="card">
              <h4>Attached to this chat</h4>
              <p class="hint">Books retrieved into context for the current chat thread.</p>
              <LorebookPicker value={attached} onchange={setAttached} />
            </section>
          {/if}
          <section class="card">
            <h4>✨ Augment a lorebook</h4>
            <p class="hint">Describe what to add; the model proposes structured entries you can review and save.</p>
            <div class="erow">
              <label>Book</label>
              <Combobox items={bookItems} value={augBook} placeholder="pick a book…" onpick={(v) => (augBook = v)} />
            </div>
            <div class="erow">
              <label>Model</label>
              <Combobox items={augModelItems} value={augModel} placeholder="Default (chat model)" onpick={(v) => (augModel = v)} />
            </div>
            <div class="erow col">
              <label>Request</label>
              <textarea class="fld ta" rows="3" bind:value={augInstr}
                placeholder="e.g. Add three factions vying for control of the northern ports…"></textarea>
            </div>
            <div class="augactions">
              <button class="primary sm" onclick={suggest} disabled={augBusy || !augBook || !augInstr.trim()}>{augBusy ? 'Thinking…' : '✨ Suggest entries'}</button>
              {#if augMsg}<span class="msg" class:ok={augMsg.ok} class:err={augMsg.err}>{augMsg.text}</span>{/if}
            </div>

            {#if suggestions.length}
              <div class="sugs">
                {#each suggestions as s, i (i)}
                  <label class="sug" class:off={!s._add}>
                    <input type="checkbox" bind:checked={s._add} />
                    <div class="sugbody">
                      <div class="sugtitle">{s.title}</div>
                      {#if s.keywords?.length}<div class="sugkeys">{s.keywords.join(', ')}</div>{/if}
                      <div class="sugcontent">{s.content}</div>
                    </div>
                  </label>
                {/each}
                <button class="primary sm" onclick={addSelected}>Add selected to “{books.find((b) => b.id === augBook)?.name || augBook}”</button>
              </div>
            {/if}
          </section>
        {/if}
      </div>
</Modal>

<style>
  .head { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border-soft); }
  .tabs { display: flex; gap: 3px; flex: 1; }
  .tab { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 13px; font-weight: 600; padding: 7px 13px; border-radius: 8px; cursor: pointer; }
  .tab:hover { color: var(--text); background: var(--elev); filter: none; }
  .tab.on { color: #fff; background: var(--elev-2); }
  .x { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 14px; cursor: pointer; padding: 4px 8px; }
  .x:hover { color: var(--text); filter: none; }
  .body { padding: 16px 18px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }

  .card { background: var(--bg); border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; }
  .card h4 { margin: 0 0 2px; font-size: 13px; font-weight: 660; color: var(--text); }
  .card h4 .sub { font-size: 11px; color: var(--faint); font-weight: 400; margin-left: 8px; }
  .card :global(label) { text-transform: none; letter-spacing: 0; }
  .hint { font-size: 12px; color: var(--muted); line-height: 1.5; margin: 0; }
  .link { background: none; border: 0; box-shadow: none; color: var(--accent); cursor: pointer; padding: 0; font: inherit; }
  .msg { font-size: 12px; color: var(--muted); } .msg.ok { color: var(--good); } .msg.err { color: var(--bad); }

  /* Rows (preset picker header + augment fields) */
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .badge { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--good); }
  .erow { display: flex; align-items: center; gap: 10px; }
  .erow.col { flex-direction: column; align-items: stretch; gap: 5px; }
  .erow > label { width: 92px; flex: none; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .erow.col > label { width: auto; }
  .erow .fld, .erow :global(.combo) { flex: 1; min-width: 0; }
  .liblink { display: inline-block; font-size: 12px; color: var(--accent); text-decoration: none; }
  .liblink:hover { text-decoration: underline; }

  /* Chat-preset picker */
  .presetpick { gap: 6px; }
  .pphead { display: flex; align-items: center; gap: 10px; }
  .pphead h4 { flex: 1; margin: 0; }
  .pgroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin-top: 6px; }
  .prow-wrap { display: flex; flex-wrap: wrap; gap: 5px; }
  .prow { display: inline-flex; align-items: center; gap: 6px; padding: 6px 11px; border-radius: 999px; cursor: pointer;
    background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); box-shadow: none; font-size: 12.5px; }
  .prow:hover { background: var(--elev); filter: none; }
  .prow.on { border-color: var(--accent); background: rgba(109,140,255,.12); color: var(--accent); }
  .pname { font-weight: 600; }

  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }
  .primary { background: var(--accent); border: 0; color: #fff; }
  .sm { font-size: 12px; padding: 6px 12px; border-radius: 8px; }

  /* Augment */
  .augactions { display: flex; align-items: center; gap: 10px; }
  .sugs { display: flex; flex-direction: column; gap: 8px; margin-top: 6px; }
  .sug { display: flex; gap: 9px; padding: 9px 11px; border-radius: 9px; background: var(--panel); border: 1px solid var(--border-soft); cursor: pointer; }
  .sug.off { opacity: .5; }
  .sug input { width: 16px; height: 16px; flex: none; margin-top: 3px; }
  .sugbody { flex: 1; min-width: 0; }
  .sugtitle { font-size: 13px; font-weight: 660; color: var(--text); }
  .sugkeys { font-size: 11px; color: var(--accent); font-family: ui-monospace, monospace; margin: 2px 0; }
  .sugcontent { font-size: 12.5px; color: var(--muted); line-height: 1.5; white-space: pre-wrap; }
</style>

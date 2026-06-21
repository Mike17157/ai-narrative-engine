<script>
  // The reusable generation-config modal — opened by the ⚙ at every chat/generation
  // surface. Four tabs fold what used to be scattered across the settings pages
  // (Models, Connections) plus two new point-of-use concerns: named chat Configs and
  // Lorebook attachment/AI-augment. Mounted once in the root layout; driven by the
  // configModal store.
  import { onMount } from 'svelte';
  import { get, post, put, del } from '$lib/api.js';
  import { refreshAll } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { configModal, closeConfigModal } from '$lib/configModal.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import LorebookPicker from '$lib/components/shared/LorebookPicker.svelte';
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

  onMount(async () => { await refreshAll(); await loadTextModels(); await loadScripts(); await loadConfigs(); await loadPresets(); });
  // Refresh model lists whenever the modal (re)opens, so a just-saved connection shows up.
  $effect(() => { if (configModal.open) { refreshAll(); loadTextModels(); } });

  // ════════════════════════════ Configs tab ════════════════════════════
  // A config = a chat with rules: model + system + lorebooks + an optional SCRIPT (the
  // pipeline action it encodes). Free configs (script '') drive the main chat; script
  // configs drive the story-builder stages / prompt configs / image roles.
  let lib = $state({ active: 'default', configs: [] });
  let scripts = $state([]);
  let selId = $state(null);
  let sel = $derived(lib.configs.find((c) => c.id === selId) || null);
  let cfgSnap = null;
  let cfgTimer = null;

  let scriptItems = $derived(scripts.map((s) => ({ value: s.id, label: s.label, group: s.group })));
  const scriptOf = (c) => scripts.find((s) => s.id === (c.script || ''));
  let selIsImage = $derived((sel?.script || '').startsWith('image:'));
  let selIsScript = $derived(!!(sel?.script));

  // Address mode — how the model is framed. '' = auto (assist for script-bound flows,
  // roleplay for free chat); explicit overrides win.
  const MODES = [
    { v: '', label: 'Auto' },
    { v: 'roleplay', label: 'Roleplay' },
    { v: 'assist', label: 'Assist' },
  ];
  let resolvedMode = $derived(sel?.mode || (selIsScript ? 'assist' : 'roleplay'));
  const CONTEXT_FIELDS = [
    { k: 'history_turns', label: 'History turns', step: 1, min: 0, ph: 'all', hint: 'Keep only the last N messages (0 = all)' },
    { k: 'lore_top_k', label: 'Lore entries', step: 1, min: 0, ph: '6', hint: 'How many lorebook entries to retrieve into context' },
  ];
  let showCtx = $state(false);

  // Advanced inference controls (forwarded to the provider; blank = model default).
  const PARAM_FIELDS = [
    { k: 'temperature', label: 'Temperature', step: 0.05, min: 0, max: 2, ph: '1.0', hint: 'Higher = more random / creative' },
    { k: 'top_p', label: 'Top P', step: 0.05, min: 0, max: 1, ph: '1.0', hint: 'Nucleus sampling — cumulative probability cutoff' },
    { k: 'top_k', label: 'Top K', step: 1, min: 0, ph: 'off', hint: 'Sample only from the K most likely tokens (0 = off)' },
    { k: 'max_tokens', label: 'Max tokens', step: 1, min: 1, ph: 'default', hint: 'Cap on reply length' },
    { k: 'frequency_penalty', label: 'Freq. penalty', step: 0.1, min: -2, max: 2, ph: '0', hint: 'Penalize tokens by how often they appeared' },
    { k: 'presence_penalty', label: 'Presence penalty', step: 0.1, min: -2, max: 2, ph: '0', hint: 'Penalize tokens that appeared at all → new topics' },
  ];
  let showAdv = $state(false);
  let paramCount = $derived(Object.values(sel?.params || {}).filter((v) => v !== '' && v != null).length);

  // Free-chat is driven by PRESETS now (not chat-configs). The Chat group is gone from the
  // config editor; what remains are the pipeline stage configs (Story builder/Prompts/Image roles).
  const GROUP_ORDER = ['Story builder', 'Prompts', 'Image roles'];
  let grouped = $derived.by(() => {
    const m = {};
    for (const c of lib.configs) { const g = scriptOf(c)?.group || 'Chat'; if (g === 'Chat') continue; (m[g] ||= []).push(c); }
    return GROUP_ORDER.filter((g) => m[g]).map((g) => ({ group: g, configs: m[g] }));
  });

  // ── Chat preset picker ──
  let presetLib = $state({ active: '', presets: [] });
  let presetGroups = $derived.by(() => {
    const rp = presetLib.presets.filter((p) => (p.mode || '') === 'roleplay');
    const as = presetLib.presets.filter((p) => (p.mode || '') !== 'roleplay');
    return [{ label: 'Roleplay', items: rp }, { label: 'Assist', items: as }].filter((g) => g.items.length);
  });
  async function loadPresets() {
    try { presetLib = await get('/presets'); } catch { /* none */ }
  }
  async function activatePreset(id) {
    const r = await post(`/presets/${id}/activate`);
    if (r.data?.active) presetLib = { ...presetLib, active: r.data.active };
  }

  async function loadConfigs() {
    lib = await get('/chat-configs');
    // Default selection is a pipeline (script-bound) config; free-chat configs are retired.
    if (!selId || !lib.configs.some((c) => c.id === selId)) selId = (lib.configs.find((c) => c.script) || {}).id || null;
    cfgSnap = sel ? JSON.stringify(sel) : null;
  }
  async function loadScripts() {
    try { const r = await get('/chat-scripts'); scripts = r.scripts || []; } catch { scripts = []; }
  }

  // Debounced autosave of the selected config.
  $effect(() => {
    if (!sel) return;
    const cur = JSON.stringify(sel);
    if (cfgSnap === null) { cfgSnap = cur; return; }
    if (cur === cfgSnap) return;
    clearTimeout(cfgTimer);
    cfgTimer = setTimeout(saveConfig, 600);
  });

  async function saveConfig() {
    if (!sel) return;
    const r = await post('/chat-configs', $state.snapshot(sel));
    if (r.data?.configs) { lib = { active: r.data.active, configs: r.data.configs }; cfgSnap = JSON.stringify(sel); }
  }
  async function newConfig() {
    const r = await post('/chat-configs', { name: 'New config', model: '', system: '', lorebooks: [] });
    if (r.data?.configs) { lib = { active: r.data.active, configs: r.data.configs }; selId = r.data.id; cfgSnap = JSON.stringify(sel); }
  }
  async function deleteConfig() {
    if (!sel || lib.configs.length <= 1) return;
    if (!await askConfirm({ title: `Delete “${sel.name}”?`, confirmLabel: 'Delete', danger: true })) return;
    const r = await del(`/chat-configs/${sel.id}`);
    if (r?.configs) { lib = { active: r.active, configs: r.configs }; selId = lib.active; cfgSnap = JSON.stringify(sel); }
  }
  async function activateConfig() {
    if (!sel) return;
    const r = await post(`/chat-configs/${sel.id}/activate`);
    if (r.data?.active) lib.active = r.data.active;
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
          <!-- Chat is driven by a PRESET — pick which one is active here; edit it in Library. -->
          <section class="card presetpick">
            <div class="pphead"><h4>Chat preset</h4><a class="liblink" href="/library/presets" onclick={closeConfigModal}>Edit in Library →</a></div>
            <p class="hint">The preset drives this chat's model, mode, prompt slots & sampling.</p>
            {#each presetGroups as g (g.label)}
              <div class="pgroup">{g.label}</div>
              <div class="prow-wrap">
                {#each g.items as p (p.id)}
                  <button class="prow" class:on={p.id === presetLib.active} onclick={() => activatePreset(p.id)} title={p.description}>
                    <span class="pname">{p.name}</span>
                    {#if p.id === presetLib.active}<span class="badge">active</span>{/if}
                  </button>
                {/each}
              </div>
            {/each}
          </section>

          {#if grouped.length}
          <div class="pipehd">Pipeline stages <span class="lo">— the builder/prompt jobs (presets coming)</span></div>
          <div class="cfgwrap">
            <div class="cfglist">
              {#each grouped as grp (grp.group)}
                <div class="cfggroup">{grp.group}</div>
                {#each grp.configs as c (c.id)}
                  <button class="cfgrow" class:on={c.id === selId} onclick={() => { selId = c.id; cfgSnap = JSON.stringify(c); }}>
                    <span class="cfgname">{c.name || c.id}</span>
                  </button>
                {/each}
              {/each}
            </div>

            {#if sel}
              <div class="cfgedit">
                <div class="erow">
                  <label>Name</label>
                  <input class="fld" bind:value={sel.name} />
                </div>
                <div class="erow">
                  <label>Script</label>
                  <Combobox items={scriptItems} value={sel.script || ''} placeholder="Free chat" onpick={(v) => (sel.script = v)} />
                </div>

                {#if selIsImage}
                  <div class="erow">
                    <label>Workflow</label>
                    <Combobox items={imageItems} value={sel.workflow || ''} placeholder="pick a workflow…" onpick={(v) => (sel.workflow = v)} />
                  </div>
                  <p class="hint">This image role renders with the chosen ComfyUI workflow.</p>
                {:else}
                  <div class="erow">
                    <label>Model</label>
                    <Combobox items={[{ value: '', label: 'Use active chat model' }, ...modelItems]}
                      value={sel.model} placeholder="Use active chat model" onpick={(v) => (sel.model = v)} />
                  </div>
                  <div class="erow">
                    <label title="Roleplay = the model becomes the character. Assist = it helps you build the document and never roleplays.">Address</label>
                    <div class="seg">
                      {#each MODES as m}
                        <button class="segbtn" class:on={(sel.mode || '') === m.v} onclick={() => (sel.mode = m.v)}>{m.label}</button>
                      {/each}
                    </div>
                  </div>
                  <p class="hint">
                    {#if resolvedMode === 'assist'}<b>Assist</b> — a craft collaborator that develops the document with you; never roleplays a character.
                    {:else}<b>Roleplay</b> — the model speaks in-character.{/if}
                    {#if !sel.mode}<span class="lo"> (auto from script)</span>{/if}
                  </p>
                  <div class="erow col">
                    <label>System prompt <span class="lo">— the rules</span></label>
                    <textarea class="fld ta" rows="6" bind:value={sel.system}
                      placeholder="Standing instructions for this config…"></textarea>
                  </div>
                  <div class="erow col">
                    <label>Lorebooks <span class="lo">— more rules, folded into the prompt</span></label>
                    <LorebookPicker value={sel.lorebooks} onchange={(v) => (sel.lorebooks = v)} />
                  </div>

                  <div class="adv">
                    <button class="advhdr" onclick={() => (showAdv = !showAdv)}>
                      <span class="advarr" class:open={showAdv}>▸</span> Inference
                      {#if paramCount}<span class="advcount">{paramCount} set</span>{/if}
                      <span class="lo">— sampling (blank = model default; OpenAI-compatible connections)</span>
                    </button>
                    {#if showAdv}
                      <div class="advgrid">
                        {#each PARAM_FIELDS as p (p.k)}
                          <div class="pfield">
                            <label title={p.hint}>{p.label}</label>
                            <input class="fld" type="number" step={p.step} min={p.min} max={p.max}
                              placeholder={p.ph} bind:value={sel.params[p.k]} />
                          </div>
                        {/each}
                      </div>
                      <button class="ghost xsm" onclick={() => (sel.params = {})}>Reset to defaults</button>
                    {/if}
                  </div>

                  <div class="adv">
                    <button class="advhdr" onclick={() => (showCtx = !showCtx)}>
                      <span class="advarr" class:open={showCtx}>▸</span> Context
                      <span class="lo">— how much history & lore to feed in (blank = default)</span>
                    </button>
                    {#if showCtx}
                      <div class="advgrid">
                        {#each CONTEXT_FIELDS as p (p.k)}
                          <div class="pfield">
                            <label title={p.hint}>{p.label}</label>
                            <input class="fld" type="number" step={p.step} min={p.min}
                              placeholder={p.ph} bind:value={sel.context[p.k]} />
                          </div>
                        {/each}
                        <label class="pfield ckrow" title="Include the character persona as reference context (Assist mode)">
                          <input type="checkbox" checked={sel.context.include_persona !== false}
                            onchange={(e) => (sel.context.include_persona = e.currentTarget.checked)} />
                          <span>Persona context</span>
                        </label>
                      </div>
                    {/if}
                  </div>
                {/if}

                <div class="cfgactions">
                  {#if !selIsScript && sel.id !== lib.active}<button class="primary sm" onclick={activateConfig}>Make active</button>{/if}
                  {#if !selIsScript}<button class="ghost sm danger" onclick={deleteConfig} disabled={grouped[0]?.configs.length <= 1}>Delete</button>{/if}
                  <span class="hint">Auto-saves{selIsScript ? ' · drives the pipeline' : ''}</span>
                  <a class="liblink sm" href="/library/presets" onclick={closeConfigModal}>Presets & infra in Library →</a>
                </div>
              </div>
            {/if}
          </div>
          {/if}

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

  /* Configs */
  .cfgwrap { display: flex; gap: 14px; align-items: flex-start; }
  .cfglist { width: 188px; flex: none; display: flex; flex-direction: column; gap: 3px; max-height: 62vh; overflow-y: auto; }
  .cfggroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 9px 4px 2px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .cfgrow { display: flex; align-items: center; gap: 6px; text-align: left; padding: 8px 10px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; box-shadow: none; }
  .cfgrow:hover { background: var(--elev); filter: none; }
  .cfgrow.on { border-color: var(--accent); background: var(--elev-2); }
  .cfgname { flex: 1; font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--good); }
  .newcfg { margin-top: 4px; padding: 8px 10px; border-radius: 8px; background: none; border: 1px dashed var(--border); color: var(--muted); font-size: 12.5px; cursor: pointer; box-shadow: none; }
  .newcfg:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .cfgedit { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 11px; }
  .erow { display: flex; align-items: center; gap: 10px; }
  .erow.col { flex-direction: column; align-items: stretch; gap: 5px; }
  .erow > label { width: 92px; flex: none; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .erow.col > label { width: auto; }
  .erow .fld, .erow :global(.combo) { flex: 1; min-width: 0; }
  .cfgactions { display: flex; align-items: center; gap: 10px; margin-top: 2px; }
  .liblink { display: inline-block; font-size: 12px; color: var(--accent); text-decoration: none; }
  .liblink:hover { text-decoration: underline; }
  .liblink.sm { margin-left: auto; }

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
  .pipehd { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); margin: 4px 0 2px; }

  /* Address-mode segmented control */
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .segbtn { background: var(--bg); border: 0; box-shadow: none; color: var(--muted); font-size: 12px; font-weight: 600;
    padding: 6px 13px; cursor: pointer; border-right: 1px solid var(--border-soft); }
  .segbtn:last-child { border-right: 0; }
  .segbtn:hover { color: var(--text); background: var(--elev); filter: none; }
  .segbtn.on { color: #fff; background: var(--accent); }
  .ckrow { flex-direction: row !important; align-items: center; gap: 7px; }
  .ckrow input { width: 15px; height: 15px; }
  .ckrow span { font-size: 11.5px; color: var(--muted); }

  /* Advanced inference */
  .adv { border-top: 1px solid var(--border-soft); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
  .advhdr { display: flex; align-items: center; gap: 7px; background: none; border: 0; box-shadow: none; padding: 0; cursor: pointer;
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .advhdr:hover { color: var(--text); filter: none; }
  .advarr { transition: transform .15s; font-size: 10px; }
  .advarr.open { transform: rotate(90deg); }
  .advcount { font-size: 9.5px; font-weight: 700; color: var(--accent); background: rgba(109,140,255,.14);
    border-radius: 999px; padding: 1px 7px; text-transform: none; letter-spacing: 0; }
  .advgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px 12px; }
  .pfield { display: flex; flex-direction: column; gap: 3px; }
  .pfield label { font-size: 10.5px; font-weight: 600; color: var(--muted); text-transform: none; letter-spacing: 0; cursor: help; }
  .pfield .fld { padding: 6px 8px; font-size: 12.5px; }
  .xsm { font-size: 11px; padding: 4px 9px; border-radius: 7px; align-self: flex-start; }

  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }
  select.fld { cursor: pointer; }
  .primary { background: var(--accent); border: 0; color: #fff; }
  .sm { font-size: 12px; padding: 6px 12px; border-radius: 8px; }
  .danger:hover { color: var(--bad); border-color: var(--bad); filter: none; }

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

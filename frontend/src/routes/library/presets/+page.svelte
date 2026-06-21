<script>
  import { onMount } from 'svelte';
  import { get, post, del, patch } from '$lib/api.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ConnectionPanel from '$lib/components/ConnectionPanel.svelte';
  import { autosize } from '$lib/autosize.js';
  import { app, refreshAll } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { openBrowse } from '$lib/browse.svelte.js';

  // Presets manager — the MODEL side of every function: a connection + model + address mode
  // + base system + inference params. Connections are part of the preset (set up right here);
  // functions reach a preset only through their lorebook's binding (Function → Lorebook →
  // Preset). Sibling of the Lorebooks manager; everything auto-saves (debounced).

  let presets = $state([]);
  let selId = $state(null);
  let sel = $derived(presets.find((p) => p.id === selId) || null);
  let snap = null, timer = null;

  // Connections (the API endpoints) live here — a preset picks one, then a model from it.
  // Sourced from the global store so ConnectionPanel edits reflect immediately.
  let connections = $derived((app.conns?.connections || []).filter((c) => c.kind === 'text'));
  let connItems = $derived([{ value: '', label: 'Active text connection' },
    ...connections.map((c) => ({ value: c.id, label: `${c.id} · ${c.provider}` }))]);
  let manageConn = $state(false);

  // Models available on the selected preset's connection (per-connection, not the global one).
  let connModels = $state([]);
  let modelItems = $derived([{ value: '', label: connModels.length ? 'Connection default' : 'Use connection default' },
    ...connModels.map((m) => ({ value: m.id, label: m.name || m.id }))]);
  let modelsLoading = $state(false);

  // Refresh the model list whenever the chosen connection changes (uses the saved key,
  // server-side; falls back to the active connection's models when none is picked).
  let modelsFor = null;
  $effect(() => {
    const cid = sel?.connection ?? null;
    if (cid === modelsFor) return;
    modelsFor = cid;
    loadConnModels(cid);
  });
  async function loadConnModels(cid) {
    modelsLoading = true;
    try {
      if (cid) connModels = (await post(`/connections/${encodeURIComponent(cid)}/test`)).data?.models || [];
      else connModels = (await get('/text-models')).models || [];
    } catch { connModels = []; }
    modelsLoading = false;
  }

  // Which lorebooks bind to each preset (the reverse of the binding) — shown so you can see
  // at a glance what a preset drives.
  let books = $state([]);
  let boundBooks = $derived((p) => books.filter((b) => b.preset === p));

  // Group presets by function, ordered by `order` (the pipeline flow); ungrouped trail last.
  let groupedPresets = $derived.by(() => {
    const m = new Map();
    for (const p of [...presets].sort((a, b) => (a.order || 0) - (b.order || 0))) {
      const g = p.group || 'Other';
      if (!m.has(g)) m.set(g, []);
      m.get(g).push(p);
    }
    // Order groups by their lowest member order so the flow reads top-to-bottom.
    return [...m.entries()]
      .map(([group, items]) => ({ group, items, ord: Math.min(...items.map((p) => p.order || 0)) }))
      .sort((a, b) => a.ord - b.ord);
  });

  const MODES = [
    { v: '', label: 'Auto', hint: 'Roleplay for free chat, Assist for function flows' },
    { v: 'roleplay', label: 'Roleplay', hint: 'Speaks in-character' },
    { v: 'assist', label: 'Assist', hint: 'A craft collaborator; never roleplays' },
  ];
  // Every OpenRouter-modifiable sampling knob (blank = model/provider default).
  const PARAM_FIELDS = [
    { k: 'temperature', label: 'Temperature', step: 0.05, min: 0, max: 2, ph: '1.0', hint: 'Randomness' },
    { k: 'top_p', label: 'Top P', step: 0.05, min: 0, max: 1, ph: '1.0', hint: 'Nucleus cutoff' },
    { k: 'top_k', label: 'Top K', step: 1, min: 0, ph: 'off', hint: 'Keep only K likeliest tokens' },
    { k: 'top_a', label: 'Top A', step: 0.01, min: 0, max: 1, ph: 'off', hint: 'Dynamic cutoff scaled by the top token' },
    { k: 'min_p', label: 'Min P', step: 0.01, min: 0, max: 1, ph: 'off', hint: 'Drop tokens below this share of the top token' },
    { k: 'max_tokens', label: 'Max tokens', step: 1, min: 1, ph: 'default', hint: 'Reply length cap' },
    { k: 'frequency_penalty', label: 'Freq. penalty', step: 0.1, min: -2, max: 2, ph: '0', hint: 'Penalize by frequency' },
    { k: 'presence_penalty', label: 'Presence penalty', step: 0.1, min: -2, max: 2, ph: '0', hint: 'Penalize any reuse → new topics' },
    { k: 'repetition_penalty', label: 'Repetition penalty', step: 0.05, min: 0, max: 2, ph: '1.0', hint: 'Discourage repeats' },
    { k: 'seed', label: 'Seed', step: 1, min: 0, ph: 'random', hint: 'Fixed seed → reproducible output' },
  ];

  // `stop` is stored as a list; edit it as a comma-separated string.
  let stopStr = $state('');
  let stopSeed = null;
  $effect(() => {
    if (sel && stopSeed !== sel.id) { stopStr = (sel.stop || []).join(', '); stopSeed = sel.id; }
  });
  function commitStop() {
    if (sel) sel.stop = stopStr.split(',').map((s) => s.trim()).filter(Boolean).slice(0, 4);
  }

  onMount(load);
  async function load() {
    const [pr, bk] = await Promise.all([get('/presets'), get('/lorebooks').catch(() => ({}))]);
    presets = pr.presets || [];
    books = bk.books || [];
    await refreshAll();   // populate app.conns so the connection picker is ready
    if (!selId || !presets.some((p) => p.id === selId)) selId = presets[0]?.id || null;
    snap = sel ? JSON.stringify(sel) : null;
  }

  // Debounced autosave of the selected preset.
  $effect(() => {
    if (!sel) return;
    const cur = JSON.stringify(sel);
    if (snap === null) { snap = cur; return; }
    if (cur === snap) return;
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  });
  async function save() {
    if (!sel) return;
    const r = await post('/presets', $state.snapshot(sel));
    if (r.data?.presets) { presets = r.data.presets; snap = JSON.stringify(sel); }
  }
  async function newPreset() {
    const r = await post('/presets', { name: 'New preset', mode: '' });
    if (r.data?.presets) { presets = r.data.presets; selId = r.data.id; snap = JSON.stringify(presets.find((p) => p.id === selId)); }
  }
  async function deletePreset() {
    if (!sel || sel.id === 'default') return;
    if (!await askConfirm({ title: `Delete “${sel.name}”?`, confirmLabel: 'Delete', danger: true })) return;
    const r = await del(`/presets/${sel.id}`);
    if (r?.presets) { presets = r.presets; selId = presets[0]?.id || null; snap = sel ? JSON.stringify(sel) : null; }
  }
  function pick(p) { selId = p.id; snap = JSON.stringify(p); }
  const paramCount = (p) => Object.values(p?.params || {}).filter((v) => v !== '' && v != null).length;

  // Edit which lorebooks bind to THIS preset via the reusable browse modal. The binding
  // lives on each book (book.preset); confirming diffs the selection and PATCHes the books
  // that changed — added → this preset, removed → '' (cleared).
  function editBoundBooks() {
    const before = boundBooks(sel.id).map((b) => b.id);
    openBrowse({
      kind: 'lorebook', multi: true, value: before,
      title: `Lorebooks for “${sel.name}”`,
      onConfirm: async (ids) => {
        const add = ids.filter((id) => !before.includes(id));
        const remove = before.filter((id) => !ids.includes(id));
        await Promise.all([
          ...add.map((id) => patch(`/lorebooks/${encodeURIComponent(id)}`, { preset: sel.id })),
          ...remove.map((id) => patch(`/lorebooks/${encodeURIComponent(id)}`, { preset: '' })),
        ]);
        books = (await get('/lorebooks')).books || [];   // refresh so chips reflect the new bindings
      },
    });
  }
</script>

<div class="wrap">
  <div class="list">
    <div class="lhead">Presets <span class="lo">— grouped by function, in pipeline order</span></div>
    {#each groupedPresets as grp (grp.group)}
      <div class="pgroup">{grp.group}</div>
      {#each grp.items as p (p.id)}
        <button class="row" class:on={p.id === selId} onclick={() => pick(p)}>
          <span class="nm">{p.name || p.id}</span>
          <span class="tags">
            <span class="mtag" class:assist={(p.mode || '') === 'assist'} class:rp={(p.mode || '') === 'roleplay'}>
              {p.mode || 'auto'}
            </span>
            {#if boundBooks(p.id).length}<span class="btag" title="lorebooks bound to this preset">{boundBooks(p.id).length}📚</span>{/if}
          </span>
        </button>
      {/each}
    {/each}
    <button class="new" onclick={newPreset}>＋ New preset</button>
  </div>

  {#if sel}
    <div class="edit">
      <div class="erow">
        <label>Name</label>
        <input class="fld" bind:value={sel.name} />
      </div>
      <div class="erow">
        <label>Group</label>
        <input class="fld" list="preset-groups" bind:value={sel.group} placeholder="e.g. Story arc" title="Buckets presets in the list & picker" />
        <label class="ordl" title="Sort order within the flow (lower = earlier)">order
          <input class="fld onum" type="number" step="1" bind:value={sel.order} />
        </label>
      </div>
      <datalist id="preset-groups">
        {#each [...new Set(presets.map((p) => p.group).filter(Boolean))] as g}<option value={g}></option>{/each}
      </datalist>
      <div class="erow col">
        <label>Description <span class="lo">— what this preset is for</span></label>
        <textarea class="fld ta" rows="1" use:autosize={sel.description} bind:value={sel.description}></textarea>
      </div>
      <div class="erow">
        <label>Connection</label>
        <Combobox items={connItems} value={sel.connection || ''} placeholder="Active text connection"
          onpick={(v) => { sel.connection = v; sel.model = ''; }} />
        <button class="mng" class:on={manageConn} onclick={() => (manageConn = !manageConn)} title="Add / edit API connections">⚙ Manage</button>
      </div>
      {#if manageConn}
        <div class="connmng">
          <ConnectionPanel kind="text" />
        </div>
      {/if}
      <div class="erow">
        <label>Model</label>
        <Combobox items={modelItems} value={sel.model} placeholder={modelsLoading ? 'loading…' : 'Connection default'} onpick={(v) => (sel.model = v)} />
      </div>
      <div class="erow">
        <label title="How the model is framed.">Address</label>
        <div class="seg">
          {#each MODES as m}
            <button class="segbtn" class:on={(sel.mode || '') === m.v} title={m.hint} onclick={() => (sel.mode = m.v)}>{m.label}</button>
          {/each}
        </div>
      </div>
      <div class="erow col">
        <label>System prompt <span class="lo">— top of the prompt; standing rules (lorebooks add more on top)</span></label>
        <textarea class="fld ta" rows="1" use:autosize={sel.system} bind:value={sel.system} placeholder="Optional standing instructions for this preset…"></textarea>
      </div>
      <div class="erow col">
        <label>Author's note <span class="lo">— injected near the end of history; strong steer on tone/direction</span></label>
        <textarea class="fld ta" rows="1" use:autosize={sel.author_note} bind:value={sel.author_note} placeholder="e.g. Keep the pace tense and the prose sensory."></textarea>
        <label class="depth">depth
          <input class="fld dnum" type="number" min="0" step="1" bind:value={sel.author_depth} title="How many messages from the end to inject the note" />
          <span class="lo">messages from the end</span>
        </label>
      </div>
      <div class="erow col">
        <label>Post-history instructions <span class="lo">— placed LAST, just before the reply; strongest steer</span></label>
        <textarea class="fld ta" rows="1" use:autosize={sel.post_history} bind:value={sel.post_history} placeholder="e.g. Stay in character. Reply in 2–3 paragraphs, present tense."></textarea>
      </div>

      <div class="adv">
        <div class="advhdr">Inference {#if paramCount(sel)}<span class="cnt">{paramCount(sel)} set</span>{/if}<span class="lo">— every OpenRouter knob; blank = model default</span></div>
        <div class="grid">
          {#each PARAM_FIELDS as f (f.k)}
            <div class="pf">
              <label title={f.hint}>{f.label}</label>
              <input class="fld" type="number" step={f.step} min={f.min} max={f.max} placeholder={f.ph} bind:value={sel.params[f.k]} />
            </div>
          {/each}
          <div class="pf">
            <label title="Map to OpenRouter reasoning:{'{'}effort{'}'} — only affects reasoning models">Reasoning</label>
            <select class="fld" bind:value={sel.reasoning_effort}>
              <option value="">default</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </div>
        </div>
        <div class="erow col" style="margin-top:9px">
          <label title="Up to 4 sequences that halt generation">Stop sequences <span class="lo">— comma-separated</span></label>
          <input class="fld" bind:value={stopStr} onblur={commitStop} placeholder='e.g. \n\n, ###, "User:"' />
        </div>
      </div>

      <div class="bound">
        <div class="bhd">Bound lorebooks <span class="lo">— functions reach this preset through these books</span>
          <button class="addb" onclick={editBoundBooks}>＋ Browse lorebooks</button>
        </div>
        {#if boundBooks(sel.id).length}
          <div class="chips">
            {#each boundBooks(sel.id) as b}<a class="chip" href="/library/lorebooks?book={b.id}">{b.name || b.id}</a>{/each}
          </div>
        {:else}
          <p class="lo">No lorebook points here yet — click <b>Browse lorebooks</b> to bind some.</p>
        {/if}
      </div>

      <div class="acts">
        <span class="hint">Auto-saves</span>
        {#if sel.id !== 'default'}<button class="ghost danger" onclick={deletePreset}>Delete</button>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .wrap { display: flex; gap: 16px; align-items: flex-start; max-width: 1100px; }
  .list { width: 230px; flex: none; display: flex; flex-direction: column; gap: 4px; }
  .lhead { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 4px 2px 6px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .row { display: flex; align-items: center; gap: 8px; text-align: left; padding: 9px 11px; border-radius: 9px;
    background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; box-shadow: none; }
  .row:hover { background: var(--elev); filter: none; }
  .row.on { border-color: var(--accent); background: var(--elev-2); }
  .nm { flex: 1; font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tags { display: flex; align-items: center; gap: 5px; flex: none; }
  .pgroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 10px 4px 3px; }
  .pgroup:first-child { padding-top: 2px; }
  .ordl { display: inline-flex; align-items: center; gap: 6px; flex: none; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .onum { width: 60px; flex: none; padding: 6px 8px; font-size: 12.5px; }
  .mtag { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--faint);
    background: var(--elev); border-radius: 999px; padding: 1px 7px; }
  .mtag.assist { color: var(--accent); background: rgba(109,140,255,.14); }
  .mtag.rp { color: var(--good); background: rgba(100,210,130,.12); }
  .btag { font-size: 9.5px; color: var(--faint); }
  .new { margin-top: 4px; padding: 9px 11px; border-radius: 9px; background: none; border: 1px dashed var(--border);
    color: var(--muted); font-size: 12.5px; cursor: pointer; box-shadow: none; }
  .new:hover { color: var(--accent); border-color: var(--accent); filter: none; }

  .edit { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
  .erow { display: flex; align-items: center; gap: 10px; }
  .erow.col { flex-direction: column; align-items: stretch; gap: 5px; }
  .erow > label { width: 96px; flex: none; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .erow.col > label { width: auto; }
  .erow .fld, .erow :global(.combo) { flex: 1; min-width: 0; }
  /* Stacked (column) rows put fields below the label — they're full-width, not flex items,
     so the textarea's own (autosized) height wins instead of being collapsed by flex. */
  .erow.col .fld { flex: none; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }

  .depth { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .3px; color: var(--muted); margin-top: 2px; }
  .dnum { width: 64px; flex: none; padding: 5px 8px; font-size: 12.5px; }
  .mng { flex: none; font-size: 11.5px; padding: 6px 11px; border-radius: 8px; background: var(--elev);
    border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; box-shadow: none; }
  .mng:hover, .mng.on { color: var(--accent); border-color: var(--accent); filter: none; }
  .connmng { border: 1px solid var(--border-soft); border-radius: 10px; padding: 12px; background: var(--bg); }
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .segbtn { background: var(--bg); border: 0; box-shadow: none; color: var(--muted); font-size: 12px; font-weight: 600;
    padding: 7px 14px; cursor: pointer; border-right: 1px solid var(--border-soft); }
  .segbtn:last-child { border-right: 0; }
  .segbtn:hover { color: var(--text); background: var(--elev); filter: none; }
  .segbtn.on { color: #fff; background: var(--accent); }

  .adv { border-top: 1px solid var(--border-soft); padding-top: 12px; display: flex; flex-direction: column; gap: 9px; }
  .advhdr { display: flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .cnt { font-size: 9.5px; font-weight: 700; color: var(--accent); background: rgba(109,140,255,.14); border-radius: 999px; padding: 1px 7px; text-transform: none; letter-spacing: 0; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px 12px; }
  .pf { display: flex; flex-direction: column; gap: 3px; }
  .pf label { font-size: 10.5px; font-weight: 600; color: var(--muted); text-transform: none; letter-spacing: 0; }
  .pf .fld { padding: 6px 8px; font-size: 12.5px; }

  .bound { border-top: 1px solid var(--border-soft); padding-top: 12px; display: flex; flex-direction: column; gap: 7px; }
  .bhd { display: flex; align-items: center; gap: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .addb { margin-left: auto; font-size: 11.5px; text-transform: none; letter-spacing: 0; font-weight: 600;
    padding: 4px 10px; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; box-shadow: none; }
  .addb:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: rgba(109,140,255,.1);
    border: 1px solid rgba(109,140,255,.25); color: var(--accent); text-decoration: none; }
  .chip:hover { background: rgba(109,140,255,.18); }
  .bound a { color: var(--accent); }

  .acts { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .hint { font-size: 12px; color: var(--muted); }
  .ghost { font-size: 12px; padding: 6px 12px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; box-shadow: none; }
  .danger:hover { color: var(--bad); border-color: var(--bad); filter: none; }
</style>

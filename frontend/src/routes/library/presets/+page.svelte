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
  // `tools` is the native tool-calling capability (true/false from OpenRouter/Anthropic, null =
  // unknown, e.g. local Ollama). Agents & pipeline presets call functions as tools, so badge
  // incapable models and offer a filter — narration presets can still use any model.
  let connModels = $state([]);
  let toolsOnly = $state(false);
  let modelItems = $derived([{ value: '', label: connModels.length ? 'Connection default' : 'Use connection default' },
    ...connModels
      .filter((m) => !toolsOnly || m.tools !== false)
      .map((m) => ({ value: m.id, label: (m.name || m.id) + (m.tools === false ? ' · no tools' : '') }))]);
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

  // Image WORKFLOWS (a preset bundles one — not an image "model"). Each runs local or cloud.
  let imageWorkflows = $state([]);
  let imageWfItems = $derived([{ value: '', label: 'None — use the active image connection' },
    ...imageWorkflows.map((m) => ({ value: m.key, label: m.key }))]);
  const IMG_PROVIDERS = [
    { v: '', label: 'Auto', hint: 'Use the global per-workflow default' },
    { v: 'local', label: 'Local', hint: 'Run on local ComfyUI' },
    { v: 'cloud', label: 'Cloud', hint: 'Run on RunPod serverless' },
  ];

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
  // PRIMARY presets are the ones you actually pick to talk to: Free Chat, the Agents (story
  // partners that can call their scripts) and Default. Everything else — image-prompt utilities
  // and the headless pipeline stages — is engine internals, tucked under an advanced section.
  const _isPrimary = (g) => g === 'Author' || g === 'Narrative' || g === 'Dungeon Master' || g === 'Agents';
  let chatGroups = $derived(groupedPresets.filter((g) => _isPrimary(g.group)));
  let pipelineGroups = $derived(groupedPresets.filter((g) => !_isPrimary(g.group)));
  let showPipeline = $state(false);
  // Advanced editor section (author's note + post-history + inference params) — collapsed by
  // default so the common fields (model / system / lorebooks) aren't buried under tuning knobs.
  let showAdvanced = $state(false);

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
    const [pr, bk, md] = await Promise.all([
      get('/presets'), get('/lorebooks').catch(() => ({})), get('/models').catch(() => ({}))]);
    presets = pr.presets || [];
    books = bk.books || [];
    imageWorkflows = md.image || [];
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
    const r = await post('/presets', { name: 'New agent', mode: '', group: 'Agents' });
    if (r.data?.presets) { presets = r.data.presets; selId = r.data.id; snap = JSON.stringify(presets.find((p) => p.id === selId)); }
  }
  async function deletePreset() {
    if (!sel || sel.id === 'default') return;
    if (!await askConfirm({ title: `Delete “${sel.name}”?`, confirmLabel: 'Delete', danger: true })) return;
    const r = await del(`/presets/${sel.id}`);
    if (r?.presets) { presets = r.presets; selId = presets[0]?.id || null; snap = sel ? JSON.stringify(sel) : null; }
  }
  function pick(p) { selId = p.id; snap = JSON.stringify(p); }

  // The three AGENTS the system divides into — shown at the top so the structure is legible. Each
  // backs a real preset (click to edit it below). Author = author-time; Narrative + Dungeon Master =
  // play-time. They map 1:1 to the agent groups in the list.
  const ROLES = [
    { icon: '🛠', name: 'Author', preset: 'character_smith',
      desc: 'Authors the story with you — every tool available; adopts a mode by what you say (its facets live in the story agent config).',
      backs: 'Author agent' },
    { icon: '🎭', name: 'Narrative', preset: 'free_chat',
      desc: 'Plays the story out: narrates each turn, embodies the cast from their per-character lorebooks, tracks who is in the scene.',
      backs: 'Narrative agent' },
    { icon: '🌙', name: 'Dungeon Master', preset: 'stage_sim_director',
      desc: 'Runs play: directs scenes and embodies actors (the simulation), and consolidates the aftermath when the player sleeps or dies (Storymaster).',
      backs: 'Director · Actor · Storymaster' },
  ];
  function gotoRole(r) { const p = r.preset && presets.find((x) => x.id === r.preset); if (p) pick(p); }
  const paramCount = (p) => Object.values(p?.params || {}).filter((v) => v !== '' && v != null).length;

  // Lorebooks this preset ATTACHES (composition: world info, sprites, functions). Stored on
  // the preset as `lorebooks`; the autosave effect persists the change.
  function editAttachedBooks() {
    openBrowse({
      kind: 'lorebook', multi: true, value: [...(sel.lorebooks || [])],
      title: `Lorebooks for “${sel.name}”`,
      onConfirm: (ids) => { sel.lorebooks = ids; },
    });
  }
  const bookName = (id) => books.find((b) => b.id === id)?.name || id;

  // SCRIPTS the preset can trigger — the callable tools (graph/cast/location edits, pipeline
  // stages) surfaced by the function lorebooks this preset binds (book.preset==id) or attaches
  // (preset.lorebooks). Each function entry NAMES a registered script and carries its trigger
  // keywords; `describe` says what it does. Resolved lazily per book and cached.
  let scriptCache = new Map();        // bookId -> entries[]
  let presetScripts = $state([]);     // [{ fn, kind, describe, keywords, book, bookName, bound, attached }]
  let scriptsKey = null;
  $effect(() => {
    const p = sel;
    if (!p) { presetScripts = []; return; }
    const boundIds = books.filter((b) => b.preset === p.id).map((b) => b.id);
    const attachedIds = [...(p.lorebooks || [])];
    const key = p.id + '|' + boundIds.join(',') + '|' + attachedIds.join(',');
    if (key === scriptsKey) return;
    scriptsKey = key;
    loadPresetScripts(p, boundIds, attachedIds);
  });
  async function loadPresetScripts(p, boundIds, attachedIds) {
    const attached = new Set(attachedIds);
    const ids = [...new Set([...boundIds, ...attachedIds])];
    const rows = [];
    for (const bid of ids) {
      let entries = scriptCache.get(bid);
      if (!entries) {
        try { entries = (await get(`/lorebooks/${encodeURIComponent(bid)}`)).entries || []; }
        catch { entries = []; }
        scriptCache.set(bid, entries);
      }
      for (const e of entries) {
        if (e.facet !== 'fn' && e.facet !== 'stage') continue;
        let c = {}; try { c = JSON.parse(e.content || '{}'); } catch { /* keep {} */ }
        rows.push({
          fn: c.fn || e.id, kind: e.facet,
          describe: c.describe || e.title || c.fn || e.id,
          keywords: e.keywords || [],
          book: bid, bookName: bookName(bid),
          bound: boundIds.includes(bid), attached: attached.has(bid),
        });
      }
    }
    if (sel?.id === p.id) presetScripts = rows;   // ignore a stale in-flight load
  }

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

<div class="roles">
  <div class="rhead">The three roles <span class="lo">— how the agent system fits together</span></div>
  <div class="rcards">
    {#each ROLES as r}
      <button class="rcard" class:link={r.preset} onclick={() => gotoRole(r)} title={r.preset ? 'Edit this agent' : 'Runtime role (no preset)'}>
        <div class="rtitle">{r.icon} {r.name}</div>
        <div class="rdesc">{r.desc}</div>
        <div class="rback">{r.preset ? '→ ' : ''}{r.backs}</div>
      </button>
    {/each}
  </div>
</div>

<div class="wrap">
  {#snippet presetRow(p)}
    <button class="row" class:on={p.id === selId} onclick={() => pick(p)}>
      <span class="nm">{p.name || p.id}</span>
      <span class="tags">
        {#if p.image_workflow}<span class="mtag img" title="renders with {p.image_workflow}{p.image_provider ? ' · ' + p.image_provider : ''}">🖼</span>{/if}
        {#if (connections.find((c) => c.id === p.connection)?.provider) === 'ollama'}<span class="mtag local" title="runs on a local model">local</span>{/if}
        <span class="mtag" class:assist={(p.mode || '') === 'assist'} class:rp={(p.mode || '') === 'roleplay'}>
          {p.mode || 'auto'}
        </span>
        {#if (p.lorebooks || []).length}<span class="btag" title="lorebooks attached to this preset">{p.lorebooks.length}📚</span>{/if}
      </span>
    </button>
  {/snippet}

  <div class="list">
    <div class="lhead">Agents <span class="lo">— a chat model + image workflow + lorebooks + tools</span></div>
    {#each chatGroups as grp (grp.group)}
      {#if chatGroups.length > 1}<div class="pgroup">{grp.group}</div>{/if}
      {#each grp.items as p (p.id)}{@render presetRow(p)}{/each}
    {/each}
    <button class="new" onclick={newPreset}>＋ New agent</button>

    {#if pipelineGroups.length}
      <button class="advtoggle" onclick={() => (showPipeline = !showPipeline)}>
        {showPipeline ? '▾' : '▸'} Pipeline &amp; functions <span class="lo">— engine internals</span>
      </button>
      {#if showPipeline}
        {#each pipelineGroups as grp (grp.group)}
          <div class="pgroup">{grp.group}</div>
          {#each grp.items as p (p.id)}{@render presetRow(p)}{/each}
        {/each}
      {/if}
    {/if}
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
        <label class="toolsf" title="Hide models that don't support native tool-calling. Agents & pipeline presets call functions as tools; narration presets can use any model.">
          <input type="checkbox" bind:checked={toolsOnly} /> tool-capable
        </label>
      </div>

      <div class="erow">
        <label title="The image WORKFLOW this preset renders with (not an image model — a workflow already carries its checkpoints/LoRAs).">Image workflow</label>
        <Combobox items={imageWfItems} value={sel.image_workflow || ''} placeholder="None — active image connection" onpick={(v) => (sel.image_workflow = v)} />
      </div>
      {#if sel.image_workflow}
        <div class="erow">
          <label title="Where this workflow runs.">Run on</label>
          <div class="seg">
            {#each IMG_PROVIDERS as p}
              <button class="segbtn" class:on={(sel.image_provider || '') === p.v} title={p.hint} onclick={() => (sel.image_provider = p.v)}>{p.label}</button>
            {/each}
          </div>
        </div>
      {/if}

      <div class="erow col">
        <label style="display:flex; align-items:center; gap:8px">Lorebooks <span class="lo">— attached to this preset (world info, sprites, functions/scripts)</span>
          <button class="addb" onclick={editAttachedBooks}>＋ Attach</button>
        </label>
        {#if (sel.lorebooks || []).length}
          <div class="chips">
            {#each sel.lorebooks as id}<a class="chip" href="/library/lorebooks?book={id}">{bookName(id)}</a>{/each}
          </div>
        {:else}
          <p class="lo">No lorebooks attached. Click <b>Attach</b> to compose this preset with world info, sprites or functions.</p>
        {/if}
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
      <button class="advtoggle adv-sec" onclick={() => (showAdvanced = !showAdvanced)}>
        {showAdvanced ? '▾' : '▸'} Advanced
        <span class="lo">— author's note, post-history &amp; inference params{#if paramCount(sel) || sel.author_note || sel.post_history}<span class="cnt">in use</span>{/if}</span>
      </button>
      {#if showAdvanced}
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
            <label title="Up to 4 strings that immediately stop generation the moment the model produces them">Stop sequences <span class="lo">— comma-separated; cut the reply off when any is produced</span></label>
            <input class="fld" bind:value={stopStr} onblur={commitStop} placeholder='e.g. \n\n, ###, "User:"' />
            <p class="lo" style="margin:2px 0 0">Generation halts as soon as the model emits one of these — e.g. to stop it writing your side of the conversation (<code>User:</code>) or running past a separator. Up to 4.</p>
          </div>
        </div>
      {/if}

      <div class="scripts">
        <div class="bhd">Scripts <span class="lo">— tools this preset can call; triggered by the keywords below</span>
          {#if presetScripts.length}<span class="cnt">{presetScripts.length}</span>{/if}
        </div>
        {#if presetScripts.length}
          <div class="scriptlist">
            {#each presetScripts as s (s.book + ':' + s.fn)}
              <div class="script">
                <div class="srow">
                  <code class="sfn">{s.fn}</code>
                  {#if s.kind === 'stage'}<span class="skind stage" title="runs a pipeline stage">stage</span>{/if}
                  <a class="sbook" href="/library/lorebooks?book={s.book}" title={s.bound ? 'bound to this preset' : 'attached to this preset'}>{s.bookName}</a>
                </div>
                <div class="sdesc">{s.describe}</div>
                {#if s.keywords.length}
                  <div class="strig">
                    <span class="lo">triggers:</span>
                    {#each s.keywords.slice(0, 8) as kw}<span class="kw">{kw}</span>{/each}
                    {#if s.keywords.length > 8}<span class="lo">+{s.keywords.length - 8}</span>{/if}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {:else}
          <p class="lo">No scripts. Bind or attach a <b>function</b> lorebook below to give this preset callable tools.</p>
        {/if}
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
  .roles { max-width: 1100px; margin-bottom: 16px; }
  .rhead { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 0 2px 8px; }
  .rcards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .rcard { text-align: left; display: flex; flex-direction: column; gap: 5px; padding: 12px 14px; border-radius: 11px;
    background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); box-shadow: none; cursor: default; }
  .rcard.link { cursor: pointer; }
  .rcard.link:hover { border-color: var(--accent); background: var(--elev); filter: none; }
  .rtitle { font-size: 13.5px; font-weight: 700; }
  .rdesc { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .rback { font-size: 11px; color: var(--accent); margin-top: auto; }
  @media (max-width: 760px) { .rcards { grid-template-columns: 1fr; } }

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
  .toolsf { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--faint); white-space: nowrap; cursor: pointer; }
  .pgroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 10px 4px 3px; }
  .pgroup:first-child { padding-top: 2px; }
  .ordl { display: inline-flex; align-items: center; gap: 6px; flex: none; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .onum { width: 60px; flex: none; padding: 6px 8px; font-size: 12.5px; }
  .mtag { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--faint);
    background: var(--elev); border-radius: 999px; padding: 1px 7px; }
  .mtag.assist { color: var(--accent); background: rgba(109,140,255,.14); }
  .mtag.rp { color: var(--good); background: rgba(100,210,130,.12); }
  .mtag.local { color: var(--warn); background: rgba(230,170,90,.14); }
  .mtag.img { background: none; padding: 0; font-size: 12px; }
  .advtoggle { margin-top: 10px; padding: 7px 9px; border-radius: 8px; background: none; border: 0; box-shadow: none;
    color: var(--muted); font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px;
    text-align: left; cursor: pointer; }
  .advtoggle:hover { color: var(--text); background: var(--elev); filter: none; }
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

  .scripts { border-top: 1px solid var(--border-soft); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
  .scriptlist { display: flex; flex-direction: column; gap: 6px; }
  .script { border: 1px solid var(--border-soft); border-radius: 9px; padding: 8px 10px; background: var(--bg); display: flex; flex-direction: column; gap: 4px; }
  .srow { display: flex; align-items: center; gap: 8px; }
  .sfn { font-size: 12px; font-weight: 700; color: var(--accent); background: rgba(109,140,255,.12); border-radius: 6px; padding: 1px 7px; }
  .skind.stage { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--warn); background: rgba(230,170,90,.14); border-radius: 999px; padding: 1px 7px; }
  .sbook { margin-left: auto; font-size: 11px; color: var(--muted); text-decoration: none; }
  .sbook:hover { color: var(--accent); }
  .sdesc { font-size: 12.5px; color: var(--text); line-height: 1.4; }
  .strig { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
  .kw { font-size: 11px; color: var(--muted); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 5px; padding: 1px 6px; }

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

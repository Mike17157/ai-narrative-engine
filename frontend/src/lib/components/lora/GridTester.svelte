<script>
  // Grid tester: render the same prompt across N checkpoints (rows) × M LoRAs (columns).
  // Anima family only. State is persisted to localStorage so the grid survives tab switches.
  //
  // Import button: drag .safetensors files into the modal — backend classifies, places in
  // the correct family folder, new items appear in the pool automatically.
  import { onDestroy, onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { jobStream } from '$lib/sse.js';
  import { img } from '$lib/images.svelte.js';
  import { loraLib, famOf } from '$lib/lora-library.svelte.js';
  import ImgCard from '$lib/components/image/ImgCard.svelte';
  import Combobox from '$lib/components/shared/Combobox.svelte';

  const CACHE_KEY = 'grid-tester-v1';

  // --- selection state ---
  let selCkpts = $state([]);
  let selLoras = $state([]);   // {name, weights: number[]}[]
  let running  = $state(false);
  let wm       = $state(null); // weight modal
  let cfgOpen  = $state(true); // left config panel folded out?
  let ckptPick = $state('');   // add-picker bindings (reset after each pick)
  let loraPick = $state('');

  // --- import modal (type-specific: checkpoints and LoRAs import separately) ---
  let importOpen  = $state(false);
  let importKind  = $state('checkpoint'); // which importer is active: 'checkpoint' | 'lora'
  let importDrop  = $state(false);  // dragging over the modal drop zone
  let importFiles = $state([]);     // [{name, status:'uploading'|'done'|'err', kind, family, rel, err, runpod:'syncing'|'synced'|'err'|null}]
  let dropKind    = $state(null);   // which inline import button is being dragged over: 'checkpoint' | 'lora' | null

  // --- pools ---
  let animaCkpts = $derived(
    (loraLib.scan?.items || [])
      .filter((i) => (i.kind === 'checkpoint' || i.kind === 'diffusion') && i.family === 'anima')
      .map((i) => i.rel)
  );
  let animaLoras = $derived(
    (loraLib.choices.loras || []).filter((n) => famOf(n) === 'anima')
  );
  let availCkpts = $derived(animaCkpts.filter((v) => !selCkpts.includes(v)));
  let availLoras = $derived(animaLoras.filter((n) => !selLoras.find((l) => l.name === n)));
  let extraCkpts = $derived(selCkpts.filter((v) => !animaCkpts.includes(v)));
  let extraLoras = $derived(selLoras.filter((l) => !animaLoras.includes(l.name)));

  // --- toggle helpers ---
  function addCkpt(v) { if (v && !selCkpts.includes(v)) selCkpts = [...selCkpts, v]; }
  function rmCkpt(v)  { selCkpts = selCkpts.filter((c) => c !== v); }
  function addLora(v) { if (v && !selLoras.find((l) => l.name === v)) selLoras = [...selLoras, { name: v, weights: [0.8] }]; }
  function rmLora(n)  { selLoras = selLoras.filter((l) => l.name !== n); }

  // --- drop-zone drag handlers for the tag fields ---
  let czDrag = $state(false);
  let lzDrag = $state(false);
  function fieldDragOver(e, set) {
    if (!e.dataTransfer.types.includes('text/plain')) return;
    e.preventDefault(); set(true);
  }
  function fieldDragLeave(e, set) { if (!e.currentTarget.contains(e.relatedTarget)) set(false); }
  function ckptDrop(e) {
    e.preventDefault(); czDrag = false;
    try { const d = JSON.parse(e.dataTransfer.getData('text/plain')); if (d?.type === 'checkpoint') addCkpt(d.name); } catch {}
  }
  function loraDrop(e) {
    e.preventDefault(); lzDrag = false;
    try { const d = JSON.parse(e.dataTransfer.getData('text/plain')); if (d?.type === 'lora') addLora(d.name); } catch {}
  }

  // --- type-specific import buttons, each also a file drop target ---
  function openImport(kind) { importKind = kind; importOpen = true; }
  function impDragOver(e, kind) {
    if (!e.dataTransfer.types.includes('Files')) return;  // files only
    e.preventDefault(); dropKind = kind;
  }
  function impDragLeave(e) { if (!e.currentTarget.contains(e.relatedTarget)) dropKind = null; }
  function impFileDrop(e, kind) {
    e.preventDefault(); dropKind = null;
    const files = [...(e.dataTransfer.files || [])];
    if (files.length) { importKind = kind; importOpen = true; uploadFiles(files); }
  }

  // --- columns ---
  let columns = $derived(
    selLoras.flatMap((l) => l.weights.map((w) => ({ lora: l.name, weight: w })))
  );

  // --- build a preset from chosen columns (each column is a LoRA @ weight) ---
  // Select the winning columns, then save them as a stacked image preset.
  let selCols = $state(new Set());           // keys: `${lora}||${weight}`
  const colKey = (col) => `${col.lora}||${col.weight}`;
  function toggleCol(col) {
    const k = colKey(col); const s = new Set(selCols);
    s.has(k) ? s.delete(k) : s.add(k); selCols = s;
  }
  let savingPreset = $state(false);
  let presetMsg = $state(null);              // {ok, text} | null
  async function saveAsPreset() {
    const chosen = columns.filter((c) => selCols.has(colKey(c)));
    if (!chosen.length || savingPreset) return;
    savingPreset = true; presetMsg = null;
    const name = (window.prompt('Name this image preset:', 'Grid preset') || '').trim();
    if (!name) { savingPreset = false; return; }
    // Dedupe by lora name, last weight wins (matches inject_models semantics).
    const loras = [...new Map(chosen.map((c) => [c.lora, { name: c.lora, weight: c.weight }])).values()];
    try {
      const res = await fetch('/api/image-presets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, family: 'anima', category: 'Custom', loras }),
      });
      const data = await res.json();
      presetMsg = data.id
        ? { ok: true, text: `Saved “${name}” → Library ▸ Image Presets` }
        : { ok: false, text: data.error || 'save failed' };
      if (data.id) selCols = new Set();
    } catch (e) { presetMsg = { ok: false, text: String(e) }; }
    savingPreset = false;
  }

  // --- cell state ---
  let cells = $state({});
  const ck = (ckpt, lora, w) => `${ckpt}||${lora}||${w}`;

  // Active job tracking (for cancel + cleanup).
  let _job = null;
  function _closeJob() { _job?.cancel(); _job = null; }
  onDestroy(_closeJob);

  function _attachJobStream(id, onDone) {
    _job = jobStream(id, (ev) => {
      const k = ev.key;
      if (ev.type === 'cell_start') {
        cells[k] = { img: cells[k]?.img || null, status: 'gen', pct: null, err: null };
      } else if (ev.type === 'cell_progress') {
        if (cells[k]) cells[k].pct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
      } else if (ev.type === 'cell_image') {
        if (cells[k]) { cells[k].img = (ev.images || [])[0] || null; cells[k].status = 'done'; }
      } else if (ev.type === 'cell_error') {
        cells[k] = { ...(cells[k] || {}), status: 'err', err: ev.error };
      }
    }, onDone);
  }

  async function renderCell(ckpt, lora, weight) {
    const col = { lora, weight };
    const k = ck(ckpt, lora, weight);
    cells[k] = { img: null, status: 'gen', pct: null, err: null };
    try {
      const res = await fetch('/api/loras/grid-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells: [_makeCell(ckpt, col)] }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) {
        cells[k] = { img: null, status: 'err', pct: null, err: data.error || 'failed to start render' };
        return;
      }
      await new Promise((resolve) => _attachJobStream(data.id, resolve));
    } catch (e) {
      cells[k] = { img: null, status: 'err', pct: null, err: String(e) };
    }
  }

  async function renderAll() {
    if (running) { _closeJob(); running = false; return; }
    if (!selCkpts.length || !columns.length) return;

    const pending = [];
    for (const ckpt of selCkpts)
      for (const col of columns)
        if (!cells[ck(ckpt, col.lora, col.weight)]?.img)
          pending.push(_makeCell(ckpt, col));

    if (!pending.length) return;
    running = true;

    try {
      const res = await fetch('/api/loras/grid-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells: pending }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) { running = false; return; }
      await new Promise((resolve) => _attachJobStream(data.id, resolve));
    } catch { /* network error */ }

    running = false;
  }

  // --- weight modal ---
  function openModal(name) {
    const found = selLoras.find((l) => l.name === name);
    if (!found) return;
    const w = found.weights; const isRange = w.length > 1;
    wm = {
      name, mode: isRange ? 'range' : 'single',
      singleW: isRange ? 0.8 : (w[0] ?? 0.8),
      rangeMin: isRange ? w[0] : 0.4, rangeMax: isRange ? w[w.length - 1] : 1.0,
      rangeStep: isRange && w.length > 1 ? Math.round((w[1] - w[0]) * 1000) / 1000 : 0.2,
    };
  }
  function modalPreview(m) {
    if (!m) return [];
    if (m.mode === 'single') return [+parseFloat(m.singleW).toFixed(3)];
    const vals = []; const step = Math.max(+m.rangeStep || 0.05, 0.01);
    for (let v = +m.rangeMin; v <= +m.rangeMax + 0.0001; v += step) { vals.push(+v.toFixed(3)); if (vals.length > 20) break; }
    return vals;
  }
  function applyModal() {
    if (!wm) return;
    selLoras = selLoras.map((l) => l.name === wm.name ? { ...l, weights: modalPreview(wm) } : l);
    wm = null;
  }

  // --- import modal ---
  async function uploadFiles(files) {
    const newEntries = files.map((f) => ({ name: f.name, status: 'uploading', kind: null, family: null, rel: null, err: null }));
    importFiles = [...importFiles, ...newEntries];
    const startIdx = importFiles.length - newEntries.length;

    await Promise.all(files.map(async (file, i) => {
      const idx = startIdx + i;
      try {
        const fd = new FormData();
        fd.append('file', file);
        fd.append('expect', importKind);
        const res = await fetch('/api/comfy/models/smart-upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (data.ok) {
          importFiles[idx] = { ...importFiles[idx], status: 'done', kind: data.kind, family: data.family, rel: data.rel,
                               runpod: data.runpod_job_id ? 'syncing' : null };
          if (data.runpod_job_id) {
            const es = new EventSource(`/api/jobs/${data.runpod_job_id}/stream`);
            es.onmessage = (e) => {
              let ev; try { ev = JSON.parse(e.data); } catch { return; }
              if (ev.type === 'done') {
                importFiles[idx] = { ...importFiles[idx], runpod: ev.status === 'done' ? 'synced' : 'err' };
                es.close();
              } else if (ev.type === 'file_error') {
                importFiles[idx] = { ...importFiles[idx], runpod: 'err', runpodErr: ev.error };
                es.close();
              }
            };
            es.onerror = () => { importFiles[idx] = { ...importFiles[idx], runpod: 'err' }; es.close(); };
          }
        } else {
          importFiles[idx] = { ...importFiles[idx], status: 'err', err: data.error || 'upload failed' };
        }
      } catch (e) {
        importFiles[idx] = { ...importFiles[idx], status: 'err', err: String(e) };
      }
    }));

    // Refresh the scan so new items appear in the pool
    const anyDone = importFiles.some((f) => f.status === 'done');
    if (anyDone) {
      try { loraLib.scan = await get('/comfy/models'); } catch {}
      try { loraLib.choices = await get('/comfy/choices'); } catch {}
    }
  }

  function onModalDrop(e) {
    e.preventDefault(); importDrop = false;
    const files = [...(e.dataTransfer.files || [])].filter((f) => f.name.endsWith('.safetensors'));
    if (files.length) uploadFiles(files);
  }

  function closeImport() { importOpen = false; importFiles = []; }

  // --- persistence (localStorage) ---
  onMount(async () => {
    // Local cache first (instant).
    try {
      const saved = JSON.parse(localStorage.getItem(CACHE_KEY) || 'null');
      if (saved) {
        if (Array.isArray(saved.selCkpts)) selCkpts = saved.selCkpts;
        if (Array.isArray(saved.selLoras)) selLoras = saved.selLoras;
        if (saved.cells && typeof saved.cells === 'object') cells = saved.cells;
        if (typeof saved.cfgOpen === 'boolean') cfgOpen = saved.cfgOpen;
      }
    } catch {}
    // Server config is the authoritative selection (survives clearing localStorage / other browsers).
    try {
      const r = await fetch('/api/runpod/grid-config');
      if (r.ok) {
        const cfg = await r.json();
        if (cfg.checkpoints?.length) selCkpts = cfg.checkpoints;
        if (cfg.loras?.length) selLoras = cfg.loras.map((n) => {
          const existing = selLoras.find((l) => l.name === n);
          return existing || { name: n, weights: [0.8] };
        });
      }
    } catch {}
  });

  let _saveTimer;
  let _gridSaveTimer;
  $effect(() => {
    // Touch reactive state to establish tracking
    const snap = { selCkpts: [...selCkpts], selLoras: [...selLoras], cells: { ...cells }, cfgOpen };
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
      try { localStorage.setItem(CACHE_KEY, JSON.stringify(snap)); } catch {}
    }, 600);

    // Also persist checkpoint+lora selection server-side so startup reconcile knows what to sync.
    const ckpts = [...selCkpts];
    const loras = selLoras.map((l) => l.name);
    clearTimeout(_gridSaveTimer);
    _gridSaveTimer = setTimeout(() => {
      fetch('/api/runpod/grid-config', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ checkpoints: ckpts, loras }),
      }).catch(() => {});
    }, 1200);
  });

  // --- RunPod volume reconcile ---
  let syncing = $state(false);
  let syncMsg = $state(null);  // {ok, text} | null

  async function syncRunpod() {
    if (syncing || !selCkpts.length) return;
    syncing = true; syncMsg = null;
    try {
      const res = await fetch('/api/runpod/volume/reconcile', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          checkpoints: selCkpts,
          loras: selLoras.map((l) => l.name),
        }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) {
        syncMsg = { ok: false, text: data.error || 'failed to start reconcile' };
        syncing = false; return;
      }
      // Stream the job so Activity panel picks it up; wait for done.
      await new Promise((resolve) => {
        const es = new EventSource(`/api/jobs/${data.id}/stream`);
        es.onmessage = (e) => {
          let ev; try { ev = JSON.parse(e.data); } catch { return; }
          if (ev.type === 'plan') syncMsg = { ok: true, text: `${ev.delete} delete · ${ev.upload} upload` };
          else if (ev.type === 'done') { es.close(); resolve(); }
        };
        es.onerror = () => { es.close(); resolve(); };
      });
      syncMsg = syncMsg || { ok: true, text: 'done' };
    } catch (e) { syncMsg = { ok: false, text: String(e) }; }
    syncing = false;
  }

  // Build a triage cell dict. Diffusion models (split UNet) use the model-based path
  // so inject_models wires them through the real anima workflow. Bundled checkpoints
  // use the minimal graph path (CheckpointLoaderSimple).
  function _makeCell(ckpt, col) {
    const k = ck(ckpt, col.lora, col.weight);
    const kind = (loraLib.scan?.items || []).find((i) => i.rel === ckpt)?.kind ?? 'checkpoint';
    if (kind === 'diffusion') {
      return { key: k, model: 'anima', checkpoint_override: ckpt,
               loras: [{ name: col.lora, weight: col.weight }], prompt: img.testPrompt };
    }
    return { key: k, checkpoint: ckpt, lora: col.lora, weight: col.weight, prompt: img.testPrompt };
  }

  // --- progress ---
  let totalCells = $derived(selCkpts.length * columns.length);
  let doneCells  = $derived(
    selCkpts.reduce((s, ckpt) => s + columns.filter((c) => cells[ck(ckpt, c.lora, c.weight)]?.img).length, 0)
  );
  const shortName = (p) => (p || '').split(/[/\\]/).pop();
  const weightLabel = (l) => l.weights.length === 1
    ? `@${l.weights[0]}`
    : `@${l.weights[0]}–${l.weights[l.weights.length - 1]} ×${l.weights.length}`;
</script>

<div class="gt" class:cfg-closed={!cfgOpen}>
  <!-- ── Fold-out config panel ── -->
  <aside class="cfg">
   <div class="cfg-inner">
    <div class="gt-head">
      <span class="gt-title">Grid Test</span>
      <span class="gt-sub">anima · checkpoints × LoRAs</span>
    </div>

  <!-- Prompt -->
  <div class="prompt-row">
    <label>Prompt</label>
    <textarea class="tp" rows="2" bind:value={img.testPrompt} placeholder="1girl, solo, standing…"></textarea>
  </div>

  <div class="axes">
    <!-- ── Checkpoints ── -->
    <div class="axis">
      <div class="axis-label">
        Checkpoints <span class="ax-hint">rows</span>
        <button class="import-btn" class:drop={dropKind === 'checkpoint'}
          ondragover={(e) => impDragOver(e, 'checkpoint')}
          ondragleave={impDragLeave}
          ondrop={(e) => impFileDrop(e, 'checkpoint')}
          onclick={() => openImport('checkpoint')}
          title="Import a checkpoint — drag a .safetensors file here or click">+ Import checkpoint</button>
      </div>
      <div class="tag-field" class:dragover={czDrag} role="group"
        ondragover={(e) => fieldDragOver(e, (v) => czDrag = v)}
        ondragleave={(e) => fieldDragLeave(e, (v) => czDrag = v)}
        ondrop={ckptDrop}
      >
        {#each selCkpts as v (v)}
          <span class="tag ckpt" class:ext={extraCkpts.includes(v)}>
            <span class="tname" title={v}>{shortName(v)}</span>
            {#if extraCkpts.includes(v)}<span class="ext-badge">ext</span>{/if}
            <button class="tx" onclick={() => rmCkpt(v)} aria-label="remove">×</button>
          </span>
        {/each}
        {#if !selCkpts.length}
          <span class="field-ph">{czDrag ? '↓ drop here' : 'click below to add…'}</span>
        {:else if czDrag}
          <span class="field-ph drop">↓ drop to add</span>
        {/if}
      </div>
      <Combobox items={availCkpts.map((v) => ({ value: v, label: shortName(v) }))}
        bind:value={ckptPick} placeholder="+ Add checkpoint…"
        onpick={(v) => { addCkpt(v); ckptPick = ''; }} />
      {#if !animaCkpts.length}<p class="ax-empty">No anima checkpoints in scan.</p>{/if}
    </div>

    <!-- ── LoRAs ── -->
    <div class="axis">
      <div class="axis-label">
        LoRAs <span class="ax-hint">columns · click tag to set weight</span>
        <button class="import-btn" class:drop={dropKind === 'lora'}
          ondragover={(e) => impDragOver(e, 'lora')}
          ondragleave={impDragLeave}
          ondrop={(e) => impFileDrop(e, 'lora')}
          onclick={() => openImport('lora')}
          title="Import a LoRA — drag a .safetensors file here or click">+ Import LoRA</button>
      </div>
      <div class="tag-field" class:dragover={lzDrag} role="group"
        ondragover={(e) => fieldDragOver(e, (v) => lzDrag = v)}
        ondragleave={(e) => fieldDragLeave(e, (v) => lzDrag = v)}
        ondrop={loraDrop}
      >
        {#each selLoras as l (l.name)}
          <button class="tag lora" class:ext={extraLoras.find((x) => x.name === l.name)} onclick={() => openModal(l.name)}>
            <span class="tname" title={l.name}>{shortName(l.name)}</span>
            <span class="tw">{weightLabel(l)}</span>
            <span class="tx" role="presentation" onclick={(e) => { e.stopPropagation(); rmLora(l.name); }}>×</span>
          </button>
        {/each}
        {#if !selLoras.length}
          <span class="field-ph">{lzDrag ? '↓ drop here' : 'click below to add…'}</span>
        {:else if lzDrag}
          <span class="field-ph drop">↓ drop to add</span>
        {/if}
      </div>
      <Combobox items={availLoras.map((n) => ({ value: n, label: shortName(n) }))}
        bind:value={loraPick} placeholder="+ Add LoRA…"
        onpick={(v) => { addLora(v); loraPick = ''; }} />
      {#if !animaLoras.length}<p class="ax-empty">No anima LoRAs found.</p>{/if}
    </div>
    </div>
   </div>
  </aside>

  <!-- Fold handle (always visible so a collapsed panel can be reopened) -->
  <button class="cfg-handle" onclick={() => (cfgOpen = !cfgOpen)}
    title={cfgOpen ? 'Hide configuration' : 'Show configuration'}
    aria-label={cfgOpen ? 'Hide configuration' : 'Show configuration'}>
    <span class="chev">{cfgOpen ? '‹' : '›'}</span>
  </button>

  <!-- ── Main: run bar + grid in their own scroll viewport ── -->
  <div class="main">
  <!-- Run bar -->
  <div class="runbar">
    <button onclick={renderAll} disabled={!selCkpts.length || !columns.length}>
      {running ? '■ Stop' : doneCells > 0 ? 'Resume' : 'Render all'}
    </button>
    {#if totalCells > 0}
      <span class="m">{doneCells}/{totalCells} · {selCkpts.length} ckpt × {columns.length} col</span>
    {/if}
    {#if running}<span class="m pulse">generating…</span>{/if}
    <div class="runbar-sep"></div>
    <button class="ghost sync-btn" onclick={syncRunpod}
      disabled={syncing || !selCkpts.length}
      title="Upload selected checkpoints + LoRAs to RunPod volume; remove anything not in this grid">
      {syncing ? 'Syncing…' : 'Sync RunPod'}
    </button>
    {#if syncMsg}
      <span class="m" class:bad={!syncMsg.ok}>{syncMsg.text}</span>
    {/if}
    <button class="ghost save-preset" onclick={saveAsPreset} disabled={savingPreset || !selCols.size}
      title="Save the selected LoRA columns as a stacked image preset">
      {savingPreset ? 'Saving…' : `＋ Save preset${selCols.size ? ` (${selCols.size})` : ''}`}
    </button>
    {#if presetMsg}
      <span class="m" class:bad={!presetMsg.ok}>{presetMsg.text}</span>
    {/if}
  </div>

  <!-- Grid -->
  <div class="grid-wrap">
    {#if !selCkpts.length || !columns.length}
      <div class="grid-empty">
        {#if !selCkpts.length && !columns.length}Select checkpoints and LoRAs in the panel.
        {:else if !selCkpts.length}Select at least one checkpoint.
        {:else}Select at least one LoRA.{/if}
      </div>
    {:else}
      <div class="grid" style="grid-template-columns: 150px repeat({columns.length}, 180px);">
        <div class="corner"></div>
        {#each columns as col (`${col.lora}||${col.weight}`)}
          <button class="ch" class:picked={selCols.has(`${col.lora}||${col.weight}`)}
            onclick={() => toggleCol(col)} title="Click to add this LoRA @ {col.weight} to a saved preset">
            <span class="cn" title={col.lora}>{shortName(col.lora)}</span>
            <span class="cw">@{col.weight}</span>
            {#if selCols.has(`${col.lora}||${col.weight}`)}<span class="ckmark">✓</span>{/if}
          </button>
        {/each}
        {#each selCkpts as ckpt (ckpt)}
          <div class="rh" title={ckpt}>{shortName(ckpt)}</div>
          {#each columns as col (`${col.lora}||${col.weight}`)}
            {@const c = cells[ck(ckpt, col.lora, col.weight)] || {}}
            <div class="gcell">
              <div class="thumb">
                <ImgCard src={c.img || null} busy={c.status === 'gen'}
                  onRegen={() => renderCell(ckpt, col.lora, col.weight)} />
              </div>
              {#if c.status === 'gen' && c.pct !== null}<div class="gpct">{c.pct}%</div>{/if}
              {#if c.status === 'err'}<div class="gerr" title={c.err || ''}>!</div>{/if}
            </div>
          {/each}
        {/each}
      </div>
    {/if}
  </div>
  </div>
</div>

<!-- ── Import modal ── -->
{#if importOpen}
  <div class="overlay" role="dialog" aria-modal="true" onclick={(e) => { if (e.target === e.currentTarget) closeImport(); }}>
    <div class="modal import-modal">
      <div class="modal-head">
        <h4>Import {importKind === 'lora' ? 'LoRAs' : 'checkpoints'}</h4>
        <button class="close-btn" onclick={closeImport} aria-label="close">×</button>
      </div>
      <p class="import-sub">Drag <code>.safetensors</code> {importKind === 'lora' ? 'LoRA' : 'checkpoint'} files below. The backend verifies each is a {importKind}, detects the family, and places it in the correct folder. A file of the wrong kind is rejected.</p>

      <!-- Drop zone -->
      <div
        class="drop-zone"
        class:active={importDrop}
        ondragover={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); importDrop = true; } }}
        ondragleave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) importDrop = false; }}
        ondrop={onModalDrop}
      >
        <span class="dz-icon">⬇</span>
        <span class="dz-label">{importDrop ? 'Drop to upload' : `Drop .safetensors ${importKind} files here`}</span>
      </div>

      <!-- File list -->
      {#if importFiles.length}
        <div class="file-list">
          {#each importFiles as f (f.name)}
            <div class="file-row" class:done={f.status === 'done'} class:err={f.status === 'err'}>
              <span class="fname">{f.name}</span>
              {#if f.status === 'uploading'}
                <span class="ftag uploading">uploading…</span>
              {:else if f.status === 'done'}
                <span class="ftag done">{f.kind} · {f.family}</span>
                <span class="frel">{f.rel}</span>
                {#if f.runpod === 'syncing'}<span class="ftag rp-sync">RunPod ↑</span>
                {:else if f.runpod === 'synced'}<span class="ftag rp-ok">RunPod ✓</span>
                {:else if f.runpod === 'err'}<span class="ftag rp-err" title={f.runpodErr || 'upload failed'}>RunPod ✗</span>
                {/if}
              {:else if f.status === 'err'}
                <span class="ftag err" title={f.err}>error</span>
                <span class="ferr">{f.err}</span>
              {/if}
            </div>
          {/each}
        </div>
      {/if}

      <div class="modal-foot">
        <button class="sec" onclick={closeImport}>Done</button>
      </div>
    </div>
  </div>
{/if}

<!-- ── Weight modal ── -->
{#if wm}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="modal">
      <h4>Weight — <span class="mname">{shortName(wm.name)}</span></h4>
      <div class="moderow">
        <label class="mopt"><input type="radio" bind:group={wm.mode} value="single" /> Single</label>
        <label class="mopt"><input type="radio" bind:group={wm.mode} value="range" /> Range</label>
      </div>
      {#if wm.mode === 'single'}
        <div class="frow"><span class="fl">Weight</span><input type="number" step="0.05" min="0" max="2" bind:value={wm.singleW} class="fw" /></div>
      {:else}
        <div class="frow"><span class="fl">Min</span><input type="number" step="0.05" min="0" max="2" bind:value={wm.rangeMin} class="fw" /></div>
        <div class="frow"><span class="fl">Max</span><input type="number" step="0.05" min="0" max="2" bind:value={wm.rangeMax} class="fw" /></div>
        <div class="frow"><span class="fl">Step</span><input type="number" step="0.05" min="0.05" max="1" bind:value={wm.rangeStep} class="fw" /></div>
      {/if}
      <div class="preview">
        <span class="plabel">Columns:</span>
        {#each modalPreview(wm) as w}<span class="ptag">{w}</span>{/each}
        {#if !modalPreview(wm).length}<span class="pempty">invalid range</span>{/if}
      </div>
      <div class="mbtns">
        <button onclick={applyModal} disabled={!modalPreview(wm).length}>Apply</button>
        <button class="sec" onclick={() => (wm = null)}>Cancel</button>
      </div>
    </div>
  </div>
{/if}

<style>
  /* Two-pane shell: fold-out config panel (left) + grid viewport (right).
     Fills the full height of the page so the grid gets its own scroll area
     and the horizontal scrollbar stays reachable without scrolling the page. */
  .gt { display: flex; align-items: stretch; height: 100%; min-height: 0; }

  /* Fold-out config panel */
  .cfg {
    flex: none; width: 340px; overflow: hidden;
    border-right: 1px solid var(--border-soft);
    transition: width .18s ease;
  }
  .gt.cfg-closed .cfg { width: 0; border-right-color: transparent; }
  .cfg-inner {
    width: 340px; height: 100%; box-sizing: border-box;
    display: flex; flex-direction: column; gap: 14px;
    padding: 16px 16px 24px; overflow-y: auto;
  }

  /* Fold handle on the divider — always visible */
  .cfg-handle {
    flex: none; width: 16px; align-self: stretch;
    display: flex; align-items: center; justify-content: center;
    border: none; border-right: 1px solid var(--border-soft);
    background: var(--elev); color: var(--faint); cursor: pointer;
    padding: 0; transition: color .12s, background .12s;
  }
  .cfg-handle:hover { color: var(--accent); background: var(--panel); }
  .chev { font-size: 15px; line-height: 1; }

  /* Main grid viewport */
  .main {
    flex: 1; min-width: 0; min-height: 0;
    display: flex; flex-direction: column; gap: 12px;
    padding: 16px; overflow: hidden;
  }

  .gt-head { display: flex; align-items: baseline; gap: 10px; }
  .gt-title { font-size: 15px; font-weight: 700; color: var(--text); }
  .gt-sub { font-size: 12px; color: var(--faint); flex: 1; }
  .import-btn {
    margin-left: auto; font-size: 10.5px; padding: 2px 8px; border-radius: 6px;
    background: var(--elev); border: 1px dashed var(--border-soft);
    color: var(--muted); cursor: pointer; white-space: nowrap; flex: none;
    transition: border-color .12s, color .12s, background .12s;
  }
  .import-btn:hover, .import-btn.drop {
    border-color: var(--accent); color: var(--accent);
    background: rgba(109,140,255,.08);
  }

  .prompt-row label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .tp { width: 100%; resize: none; overflow: hidden; font: inherit; line-height: 1.4; }

  .axes { display: flex; flex-direction: column; gap: 16px; }
  .axis { display: flex; flex-direction: column; gap: 6px; }
  .axis-label { display: flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600; color: var(--text); }
  .ax-hint { font-weight: 400; font-size: 11px; color: var(--faint); margin-left: 4px; }
  .ax-empty { font-size: 12px; color: var(--faint); margin: 2px 0 0; }

  .tag-field {
    display: flex; flex-wrap: wrap; gap: 5px; min-height: 40px; padding: 6px 8px;
    border: 1.5px solid var(--border-soft); border-radius: 9px; background: var(--elev);
    align-items: flex-start; transition: border-color .12s;
  }
  .tag-field.dragover { border-color: var(--accent); background: rgba(109,140,255,.07); }
  .field-ph { font-size: 11.5px; color: var(--faint); align-self: center; }
  .field-ph.drop { color: var(--accent); }

  .tag {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 5px 3px 8px; border-radius: 6px; font-size: 11.5px;
    user-select: none; white-space: nowrap; font: inherit;
  }
  .tag.ckpt { background: rgba(109,140,255,.14); border: 1px solid rgba(109,140,255,.35); color: var(--text); cursor: default; }
  .tag.lora { background: rgba(109,140,255,.18); border: 1px solid var(--accent); color: var(--accent); cursor: pointer; }
  .tag.lora:hover { background: rgba(109,140,255,.28); }
  .tag.ext { opacity: .8; }
  .tname { max-width: 130px; overflow: hidden; text-overflow: ellipsis; }
  .tw { font-size: 10px; color: var(--muted); }
  .ext-badge { font-size: 9px; padding: 0 4px; background: rgba(255,255,255,.08); border-radius: 3px; color: var(--faint); }
  .tx { font-size: 14px; line-height: 1; padding: 0 1px; color: var(--faint); flex: none; cursor: pointer; border: none; background: none; display: inline; }
  .tx:hover { color: var(--bad); }

  .runbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .runbar-sep { flex: 1; }
  .sync-btn { font-size: 12px; padding: 4px 12px; }
  .m { font-size: 12.5px; color: var(--muted); }
  .m.bad { color: var(--bad); }
  .pulse { color: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.45} }

  .grid-wrap { flex: 1; min-height: 0; border: 1px solid var(--border-soft); border-radius: 10px; overflow: auto; background: var(--panel); }
  .grid-empty { display: flex; align-items: center; justify-content: center; height: 100px; color: var(--faint); font-size: 13px; }
  .grid { display: grid; gap: 1px; background: var(--border-soft); min-width: max-content; }
  .corner { background: var(--panel); }
  .ch { position: relative; background: var(--elev); padding: 6px 8px; font-size: 11px; display: flex; flex-direction: column; gap: 2px; justify-content: flex-end; min-height: 50px; min-width: 180px; box-shadow: none; border: 1px solid transparent; border-radius: 0; cursor: pointer; text-align: left; font: inherit; }
  .ch:hover { background: var(--elev-2); filter: none; }
  .ch.picked { border-color: var(--accent); background: rgba(109,140,255,.14); }
  .ckmark { position: absolute; top: 4px; right: 6px; font-size: 11px; font-weight: 700; color: var(--accent); }
  .save-preset { font-size: 12px; padding: 4px 12px; }
  .cn { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cw { color: var(--accent); font-size: 10.5px; font-weight: 700; }
  .rh { background: var(--elev); padding: 6px 8px; font-size: 10.5px; color: var(--text); display: flex; align-items: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; line-height: 1.35; min-width: 150px; }
  .gcell { position: relative; width: 180px; height: 180px; background: var(--panel); }
  .thumb { width: 100%; height: 100%; }
  .gpct { position: absolute; bottom: 4px; left: 50%; transform: translateX(-50%); font-size: 10px; color: #fff; background: rgba(0,0,0,.65); border-radius: 4px; padding: 1px 6px; pointer-events: none; }
  .gerr { position: absolute; top: 4px; right: 4px; font-size: 10px; font-weight: 700; color: var(--bad); background: rgba(255,60,60,.15); border-radius: 4px; padding: 1px 5px; }

  /* Import modal */
  .import-modal { width: 500px; max-width: 92vw; }
  .modal-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .modal-head h4 { margin: 0; font-size: 14px; }
  .close-btn { background: none; border: none; box-shadow: none; font-size: 18px; color: var(--muted); cursor: pointer; padding: 0 4px; line-height: 1; }
  .close-btn:hover { color: var(--text); }
  .import-sub { font-size: 12px; color: var(--muted); margin: 0 0 14px; }
  .import-sub code { font-family: ui-monospace, monospace; color: var(--text); font-size: 11.5px; }
  .drop-zone {
    border: 2px dashed var(--border-soft); border-radius: 10px;
    padding: 32px 16px; text-align: center; cursor: default;
    display: flex; flex-direction: column; align-items: center; gap: 8px;
    transition: border-color .15s, background .15s;
  }
  .drop-zone.active { border-color: var(--accent); background: rgba(109,140,255,.07); }
  .dz-icon { font-size: 28px; opacity: .4; }
  .dz-label { font-size: 13px; color: var(--muted); }
  .file-list { margin-top: 14px; display: flex; flex-direction: column; gap: 6px; max-height: 240px; overflow-y: auto; }
  .file-row { display: flex; align-items: center; gap: 8px; font-size: 12px; padding: 5px 8px; border-radius: 7px; background: var(--elev); flex-wrap: wrap; }
  .fname { font-weight: 600; color: var(--text); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ftag { font-size: 10.5px; padding: 1px 7px; border-radius: 999px; font-weight: 600; white-space: nowrap; }
  .ftag.uploading { background: rgba(255,200,0,.12); color: #e6b800; }
  .ftag.done { background: rgba(80,200,120,.14); color: var(--good); }
  .ftag.err { background: rgba(255,60,60,.12); color: var(--bad); }
  .ftag.rp-sync { background: rgba(109,140,255,.12); color: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  .ftag.rp-ok   { background: rgba(80,200,120,.14); color: var(--good); }
  .ftag.rp-err  { background: rgba(255,60,60,.12); color: var(--bad); }
  .frel { font-size: 11px; color: var(--faint); font-family: ui-monospace, monospace; }
  .ferr { font-size: 11px; color: var(--bad); }
  .modal-foot { margin-top: 16px; display: flex; justify-content: flex-end; }

  /* Shared modal */
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 200; display: flex; align-items: center; justify-content: center; }
  .modal { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 20px 22px; }
  .modal h4 { margin: 0 0 14px; font-size: 14px; }
  .mname { color: var(--accent); }
  .moderow { display: flex; gap: 18px; margin-bottom: 14px; }
  .mopt { display: flex; align-items: center; gap: 5px; font-size: 13px; cursor: pointer; }
  .mopt input { width: auto; }
  .frow { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .fl { font-size: 12px; color: var(--muted); width: 38px; flex: none; }
  .fw { width: 90px; }
  .preview { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin: 12px 0; padding: 8px 10px; background: var(--elev); border-radius: 8px; min-height: 36px; }
  .plabel { font-size: 11px; color: var(--muted); }
  .ptag { font-size: 11.5px; padding: 2px 8px; border-radius: 999px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--text); }
  .pempty { font-size: 11.5px; color: var(--bad); }
  .mbtns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
  .sec { background: var(--elev); color: var(--text); border: 1px solid var(--border-soft); }
</style>

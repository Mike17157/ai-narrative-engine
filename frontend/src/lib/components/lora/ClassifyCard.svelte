<script>
  // Classify (triage): render each on-disk LoRA against a checkpoint of its own
  // architecture, then tag it detail/theme/character/skip. Classifications land
  // in the shared library config (loraLib.cfg.library). Renders against the
  // shared global test prompt (img.testPrompt).
  import { onDestroy } from 'svelte';
  import { get } from '$lib/api.js';
  import { jobStream } from '$lib/sse.js';
  import { img } from '$lib/images.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ScrubInput from '$lib/components/shared/ScrubInput.svelte';
  import {
    loraLib, famOf, famLabel, compat, baseFamMap,
  } from '$lib/lora-library.svelte.js';
  import ImgCard from '$lib/components/image/ImgCard.svelte';

  let triageWeight = $state(0.8);
  let triageItems = $state([]);   // every lora: {name, fam, img, status, pct, type}
  let baseModel = $state('');     // the chosen workflow
  let triageRunning = $state(false);

  // Family-aware filtering against the picked workflow's family.
  let selBase = $derived(loraLib.bases.find((b) => b.key === baseModel) || null);
  let wfFam = $derived(selBase?.family || 'unknown');
  function famMatch(f) { const c = compat(f, wfFam); return loraLib.showAll ? c !== 'incompatible' : c === 'native'; }
  let baseItems = $derived(loraLib.bases.map((b) => ({ value: b.key, label: `${b.key}  ·  ${famLabel()[b.family] || b.family || b.arch}` })));
  let visibleTriage = $derived(triageItems.filter((t) => famMatch(t.fam)));
  let triageDone = $derived(visibleTriage.filter((t) => t.img).length);
  let triageGroups = $derived((loraLib.families || []).map((f) => ({
    id: f.id, label: f.label, items: visibleTriage.filter((t) => t.fam === f.id),
  })).filter((g) => g.items.length));

  // The page seeds baseModel (app.activeImage fallback) and rebuilds triage once
  // choices are in. Exposed via these so the page can drive first-paint order.
  export function setBaseModel(v) { baseModel = v; }
  export function buildTriage() {
    triageItems = (loraLib.choices.loras || []).map((n) => {
      const lib = loraLib.cfg.library.find((l) => l.name === n);
      return { name: n, fam: famOf(n), img: null, status: 'idle', pct: null, type: lib?.type || null };
    });
  }

  // Rehydrate the grid from on-disk cached renders. The backend persists each
  // render keyed by (workflow, lora), so switching workflows shows that
  // workflow's cached shots and "Render all" only fills what's missing.
  const cacheImg = (n) => `/api/loras/triage-cache/img?scope=${encodeURIComponent(baseModel)}&lora=${encodeURIComponent(n)}`;
  let hydratedFor = $state('');
  $effect(() => {
    const bm = baseModel;
    if (!bm || !triageItems.length || bm === hydratedFor) return;
    hydratedFor = bm;
    for (const it of triageItems) { it.img = null; it.status = 'idle'; it.pct = null; }
    (async () => {
      let cached = {};
      try { cached = (await get(`/loras/triage-cache?scope=${encodeURIComponent(bm)}`)).loras || {}; } catch { return; }
      if (bm !== baseModel) return; // workflow changed mid-fetch
      for (const it of triageItems) if (cached[it.name]) { it.img = cacheImg(it.name); it.status = 'done'; }
    })();
  });

  // Active job tracking (cancel + cleanup on destroy).
  let _job = null;
  function _closeJob() { _job?.cancel(); _job = null; }
  onDestroy(_closeJob);

  function _itemCell(item) {
    // Build a grid-render cell for one classify item.
    const w = +triageWeight || 0.8;
    const cache = { scope: baseModel, lora: item.name, prompt: img.testPrompt, weight: w };
    return selBase?.checkpoint
      ? { key: item.name, checkpoint: selBase.checkpoint, lora: item.name, weight: w, prompt: img.testPrompt, cache }
      : { key: item.name, model: baseModel, loras: [{ name: item.name, weight: w }], prompt: img.testPrompt, cache };
  }

  function _attachJobStream(id, itemMap, onDone) {
    _job = jobStream(id, (ev) => {
      const item = itemMap[ev.key];
      if (ev.type === 'cell_start') {
        if (item) { item.status = 'gen'; item.pct = null; item.img = null; }
      } else if (ev.type === 'cell_progress') {
        if (item) item.pct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
      } else if (ev.type === 'cell_image') {
        if (item) { item.img = (ev.images || [])[0] || null; }
      } else if (ev.type === 'cell_error') {
        if (item) { item.status = 'err'; item.err = ev.error; }
      } else if (ev.type === 'done') {
        // Mark any still-gen items as done/err.
        for (const it of Object.values(itemMap))
          if (it.status === 'gen') it.status = it.img ? 'done' : 'err';
      }
    }, onDone);
  }

  async function renderOne(item) {
    if (!selBase) { item.status = 'nockpt'; return; }
    item.status = 'gen'; item.pct = null; item.img = null;
    try {
      const res = await fetch('/api/loras/grid-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells: [_itemCell(item)] }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) { item.status = 'err'; item.err = data.error || 'failed to start'; return; }
      await new Promise((resolve) => _attachJobStream(data.id, { [item.name]: item }, resolve));
    } catch (e) { item.status = 'err'; item.err = String(e); }
    if (item.status === 'gen') item.status = item.img ? 'done' : 'err';
  }

  async function startTriage() {
    if (triageRunning) { _closeJob(); triageRunning = false; return; }
    if (!selBase) { loraLib.msg = { err: true, text: 'pick a workflow first' }; return; }

    const pending = visibleTriage.filter((t) => !t.img);
    if (!pending.length) return;
    triageRunning = true;

    try {
      const res = await fetch('/api/loras/grid-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells: pending.map(_itemCell) }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) { triageRunning = false; return; }
      const itemMap = Object.fromEntries(pending.map((t) => [t.name, t]));
      await new Promise((resolve) => _attachJobStream(data.id, itemMap, resolve));
    } catch { /* network error */ }

    triageRunning = false;
  }

  function classify(item, type) {
    item.type = (item.type === type) ? null : type;
    const i = loraLib.cfg.library.findIndex((l) => l.name === item.name);
    if (!item.type || item.type === 'skip') { if (i >= 0) loraLib.cfg.library.splice(i, 1); return; }
    const w = item.type === 'detail' ? 0.5 : item.type === 'theme' ? 0.8 : 0.7;
    if (i >= 0) loraLib.cfg.library[i].type = item.type;
    else loraLib.cfg.library.push({ name: item.name, type: item.type, weight: w, enabled: true, comment: famOf(item.name) });
  }

  async function deleteLora(item) {
    if (!await askConfirm({ title: 'Delete LoRA file?', message: `${item.name} — removes the .safetensors from disk. Cannot be undone.`, confirmLabel: 'Delete', danger: true })) return;
    const r = await fetch(`/api/loras/file?name=${encodeURIComponent(item.name)}`, { method: 'DELETE' });
    const d = await r.json().catch(() => ({}));
    if (d.ok) {
      triageItems = triageItems.filter((t) => t.name !== item.name);
      const i = loraLib.cfg.library.findIndex((l) => l.name === item.name);
      if (i >= 0) loraLib.cfg.library.splice(i, 1);
    } else loraLib.msg = { err: true, text: d.error || 'delete failed' };
  }
</script>

<section class="card">
  <h3>Classify <span class="sub">— pick a workflow; only its family's LoRAs show (toggle for all compatible). Grouped by family; render, then tag each.</span></h3>

  <div class="trow" style="grid-template-columns: 1.4fr 3fr 90px;">
    <div><label>Workflow</label><Combobox items={baseItems} value={baseModel} placeholder="workflow…" onpick={(v) => (baseModel = v)} /></div>
    <div><label>Test prompt <span class="sub">— shared with every LoRA test</span></label><textarea class="tp" rows="2" bind:value={img.testPrompt} placeholder="1girl, solo, standing…"></textarea></div>
    <div><label>Weight</label><ScrubInput step={0.01} min={0} max={2} bind:value={triageWeight} title="drag ↕ or click to type" /></div>
  </div>
  <div class="trow2">
    <button onclick={startTriage}>{triageRunning ? '■ Stop' : (triageDone ? 'Resume' : 'Render all')}</button>
    <span class="m">{triageDone}/{visibleTriage.length} rendered{triageRunning ? ' — generating…' : ''}</span>
    {#if selBase}<span class="warnsm">{famLabel()[selBase.family] || selBase.family || selBase.arch} · {visibleTriage.length} of {triageItems.length} LoRAs{triageItems.length - visibleTriage.length ? ` (${triageItems.length - visibleTriage.length} hidden)` : ''}</span>{/if}
    <label class="allchk"><input type="checkbox" bind:checked={loraLib.showAll} /> show all compatible</label>
  </div>

  {#each triageGroups as g (g.id)}
    <div class="famhead">{g.label} <span class="famn">{g.items.length}</span>{#if g.id !== wfFam}<span class="famx">cross-family</span>{/if}</div>
    <div class="grid">
      {#each g.items as it (it.name)}
        <div class="cell" class:classified={it.type && it.type !== 'skip'}
          draggable="true"
          ondragstart={(e) => { e.dataTransfer.setData('text/plain', JSON.stringify({type:'lora', name: it.name})); e.dataTransfer.effectAllowed = 'copy'; }}
        >
          <div class="thumb">
            <ImgCard
              src={it.img}
              caption={it.name}
              onRegen={selBase ? () => renderOne(it) : null}
              busy={it.status === 'gen'}
            />
          </div>
          <div class="cap" title={it.name}>
            <span class="ach">{famLabel()[it.fam] || it.fam}</span>
            <span class="fn">{it.name.split(/[\\/]/).pop()}</span>
            {#if it.status === 'err'}<span class="cerr" title={it.err || ''}>!</span>{/if}
          </div>
          <div class="types">
            {#each ['detail', 'theme', 'character', 'skip'] as t}
              <button class="tbtn {t}" class:on={it.type === t} onclick={() => classify(it, t)}>{t === 'character' ? 'char' : t}</button>
            {/each}
            <button class="tbtn del" onclick={() => deleteLora(it)} title="delete file" aria-label="Delete">🗑</button>
          </div>
        </div>
      {/each}
    </div>
  {/each}
  <p class="hint" style="margin-top:10px">Classifications fill the Library below — <strong>Save</strong> to persist.</p>
</section>

<style>
  .card { margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .warnsm { font-size: 11.5px; color: var(--muted); }
  .famhead { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; color: var(--text);
             margin: 16px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border-soft); }
  .famhead .famn { font-size: 10.5px; font-weight: 600; color: var(--faint); background: var(--elev); border-radius: 999px; padding: 1px 8px; }
  .famhead .famx { font-size: 10px; font-weight: 600; color: #c9a6ff; background: rgba(124,109,255,.12); border-radius: 999px; padding: 1px 8px; text-transform: uppercase; letter-spacing: .3px; }
  .allchk { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted); margin-left: auto; }
  .allchk input { width: auto; }
  .trow { display: grid; gap: 12px; margin-bottom: 10px; align-items: start; }
  .trow label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }
  .trow .tp { width: 100%; resize: vertical; font: inherit; line-height: 1.4; }
  .trow2 { display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
  .m { font-size: 12.5px; color: var(--muted); }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 14px; }
  .cell { display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--border-soft); border-radius: 10px; padding: 8px; background: var(--elev); }
  .cell.classified { border-color: var(--accent); }
  .thumb { position: relative; aspect-ratio: 1; width: 100%; border-radius: 7px; overflow: hidden; }
  .cap { display: flex; align-items: center; gap: 5px; font-size: 11px; margin: 7px 0 6px; min-width: 0; }
  .cap .fn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ach { flex: none; font-size: 9.5px; color: var(--faint); background: var(--panel); border: 1px solid var(--border-soft); border-radius: 4px; padding: 0 4px; }
  .cerr { flex: none; font-size: 10px; font-weight: 700; color: var(--bad); background: rgba(255,60,60,.12); border-radius: 4px; padding: 0 4px; }
  .types { display: grid; grid-template-columns: repeat(4, 1fr) 24px; gap: 3px; margin-top: auto; }
  .tbtn { font-size: 10px; padding: 4px 0; border-radius: 6px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--muted); white-space: nowrap; min-width: 0; overflow: hidden; }
  .tbtn:hover { color: var(--text); }
  .tbtn.on { color: #fff; border-color: transparent; }
  .tbtn.detail.on { background: #3a6ea5; } .tbtn.theme.on { background: #7a5bbf; } .tbtn.character.on { background: var(--accent); } .tbtn.skip.on { background: #555; }
  .tbtn.del { color: var(--muted); } .tbtn.del:hover { color: var(--bad); border-color: var(--bad); }
</style>

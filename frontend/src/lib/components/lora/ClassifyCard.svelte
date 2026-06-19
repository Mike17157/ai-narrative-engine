<script>
  // Classify (triage): render each on-disk LoRA against a checkpoint of its own
  // architecture, then tag it detail/theme/character/skip. Classifications land
  // in the shared library config (loraLib.cfg.library). Renders against the
  // shared global test prompt (img.testPrompt).
  import { get } from '$lib/api.js';
  import { img } from '$lib/images.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { openLightbox } from '$lib/lightbox.svelte.js';
  import Combobox from '$lib/components/Combobox.svelte';
  import ScrubInput from '$lib/components/ScrubInput.svelte';
  import {
    loraLib, famOf, famLabel, compat, baseFamMap,
  } from '$lib/lora-library.svelte.js';

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

  async function renderOne(item) {
    if (!selBase) { item.status = 'nockpt'; return; }
    item.status = 'gen'; item.pct = null; item.img = null;
    // bundled checkpoint → fast minimal graph; split base (Anima/DiT/Flux) →
    // inject the LoRA into the real workflow (split-loader aware).
    const minimal = !!selBase.checkpoint;
    const url = minimal ? '/api/loras/triage-render' : '/api/lora/sample';
    const body = minimal
      ? { checkpoint: selBase.checkpoint, lora: item.name, weight: +triageWeight || 0.8, prompt: img.testPrompt }
      : { model: baseModel, loras: [{ name: item.name, weight: +triageWeight || 0.8 }], prompt: img.testPrompt };
    try {
      const res = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') item.pct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
          else if (ev.type === 'image') item.img = (ev.images || [])[0] || null;
          else if (ev.type === 'error') { item.status = 'err'; item.err = ev.error; }
        }
      }
    } catch { item.status = 'err'; return; }
    if (item.status !== 'err') item.status = 'done';
  }

  async function startTriage() {
    if (triageRunning) { triageRunning = false; return; }  // toggle = stop
    if (!selBase) { loraLib.msg = { err: true, text: 'pick a workflow first' }; return; }
    triageRunning = true;
    for (const item of visibleTriage) {
      if (!triageRunning) break;
      if (item.img) continue;
      await renderOne(item);
    }
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
    <div><label>Test prompt <span class="sub">— shared with every LoRA test</span></label><input bind:value={img.testPrompt} placeholder="1girl, solo, standing…" /></div>
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
        <div class="cell" class:classified={it.type && it.type !== 'skip'}>
          <div class="thumb">
            {#if it.img}<button class="imgbtn" onclick={() => openLightbox(it.img, it.name)} title="click to enlarge"><img src={it.img} alt={it.name} /></button>
            {:else if it.status === 'gen'}<div class="ph">{it.pct !== null ? it.pct + '%' : '…'}</div>
            {:else if it.status === 'err'}<div class="ph err" title={it.err || ''}>failed</div>
            {:else if it.status === 'nockpt' || !selBase}<div class="ph err" title="pick a workflow above">no base</div>
            {:else}<button class="ph go" onclick={() => renderOne(it)} title="render this one">▶</button>{/if}
          </div>
          <div class="cap" title={it.name}><span class="ach">{famLabel()[it.fam] || it.fam}</span><span class="fn">{it.name.split(/[\\/]/).pop()}</span></div>
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
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .hint { font-size: 12.5px; color: var(--muted); }
  .warnsm { font-size: 11.5px; color: var(--muted); }
  .famhead { display: flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; color: var(--text);
             margin: 16px 0 8px; padding-bottom: 4px; border-bottom: 1px solid var(--border-soft); }
  .famhead .famn { font-size: 10.5px; font-weight: 600; color: var(--faint); background: var(--elev); border-radius: 999px; padding: 1px 8px; }
  .famhead .famx { font-size: 10px; font-weight: 600; color: #c9a6ff; background: rgba(124,109,255,.12); border-radius: 999px; padding: 1px 8px; text-transform: uppercase; letter-spacing: .3px; }
  .allchk { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted); margin-left: auto; }
  .allchk input { width: auto; }
  .trow { display: grid; gap: 12px; margin-bottom: 10px; }
  .trow label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }
  .trow2 { display: flex; gap: 10px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
  .m { font-size: 12.5px; color: var(--muted); }
  .grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-top: 14px; }
  .cell { display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--border-soft); border-radius: 10px; padding: 8px; background: var(--elev); }
  .cell.classified { border-color: var(--accent); }
  .thumb { aspect-ratio: 1; width: 100%; border-radius: 7px; overflow: hidden; background: var(--panel); display: grid; place-items: center; }
  .imgbtn { padding: 0; border: none; background: none; box-shadow: none; cursor: zoom-in; display: block; width: 100%; height: 100%; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .ph { color: var(--faint); font-size: 13px; width: 100%; height: 100%; display: grid; place-items: center; background: none; border: none; }
  .ph.err { color: var(--bad); } .ph.go { cursor: pointer; font-size: 20px; color: var(--muted); } .ph.go:hover { color: var(--accent); background: var(--elev-2); }
  .cap { display: flex; align-items: center; gap: 5px; font-size: 11px; margin: 7px 0 6px; min-width: 0; }
  .cap .fn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ach { flex: none; font-size: 9.5px; color: var(--faint); background: var(--panel); border: 1px solid var(--border-soft); border-radius: 4px; padding: 0 4px; }
  .types { display: grid; grid-template-columns: repeat(4, 1fr) 24px; gap: 3px; margin-top: auto; }
  .tbtn { font-size: 10px; padding: 4px 0; border-radius: 6px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--muted); box-shadow: none; white-space: nowrap; min-width: 0; overflow: hidden; }
  .tbtn:hover { color: var(--text); }
  .tbtn.on { color: #fff; border-color: transparent; }
  .tbtn.detail.on { background: #3a6ea5; } .tbtn.theme.on { background: #7a5bbf; } .tbtn.character.on { background: var(--accent); } .tbtn.skip.on { background: #555; }
  .tbtn.del { color: var(--muted); } .tbtn.del:hover { color: var(--bad); border-color: var(--bad); }
</style>

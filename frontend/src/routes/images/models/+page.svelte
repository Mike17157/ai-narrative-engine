<script>
  import { onMount } from 'svelte';
  import { get, del } from '$lib/api.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import WorkflowImport from '$lib/components/image/WorkflowImport.svelte';

  // The image-model surface, focused on the active pipeline: Anima + the support
  // models (VAE / CLIP / upscalers / ControlNet) it depends on. The full
  // cross-family library + organizer lives behind the Graph pane's "Model
  // library" modal — see ModelLibraryModal.svelte.
  let scan = $state({ items: [], counts: {} });
  let loading = $state(true);
  let err = $state(null);

  // browser filters (family is fixed to Anima + support here)
  let q = $state('');
  let kindFilter = $state('all');
  let sortBy = $state('name');  // name | size | recent  (no usage tracking — recent = mtime)

  // catalog (ComfyUI Manager) — browse + download new models
  let catalog = $state({ entries: [], types: [] });
  let catType = $state('Ultralytics');
  let catQ = $state('');
  let dl = $state({});  // rel -> pct (0-100) | 'done' | 'err: …'

  async function load() {
    loading = true; err = null;
    try {
      scan = await get('/comfy/models');
      catalog = await get('/comfy/catalog');
    } catch (e) { err = String(e); }
    loading = false;
  }
  onMount(load);

  let catItems = $derived((catalog.entries || []).filter((e) =>
    (catType === 'all' || e.type === catType) &&
    (!catQ.trim() || (e.name + ' ' + e.rel + ' ' + e.base).toLowerCase().includes(catQ.toLowerCase()))
  ));

  async function download(e) {
    if (typeof dl[e.rel] === 'number') return;  // already downloading
    dl[e.rel] = 0;
    try {
      const res = await fetch('/api/comfy/catalog/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: e.url, rel: e.rel })
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') dl[e.rel] = ev.total ? Math.round((ev.done / ev.total) * 100) : -1;
          else if (ev.type === 'done') { dl[e.rel] = 'done'; e.installed = true; }
          else if (ev.type === 'error') dl[e.rel] = 'err: ' + ev.error;
        }
      }
    } catch (ex) { dl[e.rel] = 'err: ' + ex; }
  }

  const KINDS = ['all', 'checkpoint', 'diffusion', 'lora', 'vae', 'clip', 'controlnet', 'upscale'];
  // Show only the active pipeline's models: the Anima family + support models (VAE/CLIP/
  // upscalers/ControlNet, which carry no family). Other families are managed in the
  // Graph pane's Model library modal.
  const inScope = (i) => i.family === 'anima' || !i.family;
  let items = $derived((scan.items || []).filter((i) =>
    inScope(i) &&
    (kindFilter === 'all' || i.kind === kindFilter) &&
    (!q.trim() || (i.folder + '/' + i.rel + ' ' + i.arch + ' ' + (i.family || '')).toLowerCase().includes(q.toLowerCase()))
  ).sort((a, b) =>
    sortBy === 'size' ? (b.size || 0) - (a.size || 0)
    : sortBy === 'recent' ? (b.mtime || 0) - (a.mtime || 0)
    : (a.folder + '/' + a.rel).localeCompare(b.folder + '/' + b.rel)
  ));
  function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b > 1e6 ? Math.round(b / 1e6) + ' MB' : Math.round(b / 1e3) + ' KB'; }

  async function delModel(i) {
    if (!await askConfirm({ title: `Delete ${i.rel}?`, danger: true, confirmLabel: 'Delete',
      message: `Permanently removes ${i.folder}/${i.rel} (${fmtSize(i.size)}) from disk.` })) return;
    const r = await del(`/comfy/models/file?folder=${encodeURIComponent(i.folder)}&rel=${encodeURIComponent(i.rel)}`);
    if (r?.ok) scan = await get('/comfy/models');
    else err = r?.error || 'delete failed';
  }
</script>

<WorkflowImport onimported={load} />

<div class="hint">The image models for the active pipeline — <b>Anima</b> plus the support models (VAE, CLIP, upscalers, ControlNet) it depends on. Browse other families and file misfiled / loose downloads from the <b>Graph</b> pane's <b>⊞ Model library</b>.</div>

{#if loading}
  <div class="center">Scanning model tree…</div>
{:else}
  {#if err}<div class="err">{err}</div>{/if}

  <!-- BROWSER -->
  <section class="card">
    <div class="chead">
      <h3>Library <span class="sub">{Object.entries(scan.counts).map(([k, v]) => `${v} ${k}`).join(' · ')}</span></h3>
      <div class="row">
        <select bind:value={kindFilter}>{#each KINDS as k}<option value={k}>{k}</option>{/each}</select>
        <select bind:value={sortBy} title="sort order">
          <option value="name">name</option>
          <option value="size">largest</option>
          <option value="recent">newest</option>
        </select>
        <input class="search" bind:value={q} placeholder="filter…" />
        <button class="ghost sm" onclick={load} disabled={loading}>↻ Re-scan</button>
      </div>
    </div>
    <div class="list">
      {#each items as i (i.folder + '/' + i.rel)}
        <div class="lrow">
          <span class="arch {i.arch}">{i.arch || '—'}</span>
          <span class="kind">{i.kind}</span>
          <span class="rel" title={i.folder + '/' + i.rel}><code class="top">{i.folder}/</code>{i.rel}</span>
          <span class="size">{fmtSize(i.size)}</span>
          <button class="del" onclick={() => delModel(i)} title="Delete this file from disk" aria-label="delete">×</button>
        </div>
      {/each}
      {#if !items.length}<div class="center">no models match.</div>{/if}
    </div>
  </section>

  <!-- CATALOG -->
  <section class="card">
    <div class="chead">
      <h3>Get models <span class="sub">{catalog.entries?.length || 0} in ComfyUI Manager catalog — download into the right folder, no searching</span></h3>
      <div class="row">
        <select bind:value={catType} title="type">
          <option value="all">all types</option>
          {#each catalog.types || [] as t}<option value={t}>{t}</option>{/each}
        </select>
        <input class="search" bind:value={catQ} placeholder="search catalog…" />
      </div>
    </div>
    {#if catalog.error}<div class="err">{catalog.error}</div>{/if}
    <div class="list">
      {#each catItems.slice(0, 80) as e (e.rel)}
        <div class="crow">
          <span class="kind">{e.type}</span>
          <span class="cname" title={`${e.name}\n${e.rel}`}>{e.name}{#if e.base}<span class="cbase">{e.base}</span>{/if}</span>
          {#if e.installed || dl[e.rel] === 'done'}
            <span class="ok">✓ installed</span>
          {:else if typeof dl[e.rel] === 'number'}
            <span class="dlpct">{dl[e.rel] < 0 ? 'downloading…' : dl[e.rel] + '%'}</span>
          {:else if typeof dl[e.rel] === 'string'}
            <span class="err" title={dl[e.rel]}>failed</span>
          {:else}
            <button class="ghost sm" onclick={() => download(e)}>Download</button>
          {/if}
        </div>
      {/each}
      {#if !catItems.length}<div class="center">no catalog entries match.</div>
      {:else if catItems.length > 80}<div class="more">showing 80 of {catItems.length} — refine the filter</div>{/if}
    </div>
  </section>
{/if}

<style>
  .hint { font-size: 12.5px; color: var(--muted); margin-bottom: 14px; }
  .center { display: grid; place-items: center; height: 24vh; color: var(--muted); font-size: 13px; }
  .err { color: var(--bad); font-size: 12.5px; }
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .chead { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  .chead h3 { margin: 0; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .search { width: 200px; }

  .list { display: flex; flex-direction: column; gap: 3px; }
  .lrow { display: grid; grid-template-columns: 70px 90px 1fr 80px 24px; gap: 10px; align-items: center; padding: 6px 8px; border-bottom: 1px solid var(--border-soft); font-size: 12.5px; }
  .del { width: 22px; height: 22px; padding: 0; font-size: 15px; line-height: 1; border-radius: 6px; box-shadow: none;
    background: none; border: 1px solid transparent; color: var(--faint); cursor: pointer; opacity: 0; transition: opacity .12s; }
  .lrow:hover .del { opacity: 1; }
  .del:hover { color: var(--bad); border-color: rgba(255,90,90,.4); background: rgba(255,90,90,.1); filter: none; }
  .kind { font-size: 11px; color: var(--muted); }
  .rel { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 12px; }
  .rel .top { color: var(--faint); }
  .size { font-size: 11px; color: var(--faint); justify-self: end; font-family: ui-monospace, monospace; }
  .crow { display: grid; grid-template-columns: 90px 1fr 110px; gap: 10px; align-items: center; padding: 6px 8px; border-bottom: 1px solid var(--border-soft); font-size: 12.5px; }
  .cname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cbase { color: var(--faint); font-size: 11px; margin-left: 8px; }
  .crow .ok { color: var(--good); font-size: 11.5px; justify-self: end; }
  .dlpct { color: var(--accent); font-size: 11.5px; justify-self: end; font-family: ui-monospace, monospace; }
  .crow .err { justify-self: end; }
  .crow button { justify-self: end; }
  .more { padding: 8px; font-size: 12px; color: var(--faint); text-align: center; }
  .arch { font-size: 11px; justify-self: start; border-radius: 999px; padding: 1px 9px; border: 1px solid var(--border-soft); color: var(--muted); text-align: center; }
  .arch.sdxl { color: #8fcaff; } .arch.dit { color: #c9a6ff; } .arch.flux { color: #ffcaa0; } .arch.sd15, .arch.sd { color: var(--muted); }
</style>

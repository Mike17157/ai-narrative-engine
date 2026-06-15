<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';

  // The model library, organized by architecture. Browse everything ComfyUI has
  // (classified by tensor signature, not folder), and file misfiled / loose
  // downloads into arch-correct folders — references rewritten automatically.
  let scan = $state({ items: [], counts: {} });
  let moves = $state([]);
  let warnings = $state([]);
  let sel = $state({});
  let loading = $state(true);
  let err = $state(null);
  let applying = $state(false);
  let result = $state(null);

  // browser filters
  let q = $state('');
  let kindFilter = $state('all');
  let familyFilter = $state('all');  // model type: Anima / Illustrious / Pony …

  // catalog (ComfyUI Manager) — browse + download new models
  let catalog = $state({ entries: [], types: [] });
  let catType = $state('Ultralytics');
  let catQ = $state('');
  let dl = $state({});  // rel -> pct (0-100) | 'done' | 'err: …'

  async function load() {
    loading = true; err = null; result = null;
    try {
      scan = await get('/comfy/models');
      const p = await get('/comfy/librarian');
      moves = p.moves || [];
      warnings = p.warnings || [];
      sel = Object.fromEntries(moves.map((m) => [key(m), true]));
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

  const key = (m) => m.src + '|' + m.top;
  const KINDS = ['all', 'checkpoint', 'diffusion', 'lora', 'vae', 'clip', 'controlnet', 'upscale'];
  let families = $derived(['all', ...[...new Set((scan.items || []).map((i) => i.arch_dir).filter(Boolean))].sort()]);
  let items = $derived((scan.items || []).filter((i) =>
    (kindFilter === 'all' || i.kind === kindFilter) &&
    (familyFilter === 'all' || i.arch_dir === familyFilter) &&
    (!q.trim() || (i.folder + '/' + i.rel + ' ' + i.arch).toLowerCase().includes(q.toLowerCase()))
  ));
  let chosen = $derived(moves.filter((m) => sel[key(m)]));
  function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + ' GB' : b > 1e6 ? Math.round(b / 1e6) + ' MB' : Math.round(b / 1e3) + ' KB'; }

  async function apply() {
    if (!chosen.length || applying) return;
    applying = true; result = null;
    const r = await post('/comfy/librarian/apply', { moves: chosen.map((m) => ({ top: m.top, src: m.src, dst: m.dst })) });
    result = r.data || { error: 'apply failed' };
    applying = false;
    await load();
  }
</script>

<div class="hint">Every model ComfyUI has, classified by tensor signature (kind + architecture) — not by folder name. Below, file misfiled or loose downloads into arch-correct folders; references in your stacks, characters and workflows are rewritten automatically.</div>

{#if loading}
  <div class="center">Scanning model tree…</div>
{:else}
  {#if err}<div class="err">{err}</div>{/if}

  <!-- ORGANIZE -->
  <section class="card">
    <div class="chead">
      <h3>Organize <span class="sub">— {moves.length ? `${moves.length} misfiled / loose` : 'all filed correctly ✓'}</span></h3>
      <div class="row">
        <button onclick={apply} disabled={applying || !chosen.length}>{applying ? 'Moving…' : `File ${chosen.length}`}</button>
        <button class="ghost sm" onclick={load} disabled={loading}>↻ Re-scan</button>
      </div>
    </div>
    {#if moves.length}
      <div class="hint2">Filing classified LoRAs into <code>&lt;family&gt;/&lt;classification&gt;/</code>. Family (model type) is preserved; classification comes from your tagging in LoRA → Classify.</div>
      <div class="warn">⚠ Moves files on disk and won't rewrite ComfyUI's own saved workflows — re-point those there.</div>
      <div class="moves">
        {#each moves as m (key(m))}
          <label class="mrow">
            <input type="checkbox" checked={sel[key(m)]} onchange={(e) => (sel[key(m)] = e.target.checked)} />
            <span class="cls {m.cls}">{m.cls}</span>
            <span class="mname" title={m.src}>{m.name}</span>
            <span class="path"><code class="top">loras/</code>{m.family} <span class="arr">→</span> {m.family}/<strong>{m.cls}</strong></span>
          </label>
        {/each}
      </div>
    {/if}
    {#if warnings.length}
      <div class="warnlist">
        <div class="wlbl">⚠ Possibly misfiled by architecture — review &amp; move manually in ComfyUI</div>
        {#each warnings as w}
          <div class="wrow"><code>{w.family}/</code>{w.name} — detected <span class="arch {w.arch}">{w.arch}</span>, but this folder is mostly <strong>{w.dominant}</strong></div>
        {/each}
      </div>
    {/if}
    {#if result}
      <div class="result">✓ filed {result.moved?.length || 0}{result.failed?.length ? `, ${result.failed.length} failed` : ''}{result.rewritten?.length ? ` · rewrote ${result.rewritten.join(', ')}` : ''}.
        {#each result.failed || [] as f}<div class="err">✕ {f.src}: {f.error}</div>{/each}
      </div>
    {/if}
  </section>

  <!-- BROWSER -->
  <section class="card">
    <div class="chead">
      <h3>Library <span class="sub">{Object.entries(scan.counts).map(([k, v]) => `${v} ${k}`).join(' · ')}</span></h3>
      <div class="row">
        <select bind:value={familyFilter} title="model type">{#each families as f}<option value={f}>{f === 'all' ? 'all types' : f}</option>{/each}</select>
        <select bind:value={kindFilter}>{#each KINDS as k}<option value={k}>{k}</option>{/each}</select>
        <input class="search" bind:value={q} placeholder="filter…" />
      </div>
    </div>
    <div class="list">
      {#each items as i (i.folder + '/' + i.rel)}
        <div class="lrow">
          <span class="arch {i.arch}">{i.arch || '—'}</span>
          <span class="kind">{i.kind}</span>
          <span class="rel" title={i.folder + '/' + i.rel}><code class="top">{i.folder}/</code>{i.rel}</span>
          <span class="size">{fmtSize(i.size)}</span>
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

  .warn { font-size: 12px; color: var(--warn, #e6b800); background: rgba(230,184,0,.07); border: 1px solid rgba(230,184,0,.25); border-radius: 8px; padding: 8px 11px; margin-bottom: 12px; }
  .warn code { background: var(--elev); padding: 0 4px; border-radius: 4px; }
  .result { font-size: 12.5px; color: var(--good); margin-top: 10px; }

  .moves, .list { display: flex; flex-direction: column; gap: 3px; }
  .mrow { display: grid; grid-template-columns: 24px 80px 1.4fr 2fr; gap: 10px; align-items: center; padding: 7px 8px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); cursor: pointer; }
  .mrow:hover { border-color: var(--border); }
  .hint2 { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .hint2 code { background: var(--elev); padding: 0 4px; border-radius: 4px; }
  .cls { font-size: 11px; justify-self: start; border-radius: 999px; padding: 1px 9px; border: 1px solid var(--border-soft); color: var(--muted); }
  .cls.detail { color: #8fcaff; } .cls.theme { color: #c9a6ff; } .cls.character { color: var(--good); }
  .warnlist { margin-top: 12px; border: 1px solid rgba(230,184,0,.25); border-radius: 8px; padding: 8px 11px; }
  .wlbl { font-size: 11px; color: var(--warn, #e6b800); margin-bottom: 6px; }
  .wrow { font-size: 12px; color: var(--muted); padding: 2px 0; }
  .wrow code { color: var(--faint); }
  .lrow { display: grid; grid-template-columns: 70px 90px 1fr 80px; gap: 10px; align-items: center; padding: 6px 8px; border-bottom: 1px solid var(--border-soft); font-size: 12.5px; }
  .mname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; }
  .kind { font-size: 11px; color: var(--muted); }
  .rel, .path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 12px; }
  .rel .top, .path .top { color: var(--faint); } .arr { color: var(--accent); margin: 0 4px; }
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

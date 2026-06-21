<script>
  import { get, post } from '$lib/api.js';
  import Modal from '$lib/components/shared/Modal.svelte';

  // The full, cross-family model library + organizer — relocated here from the Images → Models
  // pane (which is now Anima-focused). This is the power-user surface for the OTHER families
  // (Illustrious / Pony / SDXL …): browse everything ComfyUI has, and file misfiled / loose
  // downloads into arch- or family-correct folders (references rewritten automatically).
  let { open = false, onclose = () => {}, onapplied = () => {} } = $props();

  let scan = $state({ items: [], counts: {} });
  let moves = $state([]);
  let warnings = $state([]);
  let sel = $state({});
  let loading = $state(false);
  let err = $state(null);
  let applying = $state(false);
  let result = $state(null);
  let orgMode = $state('role');  // 'role' = classify-folder moves · 'family' = categorize-by-name moves

  // browser filters
  let q = $state('');
  let kindFilter = $state('all');
  let familyFilter = $state('all');

  const key = (m) => m.src + '|' + m.top;
  const KINDS = ['all', 'checkpoint', 'diffusion', 'lora', 'vae', 'clip', 'controlnet', 'upscale'];
  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : f);

  async function load() {
    loading = true; err = null; result = null;
    try {
      scan = await get('/comfy/models');
      const p = await get('/comfy/librarian');
      moves = p.moves || [];
      warnings = p.warnings || [];
      sel = Object.fromEntries(moves.map((m) => [key(m), true]));
      orgMode = 'role';
    } catch (e) { err = String(e); }
    loading = false;
  }

  // (Re)load the scan whenever the modal opens.
  let wasOpen = false;
  $effect(() => {
    if (open && !wasOpen) { wasOpen = true; load(); }
    if (!open) wasOpen = false;
  });

  // "Categorize by name": file every model into its family folder (Illustrious/, Pony/, Anima/, …)
  // derived from its name/folder/arch. Loads proposals into the same review list; Apply moves + rewrites.
  async function categorizeByName() {
    loading = true; err = null; result = null;
    try {
      const r = await get('/comfy/librarian/family');
      moves = r.moves || [];
      sel = Object.fromEntries(moves.map((m) => [key(m), true]));
      orgMode = 'family';
    } catch (e) { err = String(e); }
    loading = false;
  }

  // Real sub-family containers (illustrious/pony/anima/…) from the scan, not folder names.
  let families = $derived(['all', ...[...new Set((scan.items || []).map((i) => i.family).filter(Boolean))].sort()]);
  let items = $derived((scan.items || []).filter((i) =>
    (kindFilter === 'all' || i.kind === kindFilter) &&
    (familyFilter === 'all' || i.family === familyFilter) &&
    (!q.trim() || (i.folder + '/' + i.rel + ' ' + i.arch + ' ' + (i.family || '')).toLowerCase().includes(q.toLowerCase()))
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
    onapplied();
  }
</script>

<Modal {open} onClose={onclose} flush width="920px" maxHeight="92vh">
      <div class="head">
        <h3>Model library <span class="sub">— all families: browse &amp; organize what ComfyUI has</span></h3>
        <button class="x" onclick={onclose} aria-label="Close">✕</button>
      </div>

      <div class="body">
        {#if loading}
          <div class="center">Scanning model tree…</div>
        {:else}
          {#if err}<div class="err">{err}</div>{/if}

          <!-- ORGANIZE -->
          <section class="card">
            <div class="chead">
              <h4>Organize <span class="sub">— {moves.length ? `${moves.length} ${orgMode === 'family' ? 'to file by family' : 'misfiled / loose'}` : 'all filed correctly ✓'}</span></h4>
              <div class="row">
                <button class="ghost sm" onclick={categorizeByName} disabled={loading} title="File every model into its family folder (Illustrious/, Pony/, Anima/, SDXL/…) by name">⊞ Categorize by name</button>
                <button onclick={apply} disabled={applying || !chosen.length}>{applying ? 'Moving…' : `File ${chosen.length}`}</button>
                <button class="ghost sm" onclick={load} disabled={loading}>↻ Re-scan</button>
              </div>
            </div>
            {#if moves.length}
              {#if orgMode === 'family'}
                <div class="hint2">Filing each model into its <code>&lt;Family&gt;/</code> folder (one folder per type); LoRA role subfolders are kept.</div>
              {:else}
                <div class="hint2">Filing classified LoRAs into <code>&lt;family&gt;/&lt;classification&gt;/</code>. Family (model type) is preserved; classification comes from your tagging in LoRA → Classify.</div>
              {/if}
              <div class="warn">⚠ Moves files on disk and won't rewrite ComfyUI's own saved workflows — re-point those there.</div>
              <div class="moves">
                {#each moves as m (key(m))}
                  <label class="mrow">
                    <input type="checkbox" checked={sel[key(m)]} onchange={(e) => (sel[key(m)] = e.target.checked)} />
                    {#if orgMode === 'family'}
                      <span class="cls {m.family}">{m.family}</span>
                      <span class="mname" title={m.src}>{m.name}</span>
                      <span class="path"><code class="top">{m.top}/</code>{m.src} <span class="arr">→</span> <strong>{m.dst}</strong></span>
                    {:else}
                      <span class="cls {m.cls}">{m.cls}</span>
                      <span class="mname" title={m.src}>{m.name}</span>
                      <span class="path"><code class="top">loras/</code>{m.family} <span class="arr">→</span> {m.family}/<strong>{m.cls}</strong></span>
                    {/if}
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

          <!-- BROWSER (all families) -->
          <section class="card">
            <div class="chead">
              <h4>Library <span class="sub">{Object.entries(scan.counts).map(([k, v]) => `${v} ${k}`).join(' · ')}</span></h4>
              <div class="row">
                <select bind:value={familyFilter} title="model family">{#each families as f}<option value={f}>{f === 'all' ? 'all families' : famCap(f)}</option>{/each}</select>
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
        {/if}
      </div>
</Modal>

<style>
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--border); }
  .head h3 { margin: 0; font-size: 16px; }
  .head .x { background: none; box-shadow: none; color: var(--faint); padding: 4px 8px; font-size: 15px; }
  .head .x:hover { color: var(--text); filter: none; }
  .body { overflow-y: auto; padding: 16px 20px; }

  .center { display: grid; place-items: center; height: 18vh; color: var(--muted); font-size: 13px; }
  .err { color: var(--bad); font-size: 12.5px; }
  .card { background: var(--elev); border: 1px solid var(--border-soft); border-radius: 14px; padding: 14px 16px; margin-bottom: 14px; }
  .card:last-child { margin-bottom: 0; }
  .chead { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  .chead h4 { margin: 0; font-size: 14px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .row { display: flex; align-items: center; gap: 8px; }
  .search { width: 180px; }

  .warn { font-size: 12px; color: var(--warn, #e6b800); background: rgba(230,184,0,.07); border: 1px solid rgba(230,184,0,.25); border-radius: 8px; padding: 8px 11px; margin-bottom: 12px; }
  .result { font-size: 12.5px; color: var(--good); margin-top: 10px; }

  .moves, .list { display: flex; flex-direction: column; gap: 3px; }
  .mrow { display: grid; grid-template-columns: 24px 80px 1.4fr 2fr; gap: 10px; align-items: center; padding: 7px 8px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--panel); cursor: pointer; }
  .mrow:hover { border-color: var(--border); }
  .hint2 { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
  .hint2 code { background: var(--panel); padding: 0 4px; border-radius: 4px; }
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
  .arch { font-size: 11px; justify-self: start; border-radius: 999px; padding: 1px 9px; border: 1px solid var(--border-soft); color: var(--muted); text-align: center; }
  .arch.sdxl { color: #8fcaff; } .arch.dit { color: #c9a6ff; } .arch.flux { color: #ffcaa0; } .arch.sd15, .arch.sd { color: var(--muted); }
</style>

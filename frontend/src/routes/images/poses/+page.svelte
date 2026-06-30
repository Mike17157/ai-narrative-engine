<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';

  // Two parts: (1) global shot GEOMETRY per emotion (camera crop + canvas) — geometry, not personality;
  // (2) per-character BODY LANGUAGE — generated from the persona (no static library), editable as prose.
  let geom = $state([]);           // [{ key, label, framing, aspect, custom }]
  let framings = $state([]);
  let aspects = $state([]);
  let loading = $state(true);
  let saved = $state('');
  const gtimers = {};

  async function loadGeom() {
    const d = await get('/poses');
    geom = d.poses || []; framings = d.framings || []; aspects = d.aspects || [];
    loading = false;
  }

  // --- curated pose palette (the real-tag library the model picks from) ---
  let lib = $state({ facets: [], count: 0 });
  let libOpen = $state(false);
  async function loadLib() { lib = (await get('/pose-library')) || { facets: [], count: 0 }; }
  onMount(() => { loadGeom(); loadLib(); loadChars(); });

  async function saveGeom(row, field, v) {
    row[field] = v;
    const r = await post('/poses', { key: row.key, framing: row.framing, aspect: row.aspect });
    if (r.ok) { saved = 'g:' + row.key; setTimeout(() => (saved = saved === 'g:' + row.key ? '' : saved), 1200); }
  }

  // --- per-character body language ---
  let charItems = $derived((chars.list || []).map((c) => ({ value: c.key, label: c.name || c.key })));
  let curChar = $state('');
  let poses = $state([]);          // [{ key, label, tags }]
  let composing = $state(false);
  let cmsg = $state('');
  const ptimers = {};

  async function loadCharPoses(k) {
    poses = [];
    if (!k) return;
    poses = (await get(`/characters/${k}/poses`)).poses || [];
  }
  function pickChar(v) { curChar = v; loadCharPoses(v); }

  function editPose(row, v) {
    row.tags = v;
    clearTimeout(ptimers[row.key]);
    ptimers[row.key] = setTimeout(() => savePose(row), 700);
  }
  async function savePose(row) {
    const r = await post(`/characters/${curChar}/poses`, { emotion: row.key, tags: (row.tags || '').trim() });
    if (r.ok) { saved = 'p:' + row.key; setTimeout(() => (saved = saved === 'p:' + row.key ? '' : saved), 1200); }
  }
  async function composeAll() {
    if (!curChar || composing) return;
    composing = true; cmsg = 'Composing from persona…';
    const r = await post(`/characters/${curChar}/poses`, { compose: true });
    composing = false;
    if (r.ok && r.data?.poses) { poses = poses.map((p) => ({ ...p, tags: r.data.poses[p.key] || '' })); cmsg = '✓ composed'; }
    else cmsg = r.data?.error || 'compose failed';
    setTimeout(() => (cmsg = ''), 2000);
  }
</script>

<div class="hint">
  Body language is <b>generated per character</b> from their persona (no static library) — a timid
  character's anger differs from a brash one's. Below: <b>global shot geometry</b> per emotion (camera
  crop + canvas — not personality), then a character's <b>body-language</b> tags (composed from persona,
  editable). Standing emotions default to a 3/4 cowboy/portrait; <b>neutral</b> (base image) to full
  body/tall; flip lying/lewd ones to full body/landscape.
</div>

{#if loading}
  <div class="hint" style="margin-top:14px">Loading…</div>
{:else}
  <!-- GEOMETRY (global) -->
  <section class="card">
    <h3>Shot geometry <span class="sub">— camera crop + canvas per emotion (global)</span></h3>
    <div class="rows">
      {#each geom as row (row.key)}
        <div class="grow">
          <span class="nm">{row.label}{#if row.custom}<span class="badge">custom</span>{/if}{#if saved === 'g:' + row.key}<span class="ok">✓</span>{/if}</span>
          <select class="sel" value={row.framing} onchange={(e) => saveGeom(row, 'framing', e.currentTarget.value)} title="camera crop">
            {#each framings as f}<option value={f}>{f === 'fullbody' ? 'full body' : f}</option>{/each}
          </select>
          <select class="sel" value={row.aspect} onchange={(e) => saveGeom(row, 'aspect', e.currentTarget.value)} title="latent canvas">
            {#each aspects as a}<option value={a}>{a}</option>{/each}
          </select>
        </div>
      {/each}
    </div>
  </section>

  <!-- PER-CHARACTER BODY LANGUAGE -->
  <section class="card">
    <h3>Character body language <span class="sub">— composed from persona; edit as booru tags</span></h3>
    <div class="charrow">
      <div class="cpick"><Combobox items={charItems} value={curChar} placeholder="pick a character…" onpick={pickChar} /></div>
      {#if curChar}
        <button class="ghost sm" onclick={composeAll} disabled={composing}>{composing ? 'Composing…' : '✨ Compose all from persona'}</button>
        {#if cmsg}<span class="cmsg">{cmsg}</span>{/if}
      {/if}
    </div>
    {#if curChar && poses.length}
      <div class="rows">
        {#each poses as row (row.key)}
          <div class="prow">
            <span class="nm">{row.label}{#if saved === 'p:' + row.key}<span class="ok">✓</span>{/if}</span>
            <input class="fld" value={row.tags} placeholder="(empty — compose from persona)"
              oninput={(e) => editPose(row, e.currentTarget.value)} />
          </div>
        {/each}
      </div>
    {:else if curChar}
      <p class="hint" style="margin-top:8px">No body language yet — <b>Compose all from persona</b> (or it's generated when the character is regenerated).</p>
    {/if}
  </section>

  <!-- CURATED POSE PALETTE (the real-tag library the model picks from) -->
  <section class="card">
    <button class="libhdr" onclick={() => (libOpen = !libOpen)}>
      <h3 style="margin:0">Pose palette <span class="sub">— {lib.count} real booru pose tags the model draws from</span></h3>
      <span class="chev">{libOpen ? '▾' : '▸'}</span>
    </button>
    {#if libOpen}
      <p class="hint" style="margin:8px 0 12px">
        Composed body language is built by <b>picking from these real tags</b> (per facet), personalized to
        the persona — so poses stay grounded in vocabulary the image model understands. Edit
        <code>configs/pose_library.json</code> to curate it.
      </p>
      <div class="facets">
        {#each lib.facets as f (f.key)}
          <div class="facet">
            <div class="fname">{f.key} <span class="sub">— {f.desc}</span></div>
            <div class="tags">{#each f.tags as t}<span class="tag">{t}</span>{/each}</div>
          </div>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .hint { line-height: 1.55; }
  .card { border-radius: 12px; padding: 14px 16px; margin-top: 14px; }
  .card h3 { margin: 0 0 10px; font-size: 14px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12px; }
  .rows { display: flex; flex-direction: column; gap: 6px; }
  .grow { display: grid; grid-template-columns: 1fr 130px 130px; gap: 10px; align-items: center; }
  .prow { display: grid; grid-template-columns: 150px 1fr; gap: 10px; align-items: center; }
  .nm { font-size: 13px; font-weight: 600; color: var(--text); display: flex; align-items: center; gap: 7px; }
  .badge { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--accent); background: rgba(124,109,255,.14); border-radius: 999px; padding: 1px 7px; }
  .ok { font-size: 13px; }
  .sel, .fld { padding: 6px 9px; font-size: 12.5px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .fld:focus, .sel:focus { border-color: var(--accent); outline: none; }
  .charrow { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  .cpick { width: 260px; }
  .cmsg { font-size: 12px; color: var(--muted); }
  .libhdr { display: flex; align-items: center; justify-content: space-between; width: 100%; background: none; border: 0; padding: 0; cursor: pointer; color: var(--text); }
  .chev { color: var(--faint); font-size: 13px; }
  code { font-size: 11.5px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 1px 5px; }
  .facets { display: flex; flex-direction: column; gap: 12px; }
  .facet { display: flex; flex-direction: column; gap: 6px; }
  .fname { font-size: 12.5px; font-weight: 700; color: var(--text); text-transform: capitalize; }
  .tags { display: flex; flex-wrap: wrap; gap: 5px; }
  .tag { font-size: 11.5px; color: var(--muted); background: var(--bg); border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px; }
</style>

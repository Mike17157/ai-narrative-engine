<script>
  // Grid tester: render the same prompt across N checkpoints (rows) × M LoRAs (columns).
  // Anima family only. Selection uses a tag-input pattern: selected chips live in a
  // bordered field, available items sit below as a clickable pool.
  //
  // Drag protocol: text/plain JSON {type:'checkpoint'|'lora', name:'...'} (from ClassifyCard).
  import { img } from '$lib/images.svelte.js';
  import { loraLib, famOf } from '$lib/lora-library.svelte.js';
  import ImgCard from '$lib/components/ImgCard.svelte';

  // --- selection state ---
  let selCkpts = $state([]);   // string[]
  let selLoras = $state([]);   // {name, weights: number[]}[]
  let running  = $state(false);
  let wm       = $state(null); // weight modal

  // --- pools (derived, reactive) ---
  // Checkpoints: scan items flagged as anima family, kind checkpoint or diffusion.
  // Using scan (same source as the images/models page) because its family detection
  // works from folder name / filename hints and doesn't need the models dir to be
  // mounted — unlike the lora_bases endpoint which needs arch classification from file.
  let animaCkpts = $derived(
    (loraLib.scan?.items || [])
      .filter((i) => (i.kind === 'checkpoint' || i.kind === 'diffusion') && i.family === 'anima')
      .map((i) => i.rel)
  );
  // LoRAs: same approach — anima family from scan.
  let animaLoras = $derived(
    (loraLib.choices.loras || []).filter((n) => famOf(n) === 'anima')
  );

  // Available = pool minus already selected
  let availCkpts = $derived(animaCkpts.filter((v) => !selCkpts.includes(v)));
  let availLoras = $derived(animaLoras.filter((n) => !selLoras.find((l) => l.name === n)));

  // External items dragged in that aren't in the anima pool
  let extraCkpts = $derived(selCkpts.filter((v) => !animaCkpts.includes(v)));
  let extraLoras = $derived(selLoras.filter((l) => !animaLoras.includes(l.name)));

  // --- toggle helpers ---
  function addCkpt(v) { if (v && !selCkpts.includes(v)) selCkpts = [...selCkpts, v]; }
  function rmCkpt(v)  { selCkpts = selCkpts.filter((c) => c !== v); }
  function addLora(v) { if (v && !selLoras.find((l) => l.name === v)) selLoras = [...selLoras, { name: v, weights: [0.8] }]; }
  function rmLora(n)  { selLoras = selLoras.filter((l) => l.name !== n); }

  // --- drag drop ---
  let czDrag = $state(false);
  let lzDrag = $state(false);

  function ckptDrop(e) {
    e.preventDefault(); czDrag = false;
    try { const d = JSON.parse(e.dataTransfer.getData('text/plain')); if (d?.type === 'checkpoint') addCkpt(d.name); } catch {}
  }
  function loraDrop(e) {
    e.preventDefault(); lzDrag = false;
    try { const d = JSON.parse(e.dataTransfer.getData('text/plain')); if (d?.type === 'lora') addLora(d.name); } catch {}
  }
  function dragOver(e, set) {
    if (!e.dataTransfer.types.includes('text/plain')) return;
    e.preventDefault(); set(true);
  }
  function dragLeave(e, set) { if (!e.currentTarget.contains(e.relatedTarget)) set(false); }

  // --- derived columns ---
  let columns = $derived(
    selLoras.flatMap((l) => l.weights.map((w) => ({ lora: l.name, weight: w })))
  );

  // --- cell state ---
  let cells = $state({});
  const ck = (ckpt, lora, w) => `${ckpt}||${lora}||${w}`;

  async function renderCell(ckpt, lora, weight) {
    const k = ck(ckpt, lora, weight);
    cells[k] = { img: null, status: 'gen', pct: null, err: null };
    try {
      const res = await fetch('/api/loras/triage-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ checkpoint: ckpt, lora, weight, prompt: img.testPrompt }),
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') cells[k].pct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
          else if (ev.type === 'image') cells[k].img = (ev.images || [])[0] || null;
          else if (ev.type === 'error') { cells[k].status = 'err'; cells[k].err = ev.error; }
        }
      }
    } catch (e) { cells[k] = { img: null, status: 'err', pct: null, err: String(e) }; return; }
    if (cells[k]?.status !== 'err') cells[k].status = 'done';
  }

  async function renderAll() {
    if (running) { running = false; return; }
    if (!selCkpts.length || !columns.length) return;
    running = true;
    outer: for (const ckpt of selCkpts) {
      for (const col of columns) {
        if (!running) break outer;
        if (cells[ck(ckpt, col.lora, col.weight)]?.img) continue;
        await renderCell(ckpt, col.lora, col.weight);
      }
    }
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

  // --- progress ---
  let totalCells = $derived(selCkpts.length * columns.length);
  let doneCells  = $derived(
    selCkpts.reduce((s, ckpt) => s + columns.filter((c) => cells[ck(ckpt, c.lora, c.weight)]?.img).length, 0)
  );
  const shortName = (p) => p.split(/[/\\]/).pop();
  const weightLabel = (l) => l.weights.length === 1 ? `@${l.weights[0]}` : `@${l.weights[0]}–${l.weights[l.weights.length-1]} ×${l.weights.length}`;
</script>

<div class="gt">
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
      <div class="axis-label">Checkpoints <span class="ax-hint">rows</span></div>

      <!-- tag-input field: selected chips only -->
      <div
        class="tag-field"
        class:dragover={czDrag}
        role="group"
        ondragover={(e) => dragOver(e, (v) => czDrag = v)}
        ondragleave={(e) => dragLeave(e, (v) => czDrag = v)}
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
          <span class="field-ph">{czDrag ? '↓ drop here' : 'click below or drag in a checkpoint…'}</span>
        {:else if czDrag}
          <span class="field-ph drop">↓ drop to add</span>
        {/if}
      </div>

      <!-- available pool -->
      {#if availCkpts.length}
        <div class="pool">
          {#each availCkpts as v (v)}
            <button class="poolchip" onclick={() => addCkpt(v)} title={v}>{shortName(v)}</button>
          {/each}
        </div>
      {:else if !animaCkpts.length}
        <p class="ax-empty">No anima checkpoints found in scan.</p>
      {/if}
    </div>

    <!-- ── LoRAs ── -->
    <div class="axis">
      <div class="axis-label">LoRAs <span class="ax-hint">columns · click tag to set weight</span></div>

      <!-- tag-input field: selected chips only -->
      <div
        class="tag-field"
        class:dragover={lzDrag}
        role="group"
        ondragover={(e) => dragOver(e, (v) => lzDrag = v)}
        ondragleave={(e) => dragLeave(e, (v) => lzDrag = v)}
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
          <span class="field-ph">{lzDrag ? '↓ drop here' : 'click below or drag in a LoRA…'}</span>
        {:else if lzDrag}
          <span class="field-ph drop">↓ drop to add</span>
        {/if}
      </div>

      <!-- available pool -->
      {#if availLoras.length}
        <div class="pool">
          {#each availLoras as n (n)}
            <button class="poolchip" onclick={() => addLora(n)} title={n}>{shortName(n)}</button>
          {/each}
        </div>
      {:else if !animaLoras.length}
        <p class="ax-empty">No anima LoRAs found.</p>
      {/if}
    </div>
  </div>

  <!-- Run bar -->
  <div class="runbar">
    <button onclick={renderAll} disabled={!selCkpts.length || !columns.length}>
      {running ? '■ Stop' : doneCells > 0 ? 'Resume' : 'Render all'}
    </button>
    {#if totalCells > 0}
      <span class="m">{doneCells}/{totalCells} · {selCkpts.length} ckpt × {columns.length} col</span>
    {/if}
    {#if running}<span class="m pulse">generating…</span>{/if}
  </div>

  <!-- Grid -->
  <div class="grid-wrap">
    {#if !selCkpts.length || !columns.length}
      <div class="grid-empty">
        {#if !selCkpts.length && !columns.length}Select checkpoints and LoRAs above.
        {:else if !selCkpts.length}Select at least one checkpoint.
        {:else}Select at least one LoRA.{/if}
      </div>
    {:else}
      <div class="grid" style="grid-template-columns: 150px repeat({columns.length}, 180px);">
        <div class="corner"></div>
        {#each columns as col (`${col.lora}||${col.weight}`)}
          <div class="ch">
            <span class="cn" title={col.lora}>{shortName(col.lora)}</span>
            <span class="cw">@{col.weight}</span>
          </div>
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

<!-- Weight range modal -->
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
  .gt { display: flex; flex-direction: column; gap: 14px; }
  .gt-head { display: flex; align-items: baseline; gap: 10px; }
  .gt-title { font-size: 15px; font-weight: 700; color: var(--text); }
  .gt-sub { font-size: 12px; color: var(--faint); }

  .prompt-row label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
  .tp { width: 100%; resize: vertical; font: inherit; line-height: 1.4; }

  /* Two-column axis layout */
  .axes { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .axis { display: flex; flex-direction: column; gap: 6px; }
  .axis-label { font-size: 11.5px; font-weight: 600; color: var(--text); }
  .ax-hint { font-weight: 400; font-size: 11px; color: var(--faint); margin-left: 4px; }
  .ax-empty { font-size: 12px; color: var(--faint); margin: 2px 0 0; }

  /* Tag-input field (selected items) */
  .tag-field {
    display: flex; flex-wrap: wrap; gap: 5px;
    min-height: 40px; padding: 6px 8px;
    border: 1.5px solid var(--border-soft); border-radius: 9px;
    background: var(--elev);
    align-items: flex-start;
    transition: border-color .12s;
  }
  .tag-field.dragover { border-color: var(--accent); background: rgba(109,140,255,.07); }

  .field-ph { font-size: 11.5px; color: var(--faint); align-self: center; }
  .field-ph.drop { color: var(--accent); }

  /* Tags (selected) */
  .tag {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 5px 3px 8px; border-radius: 6px; font-size: 11.5px;
    user-select: none; white-space: nowrap; font: inherit;
  }
  .tag.ckpt {
    background: rgba(109,140,255,.14); border: 1px solid rgba(109,140,255,.35);
    color: var(--text); cursor: default;
  }
  .tag.lora {
    background: rgba(109,140,255,.18); border: 1px solid var(--accent);
    color: var(--accent); cursor: pointer;
  }
  .tag.lora:hover { background: rgba(109,140,255,.28); }
  .tname { max-width: 130px; overflow: hidden; text-overflow: ellipsis; }
  .tw { font-size: 10px; color: var(--muted); }
  .ext-badge { font-size: 9px; padding: 0 4px; background: rgba(255,255,255,.08); border-radius: 3px; color: var(--faint); }
  .tx {
    font-size: 14px; line-height: 1; padding: 0 1px; color: var(--faint); flex: none;
    cursor: pointer; border: none; background: none; display: inline;
  }
  .tx:hover { color: var(--bad); }

  /* Available pool */
  .pool {
    display: flex; flex-wrap: wrap; gap: 4px;
    max-height: 96px; overflow-y: auto;
    padding: 4px 0;
  }
  .poolchip {
    font-size: 11px; padding: 2px 8px; border-radius: 5px;
    background: var(--panel); border: 1px solid var(--border-soft);
    color: var(--muted); cursor: pointer; white-space: nowrap; font: inherit;
  }
  .poolchip:hover { color: var(--text); border-color: rgba(255,255,255,.22); }

  /* Run bar */
  .runbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .m { font-size: 12.5px; color: var(--muted); }
  .pulse { color: var(--accent); animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.45} }

  /* Grid */
  .grid-wrap {
    border: 1px solid var(--border-soft); border-radius: 10px;
    overflow-x: auto; min-height: 100px; background: var(--panel);
  }
  .grid-empty { display: flex; align-items: center; justify-content: center; height: 100px; color: var(--faint); font-size: 13px; }
  .grid { display: grid; gap: 1px; background: var(--border-soft); min-width: max-content; }
  .corner { background: var(--panel); }
  .ch { background: var(--elev); padding: 6px 8px; font-size: 11px; display: flex; flex-direction: column; gap: 2px; justify-content: flex-end; min-height: 50px; min-width: 180px; }
  .cn { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cw { color: var(--accent); font-size: 10.5px; font-weight: 700; }
  .rh { background: var(--elev); padding: 6px 8px; font-size: 10.5px; color: var(--text); display: flex; align-items: center; word-break: break-all; line-height: 1.35; min-width: 150px; }
  .gcell { position: relative; width: 180px; height: 180px; background: var(--panel); }
  .thumb { width: 100%; height: 100%; }
  .gpct { position: absolute; bottom: 4px; left: 50%; transform: translateX(-50%); font-size: 10px; color: #fff; background: rgba(0,0,0,.65); border-radius: 4px; padding: 1px 6px; pointer-events: none; }
  .gerr { position: absolute; top: 4px; right: 4px; font-size: 10px; font-weight: 700; color: var(--bad); background: rgba(255,60,60,.15); border-radius: 4px; padding: 1px 5px; }

  /* Modal */
  .overlay { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 200; display: flex; align-items: center; justify-content: center; }
  .modal { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 20px 22px; width: 320px; max-width: 90vw; }
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

<script>
  // Dataset viewer + captioning. View a saved dataset and (re)caption each image
  // with a vision model (OpenRouter) or the local WD14 booru tagger. Captions
  // write to the per-image .txt files used by training. Self-contained: owns
  // its datasets list, captioner config, WD14 status, and caption-job stream.
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import Combobox from '$lib/components/Combobox.svelte';

  let datasets = $state([]);
  let dataset = $state('');
  let detail = $state(null);          // { name, count, images: [{file, caption}] }

  let cfg = $state({ enabled: true, model: '', system: '' });
  let tm = $state({ models: [] });
  let cfgLoaded = $state(false);
  let cfgMsg = $state(null);
  let cfgTimer;
  let cardSize = $state(190);   // grid card target width (px), driven by the slider
  let capH = $state(64);        // caption box height (px), driven by the second slider

  let busy = $state(false);
  let progress = $state({ done: 0, total: 0 });
  let capMsg = $state(null);

  // captioning method: vlm (OpenRouter vision) | wd14 (local booru tagger)
  let method = $state('vlm');
  let wd14 = $state(null);          // { available, need_install, reason }
  let wdRepo = $state('SmilingWolf/wd-vit-tagger-v3');
  let wdGen = $state(0.35);
  let wdChar = $state(0.85);
  let installing = $state(false);

  let dsItems = $derived(datasets.map((d) => ({ value: d.name, label: `${d.name} — ${d.count} img` })));
  let modelItems = $derived([{ value: '', label: '(use active chat model — needs vision)' }, ...tm.models.map((m) => ({ value: m.id, label: m.name }))]);

  async function loadDatasets() { datasets = (await get('/lora/datasets')).datasets || []; }
  // Re-fetch the dataset list after a Save LoRA Set lands a new one — the host
  // page can call this once GenerateCard saves.
  export async function refresh() { await loadDatasets(); }

  async function selectDataset(name) {
    dataset = name;
    detail = await get('/lora/datasets/' + encodeURIComponent(name));
  }

  onMount(async () => {
    await loadDatasets();
    cfg = await get('/captioner');
    tm = await get('/text-models');
    wd14 = await get('/lora/wd14/status');
    cfgLoaded = true;
    // Reattach to a captioning run already in flight (survives navigation).
    const cj = await get('/lora/caption/job');
    if (cj && cj.status === 'running') {
      if (cj.method) method = cj.method;
      await selectDataset(cj.dataset);
      attach();
    } else if (datasets[0]) {
      await selectDataset(datasets[0].name);
    }
  });

  async function installWd14() {
    installing = true; capMsg = { text: 'installing onnxruntime into the trainer venv…' };
    try {
      const res = await fetch('/api/lora/wd14/install', { method: 'POST' });
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2);
          if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'log') capMsg = { text: ev.line };
        }
      }
    } catch (e) { capMsg = { err: true, text: String(e) }; }
    wd14 = await get('/lora/wd14/status');
    installing = false;
    capMsg = wd14.available ? { ok: true, text: '✓ WD14 ready' } : { err: true, text: wd14.reason || 'install failed' };
  }

  // Auto-save the captioner config (debounced), like Prompt Gen.
  $effect(() => {
    const snap = JSON.stringify($state.snapshot(cfg));
    if (!cfgLoaded) return;
    clearTimeout(cfgTimer);
    cfgMsg = { text: 'saving…' };
    cfgTimer = setTimeout(async () => {
      const r = await post('/captioner', JSON.parse(snap));
      cfgMsg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: 'save failed' };
    }, 500);
  });

  async function saveCaption(image) {
    await post('/lora/caption/edit', { dataset, file: image.file, caption: image.caption });
  }

  async function caption() {
    if (!detail) return;
    capMsg = null;
    const url = method === 'wd14' ? '/lora/caption/wd14' : '/lora/caption';
    const payload = method === 'wd14'
      ? { dataset, repo: wdRepo, general_thresh: wdGen, character_thresh: wdChar }
      : { dataset };
    const r = await post(url, payload);
    if (r.status === 409) { capMsg = { err: true, text: 'a captioning run is already going' }; attach(); return; }
    if (r.data?.error) { capMsg = { err: true, text: r.data.error }; return; }
    progress = { done: 0, total: r.data?.total || detail.count };
    attach();
  }

  async function attach() {
    busy = true;
    const byFile = Object.fromEntries(detail.images.map((im, i) => [im.file, i]));
    try {
      const res = await fetch('/api/lora/caption/stream');
      if (res.status === 204 || !res.body) { busy = false; return; }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2);
          if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'caption') {
            const idx = byFile[ev.file];
            if (idx != null) detail.images[idx].caption = ev.caption;
            progress = { done: ev.index + 1, total: ev.total };
          } else if (ev.type === 'error') {
            if (ev.index != null) progress = { done: ev.index + 1, total: ev.total };
            capMsg = { err: true, text: ev.file ? `${ev.file}: ${ev.error}` : ev.error };
          } else if (ev.type === 'log') {
            capMsg = { text: ev.line };
          } else if (ev.type === 'fatal') {
            capMsg = { err: true, text: ev.error };
          } else if (ev.type === 'start' && ev.total) {
            progress = { done: progress.done, total: ev.total };
          }
        }
      }
    } catch (e) { capMsg = { err: true, text: String(e) }; }
    busy = false;
  }

  async function cancelCaption() { await post('/lora/caption/cancel'); }
</script>

<div class="hint">View a dataset and (re)caption it. Output goes to the per-image <code>.txt</code> files used for training.</div>

<div class="methods">
  <button class:on={method === 'vlm'} onclick={() => (method = 'vlm')}>Vision (OpenRouter)</button>
  <button class:on={method === 'wd14'} onclick={() => (method = 'wd14')}>WD14 (local tags)</button>
</div>

{#if method === 'vlm'}
  <div class="cfg">
    <div class="chead">
      <strong>Caption model</strong>
      <span class="hint">vision-capable OpenRouter model (Qwen-VL, Gemini, GPT-4o…) · saves automatically</span>
      {#if cfgMsg}<span class:ok={cfgMsg.ok} class:err={cfgMsg.err} class="save">{cfgMsg.text}</span>{/if}
    </div>
    <Combobox items={modelItems} value={cfg.model} placeholder="vision model…" onpick={(v) => (cfg.model = v)} />
    <label>Caption instructions (system)</label>
    <textarea rows="5" bind:value={cfg.system}></textarea>
  </div>
{:else}
  <div class="cfg">
    <div class="chead">
      <strong>WD14 tagger</strong>
      <span class="hint">local booru tagger · free · most accurate for anime · runs in the trainer venv</span>
    </div>
    {#if wd14 && !wd14.available}
      <div class="hint" style="color:var(--bad)">⚠ {wd14.reason}</div>
      {#if wd14.need_install}
        <button onclick={installWd14} disabled={installing} style="margin-top:10px">{installing ? 'Installing…' : 'Install WD14 (onnxruntime)'}</button>
      {/if}
    {:else}
      <div class="wdrow">
        <div><label>Model</label>
          <select bind:value={wdRepo}>
            <option value="SmilingWolf/wd-vit-tagger-v3">wd-vit v3 (fast)</option>
            <option value="SmilingWolf/wd-eva02-large-tagger-v3">wd-eva02-large v3 (most accurate)</option>
            <option value="SmilingWolf/wd-swinv2-tagger-v3">wd-swinv2 v3</option>
          </select>
        </div>
        <div><label>General threshold</label><input type="number" step="0.05" min="0" max="1" bind:value={wdGen} /></div>
        <div><label>Character threshold</label><input type="number" step="0.05" min="0" max="1" bind:value={wdChar} /></div>
      </div>
    {/if}
  </div>
{/if}

<div class="bar2">
  <div style="flex:1; min-width:220px">
    <label>Dataset</label>
    <Combobox items={dsItems} value={dataset} placeholder="pick a dataset…" onpick={selectDataset} />
  </div>
  <button onclick={caption} disabled={busy || !detail || (method === 'wd14' && !(wd14 && wd14.available))}>{busy ? 'Captioning…' : 'Generate captions'}</button>
  {#if busy}<button class="ghost sm" onclick={cancelCaption}>Cancel</button>{/if}
</div>

{#if busy || progress.done}
  <div class="prog">
    <div class="bar"><span style="width:{progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%"></span></div>
    <span class="pmeta">{progress.done}/{progress.total} captioned{busy ? '…' : ''}</span>
  </div>
{/if}
{#if capMsg}<div class:err={capMsg.err} class="save">{capMsg.text}</div>{/if}

{#if detail}
  <div class="grid" style="grid-template-columns: repeat(auto-fill, minmax({cardSize}px, 1fr)); --cap-h: {capH}px">
    {#each detail.images as im (im.file)}
      <figure class="card">
        <img src={`/api/lora/datasets/${encodeURIComponent(dataset)}/img/${im.file}`} alt={im.file} loading="lazy" />
        <textarea bind:value={im.caption} onblur={() => saveCaption(im)} placeholder="(no caption)"></textarea>
      </figure>
    {/each}
  </div>

  <div class="sizer">
    <span class="sz-grp" title="card width"><span class="sz-ic">↔</span>
      <input type="range" min="120" max="380" step="10" bind:value={cardSize} aria-label="card width" /></span>
    <span class="sz-div"></span>
    <span class="sz-grp" title="caption height"><span class="sz-ic">↕</span>
      <input type="range" min="44" max="280" step="8" bind:value={capH} aria-label="caption height" /></span>
  </div>
{:else}
  <div class="hint" style="margin-top:18px">No dataset selected. Curate one under Generate → Save LoRA Set first.</div>
{/if}

<style>
  .hint { font-size: 12.5px; color: var(--muted); }
  code { background: var(--elev); padding: 1px 5px; border-radius: 5px; font-size: 12px; }
  .bar2 { display: flex; gap: 10px; align-items: flex-end; margin: 14px 0; flex-wrap: wrap; }
  .methods { display: flex; gap: 4px; margin: 12px 0 12px; }
  .methods button { background: none; color: var(--muted); box-shadow: none; border: 1px solid var(--border); border-radius: 8px; padding: 7px 13px; font-weight: 560; font-size: 13px; }
  .methods button:hover { color: var(--text); background: var(--elev); filter: none; }
  .methods button.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1px var(--accent); }
  .wdrow { display: grid; grid-template-columns: 1fr 160px 160px; gap: 12px; align-items: end; }
  .wdrow label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }
  .wdrow select, .wdrow input { width: 100%; }

  .cfg { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 14px; margin: 4px 0 14px; }
  .chead { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
  .chead strong { font-size: 14px; }
  .cfg textarea { width: 100%; resize: vertical; font: 12.5px/1.5 ui-monospace, monospace; }
  .save { font-size: 12px; color: var(--muted); margin-top: 6px; }
  .ok { color: var(--good); } .err { color: var(--bad); }

  .prog { display: flex; align-items: center; gap: 10px; margin: 6px 0 14px; }
  .bar { flex: 1; height: 8px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .pmeta { font-size: 12px; color: var(--muted); white-space: nowrap; }

  .grid { display: grid; gap: 12px; margin-top: 6px; padding-bottom: 72px; }
  .card { margin: 0; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; overflow: hidden; display: flex; flex-direction: column; }
  .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: var(--elev); }
  .card textarea { width: 100%; height: var(--cap-h, 56px); border: 0; border-top: 1px solid var(--border-soft); background: transparent; resize: vertical; font: 11px/1.45 ui-monospace, monospace; color: var(--text); padding: 7px 8px; }
  .card textarea:focus { outline: none; background: var(--elev); }

  /* floating card-size slider */
  .sizer {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 40;
    display: flex; align-items: center; gap: 11px; padding: 9px 16px; border-radius: 999px;
    background: rgba(20, 24, 34, .82); border: 1px solid var(--border);
    box-shadow: 0 10px 30px rgba(0, 0, 0, .45); backdrop-filter: blur(10px) saturate(1.1);
  }
  .sizer .sz-grp { display: flex; align-items: center; gap: 9px; }
  .sizer .sz-ic { color: var(--muted); font-size: 14px; line-height: 1; }
  .sizer .sz-div { width: 1px; height: 20px; background: var(--border); }
  .sizer input[type=range] { width: 150px; accent-color: var(--accent); }
</style>

<script>
  // Drag-and-drop a ComfyUI API-format workflow JSON → the app resolves which checkpoints/
  // LoRAs/VAE/CLIP it references against the local model tree, lets you fetch any that are
  // missing (catalog download OR drag-drop the file), auto-detects the positive-prompt +
  // output nodes, and registers it as a new image model (models.yaml) — no restart needed.
  let { onimported } = $props();

  let dragOver = $state(false);
  let wf = $state(null);          // parsed graph
  let fmt = $state(null);         // 'api' (node-id keyed, importable) | 'ui' (editor format)
  let fileName = $state('');
  let name = $state('');          // new model key
  let nodeCount = $derived(wf ? (fmt === 'ui' ? (wf.nodes?.length || 0) : Object.keys(wf).length) : 0);

  // CJK (Chinese/Korean/Japanese) text → English translation of prompt fields.
  let cjk = $state([]);           // [{path:[...], text, label}]
  let translating = $state(false);
  const CJK_RE = /[㐀-䶿一-鿿豈-﫿가-힣぀-ヿ]/;
  const getAt = (o, p) => p.reduce((a, k) => a?.[k], o);
  function setAt(o, p, v) { let a = o; for (let i = 0; i < p.length - 1; i++) a = a[p[i]]; a[p[p.length - 1]] = v; }
  function scanCjk(graph, format) {
    const out = [];
    if (format === 'api') {
      for (const [nid, n] of Object.entries(graph)) {
        const ins = n?.inputs; if (!ins || typeof ins !== 'object') continue;
        for (const [f, v] of Object.entries(ins))
          if (typeof v === 'string' && CJK_RE.test(v)) out.push({ path: [nid, 'inputs', f], text: v, label: `${n.class_type} · ${f}` });
      }
    } else if (format === 'ui') {
      (graph.nodes || []).forEach((n, ni) => {
        const wv = n?.widgets_values;
        if (Array.isArray(wv)) wv.forEach((v, i) => { if (typeof v === 'string' && CJK_RE.test(v)) out.push({ path: ['nodes', ni, 'widgets_values', i], text: v, label: `${n.type} · widget ${i}` }); });
      });
    }
    return out;
  }

  let checking = $state(false);
  let refs = $state([]);          // [{kind,name,installed,catalog,node,class_type}]
  let io = $state(null);          // {positive:{node,field}, output_node, text_candidates, output_candidates}
  let posNode = $state('');
  let posField = $state('text');
  let outNode = $state('');

  let dl = $state({});            // ref name -> pct|'done'|'err: …'  (catalog download)
  let up = $state({});            // ref name -> 'uploading'|'done'|'err: …' (manual upload)
  let importing = $state(false);
  let msg = $state(null);         // {ok, text, key?}

  let missing = $derived(refs.filter((r) => !r.installed));
  const short = (n) => (n || '').split(/[\\/]/).pop();

  // Map a workflow ref kind → how to upload it. lora/checkpoint/diffusion auto-classify+place
  // (smart-upload, family-aware); everything else goes to its kind folder via model_upload.
  const SMART = new Set(['lora', 'checkpoint', 'diffusion']);

  function reset() {
    wf = null; fmt = null; fileName = ''; name = ''; refs = []; io = null; cjk = []; translating = false;
    posNode = ''; posField = 'text'; outNode = ''; dl = {}; up = {}; msg = null;
  }

  async function ingest(file) {
    if (!file) return;
    let text;
    try { text = await file.text(); } catch { msg = { ok: false, text: 'could not read file' }; return; }
    let graph;
    try { graph = JSON.parse(text); } catch { msg = { ok: false, text: 'not valid JSON' }; return; }
    // API format = node-id → {class_type, inputs} (importable). UI/editor format = {nodes:[…]}
    // (only translatable + downloadable here; re-export as API to register it).
    const isApi = graph && typeof graph === 'object' && !Array.isArray(graph.nodes) &&
      Object.values(graph).some((n) => n && typeof n === 'object' && n.class_type);
    const isUi = graph && Array.isArray(graph.nodes);
    if (!isApi && !isUi) { msg = { ok: false, text: 'not a ComfyUI workflow JSON' }; return; }
    wf = graph;
    fmt = isApi ? 'api' : 'ui';
    fileName = file.name;
    name = file.name.replace(/\.json$/i, '').replace(/_api$/i, '');
    msg = null;
    cjk = scanCjk(wf, fmt);
    if (fmt === 'api') await runCheck(); else { refs = []; io = null; }
  }

  async function translateAll() {
    if (!cjk.length || translating) return;
    translating = true; msg = null;
    try {
      const res = await fetch('/api/translate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts: cjk.map((c) => c.text) }),
      });
      const data = await res.json();
      if (!data.ok) { msg = { ok: false, text: data.error || 'translation failed' }; translating = false; return; }
      const tr = data.translations || [];
      const next = structuredClone($state.snapshot(wf));
      cjk.forEach((c, i) => { if (tr[i] != null) setAt(next, c.path, tr[i]); });
      wf = next;
      cjk = scanCjk(wf, fmt);
      if (fmt === 'api') await runCheck();
      msg = { ok: true, text: 'Translated prompts to English' };
    } catch (e) { msg = { ok: false, text: 'translation failed: ' + e }; }
    translating = false;
  }

  let converting = $state(false);
  async function convertToApi() {
    if (!wf || converting) return;
    converting = true; msg = null;
    try {
      const res = await fetch('/api/workflow/convert', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ json: wf }),
      });
      const data = await res.json();
      if (!data.ok) { msg = { ok: false, text: data.error || 'convert failed' }; converting = false; return; }
      wf = data.json;
      fmt = 'api';
      cjk = scanCjk(wf, 'api');
      await runCheck();
      msg = (data.unknown_nodes || []).length
        ? { ok: false, text: `Converted, but these node types aren't installed: ${data.unknown_nodes.join(', ')} — install them or the workflow won't run.` }
        : { ok: true, text: 'Converted to API format — resolve any missing models below, then import.' };
    } catch (e) { msg = { ok: false, text: 'convert failed: ' + e }; }
    converting = false;
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(wf, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = (name || 'workflow') + (fmt === 'api' ? '_api.json' : '.json');
    a.click(); URL.revokeObjectURL(a.href);
  }

  async function runCheck() {
    if (!wf) return;
    checking = true;
    try {
      const res = await fetch('/api/workflow/check', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ json: wf }),
      });
      const data = await res.json();
      refs = data.refs || [];
      io = data.io || null;
      posNode = io?.positive?.node || '';
      posField = io?.positive?.field || 'text';
      outNode = io?.output_node || '';
    } catch (e) { msg = { ok: false, text: 'check failed: ' + e }; }
    checking = false;
  }

  function onFilePick(e) { const f = e.target.files?.[0]; if (f) ingest(f); e.target.value = ''; }
  function onDrop(e) {
    e.preventDefault(); dragOver = false;
    const f = [...(e.dataTransfer.files || [])].find((x) => x.name.endsWith('.json'));
    if (f) ingest(f);
  }

  // --- fetch a missing model: catalog download ---
  async function download(ref) {
    const c = ref.catalog; if (!c || typeof dl[ref.name] === 'number') return;
    dl[ref.name] = 0;
    try {
      const res = await fetch('/api/comfy/catalog/install', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: c.url, rel: c.rel }),
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') dl[ref.name] = ev.total ? Math.round((ev.done / ev.total) * 100) : -1;
          else if (ev.type === 'done') { dl[ref.name] = 'done'; }
          else if (ev.type === 'error') dl[ref.name] = 'err: ' + ev.error;
        }
      }
    } catch (ex) { dl[ref.name] = 'err: ' + ex; }
    await runCheck();
  }

  // --- fetch a missing model: drag-drop / pick the actual file ---
  async function uploadFor(ref, file) {
    if (!file) return;
    up[ref.name] = 'uploading';
    try {
      const fd = new FormData(); fd.append('file', file);
      let url;
      if (SMART.has(ref.kind)) { fd.append('expect', ref.kind === 'lora' ? 'lora' : 'checkpoint'); url = '/api/comfy/models/smart-upload'; }
      else { url = `/api/comfy/models/upload?kind=${encodeURIComponent(ref.kind)}`; }
      const res = await fetch(url, { method: 'POST', body: fd });
      const data = await res.json();
      up[ref.name] = data.ok ? 'done' : ('err: ' + (data.error || 'upload failed'));
    } catch (e) { up[ref.name] = 'err: ' + e; }
    await runCheck();
  }
  function pickFor(ref, e) { const f = e.target.files?.[0]; if (f) uploadFor(ref, f); e.target.value = ''; }

  let rowDrag = $state(null);   // ref.name being dragged over
  function rowDrop(ref, e) {
    e.preventDefault(); rowDrag = null;
    const f = [...(e.dataTransfer.files || [])].find((x) => /\.(safetensors|ckpt|pt|gguf|pth|bin)$/i.test(x.name));
    if (f) uploadFor(ref, f);
  }

  async function doImport() {
    if (!wf || importing) return;
    if (!posNode) { msg = { ok: false, text: 'pick the positive-prompt node' }; return; }
    if (!outNode) { msg = { ok: false, text: 'pick the output (SaveImage) node' }; return; }
    importing = true; msg = null;
    try {
      const res = await fetch('/api/workflow/import', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name, json: wf,
          positive: { node: posNode, field: posField || 'text' },
          output_node: outNode,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        msg = { ok: true, text: `Imported as image model “${data.key}”`, key: data.key };
        onimported?.(data.key);
        const done = data.key; reset(); msg = { ok: true, text: `Imported as image model “${done}” → selectable in Graph / roles`, key: done };
      } else {
        msg = { ok: false, text: data.error || 'import failed' };
      }
    } catch (e) { msg = { ok: false, text: 'import failed: ' + e }; }
    importing = false;
  }
</script>

<section class="card">
  <div class="chead">
    <h3>Import workflow <span class="sub">drop a ComfyUI <code>API-format</code> JSON — resolves its models &amp; registers it as an image model</span></h3>
    {#if wf}<button class="ghost sm" onclick={reset}>✕ Clear</button>{/if}
  </div>

  {#if !wf}
    <label class="drop" class:over={dragOver}
      ondragover={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); dragOver = true; } }}
      ondragleave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) dragOver = false; }}
      ondrop={onDrop}>
      <span class="dz-icon">⬇</span>
      <span class="dz-label">{dragOver ? 'Drop to load' : 'Drop a workflow .json here, or click to pick'}</span>
      <input type="file" accept=".json,application/json" onchange={onFilePick} hidden />
    </label>
  {:else}
    <div class="wf-head">
      <span class="wf-name" title={fileName}>{fileName}</span>
      <span class="wf-meta">{nodeCount} nodes{#if fmt === 'api'} · {refs.length} model refs{#if missing.length} · <span class="bad">{missing.length} missing</span>{:else} · <span class="ok">all present</span>{/if}{:else} · <span class="warn">UI-format</span>{/if}</span>
    </div>

    <!-- Foreign-prompt translation (Chinese / Korean → English) -->
    {#if cjk.length}
      <div class="xlate">
        <span class="xl-label">🌐 {cjk.length} prompt field{cjk.length > 1 ? 's' : ''} contain Chinese/Korean text</span>
        <button class="mini" onclick={translateAll} disabled={translating}>{translating ? 'Translating…' : 'Translate → English'}</button>
      </div>
    {/if}

    {#if fmt === 'ui'}
      <div class="ui-note">
        <b>⚠ Can't import — this is a UI-format export, not API format.</b>
        The importer registers <b>API-format</b> graphs (node-id keyed). In ComfyUI: open the workflow →
        gear ⚙ → enable <b>Dev mode</b> → <b>Save (API Format)</b>, then drop <i>that</i> file here.
        You can still translate its prompts (above) and download the result below.
      </div>
      <div class="foot">
        <button class="primary" onclick={convertToApi} disabled={converting}>{converting ? 'Converting…' : '⚙ Convert to API & analyze'}</button>
        <button class="ghost sm" onclick={downloadJson}>⤓ Download JSON</button>
      </div>
    {:else}
    <!-- I/O detection (override-able) -->
    <div class="io">
      <div class="io-row">
        <label>Positive prompt</label>
        <select bind:value={posNode}>
          <option value="" disabled>— pick a text node —</option>
          {#each (io?.text_candidates || []) as c}
            <option value={c.node}>node {c.node} · {c.class_type}</option>
          {/each}
        </select>
        <span class="io-note">where the prompt is injected</span>
      </div>
      <div class="io-row">
        <label>Output image</label>
        <select bind:value={outNode}>
          <option value="" disabled>— pick an output node —</option>
          {#each (io?.output_candidates || []) as c}
            <option value={c.node}>node {c.node} · {c.class_type}</option>
          {/each}
        </select>
        <span class="io-note">the SaveImage / preview node</span>
      </div>
    </div>

    <!-- Model references -->
    {#if checking}<div class="muted">Resolving models…</div>{/if}
    <div class="refs">
      {#each refs as r (r.kind + '/' + r.name)}
        <div class="ref" class:miss={!r.installed} class:rdrag={rowDrag === r.name}
          ondragover={(e) => { if (!r.installed && e.dataTransfer.types.includes('Files')) { e.preventDefault(); rowDrag = r.name; } }}
          ondragleave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) rowDrag = null; }}
          ondrop={(e) => !r.installed && rowDrop(r, e)}>
          <span class="rkind">{r.kind}</span>
          <span class="rname" title={r.name}>{short(r.name)}</span>
          {#if r.installed || up[r.name] === 'done' || dl[r.name] === 'done'}
            <span class="ok">✓ present</span>
          {:else if typeof dl[r.name] === 'number'}
            <span class="prog">{dl[r.name] < 0 ? 'downloading…' : dl[r.name] + '%'}</span>
          {:else if up[r.name] === 'uploading'}
            <span class="prog">uploading…</span>
          {:else if typeof dl[r.name] === 'string' || (typeof up[r.name] === 'string' && up[r.name] !== 'done')}
            <span class="bad" title={dl[r.name] || up[r.name]}>failed</span>
          {:else}
            <span class="actions">
              {#if r.catalog}<button class="mini" onclick={() => download(r)} title={r.catalog.name || r.catalog.rel}>Download</button>{/if}
              <label class="mini drop-mini">{rowDrag === r.name ? 'drop file' : 'Upload…'}
                <input type="file" accept=".safetensors,.ckpt,.pt,.gguf,.pth,.bin" onchange={(e) => pickFor(r, e)} hidden /></label>
            </span>
          {/if}
        </div>
      {/each}
      {#if !refs.length && !checking}<div class="muted">No model references found in this workflow.</div>{/if}
    </div>

    <!-- Register -->
    <div class="foot">
      <label class="namel">Name <input class="namei" bind:value={name} placeholder="model key" /></label>
      {#if missing.length}<span class="warn">⚠ {missing.length} model{missing.length > 1 ? 's' : ''} still missing — import anyway and fetch later, or resolve above</span>{/if}
      <button class="primary" onclick={doImport} disabled={importing || !name.trim() || !posNode || !outNode}>
        {importing ? 'Importing…' : 'Import as image model'}
      </button>
    </div>
    {/if}
  {/if}

  {#if msg}<div class="msg" class:ok={msg.ok} class:bad={!msg.ok}>{msg.text}</div>{/if}
</section>

<style>
  .card { margin-bottom: 16px; }
  .chead { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; flex-wrap: wrap; }
  .chead h3 { margin: 0; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .sub code { font-family: ui-monospace, monospace; color: var(--muted); font-size: 11.5px; }

  .drop { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 16px; cursor: pointer;
    border: 2px dashed var(--border-soft); border-radius: 12px; transition: border-color .15s, background .15s; }
  .drop:hover, .drop.over { border-color: var(--accent); background: rgba(109,140,255,.07); }
  .dz-icon { font-size: 26px; opacity: .4; }
  .dz-label { font-size: 13px; color: var(--muted); }

  .xlate { display: flex; align-items: center; gap: 12px; padding: 8px 12px; margin-bottom: 12px; border-radius: 10px;
    background: rgba(109,140,255,.08); border: 1px solid rgba(109,140,255,.3); }
  .xl-label { font-size: 12.5px; color: var(--text); flex: 1; }
  .ui-note { line-height: 1.55; margin-bottom: 12px; font-size: 12.5px; color: var(--text);
    padding: 10px 12px; border-radius: 10px; background: rgba(224,162,60,.1); border: 1px solid rgba(224,162,60,.4); }
  .ui-note b { color: #e0a23c; }

  .wf-head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
  .wf-name { font-weight: 700; font-size: 13.5px; }
  .wf-meta { font-size: 12px; color: var(--muted); }

  .io { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; background: var(--bg); border: 1px solid var(--border-soft); border-radius: 10px; margin-bottom: 12px; }
  .io-row { display: flex; align-items: center; gap: 10px; }
  .io-row label { width: 110px; flex: none; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .io-row select { flex: 1; min-width: 0; padding: 6px 8px; font-size: 12.5px; border-radius: 7px; background: var(--panel); }
  .io-note { font-size: 11.5px; color: var(--faint); flex: none; }

  .refs { display: flex; flex-direction: column; gap: 4px; margin-bottom: 12px; }
  .ref { display: grid; grid-template-columns: 84px 1fr auto; gap: 10px; align-items: center; padding: 6px 8px; border-radius: 8px; border: 1px solid transparent; font-size: 12.5px; }
  .ref.miss { background: rgba(255,170,60,.06); border-color: rgba(255,170,60,.25); }
  .ref.rdrag { border-color: var(--accent); background: rgba(109,140,255,.1); }
  .rkind { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; }
  .rname { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text); }
  .ok { font-size: 11.5px; justify-self: end; }
  .bad { color: var(--bad); }
  .prog { color: var(--accent); font-size: 11.5px; justify-self: end; font-family: ui-monospace, monospace; }
  .actions { display: flex; gap: 6px; justify-self: end; }
  .mini { font-size: 11px; padding: 3px 9px; border-radius: 6px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .mini:hover { color: var(--accent); border-color: var(--accent); }
  .drop-mini { display: inline-flex; align-items: center; }

  .foot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; border-top: 1px solid var(--border-soft); padding-top: 12px; }
  .namel { display: flex; align-items: center; gap: 8px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .namei { text-transform: none; letter-spacing: 0; font-weight: 400; padding: 7px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .warn { font-size: 11.5px; color: #e0a23c; }
  .primary { margin-left: auto; padding: 8px 16px; font-size: 13px; font-weight: 600; }

  .muted { font-size: 12px; color: var(--faint); padding: 4px 2px; }
  .msg { margin-top: 10px; font-size: 12.5px; }
  .msg.ok { color: var(--good); }
  .msg.bad { color: var(--bad); }
</style>

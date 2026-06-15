<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { openLightbox } from '$lib/lightbox.svelte.js';
  import Combobox from '$lib/components/Combobox.svelte';
  import ScrubInput from '$lib/components/ScrubInput.svelte';

  // The self-contained LoRA subsystem: a typed library (detail/theme) + named,
  // routable stacks. Characters reference a stack by name; nothing here leaks
  // into the character card or the train bench.
  let cfg = $state({ library: [], stacks: [] });
  let choices = $state({ checkpoints: [], loras: [] });
  let msg = $state(null);
  let saving = $state(false);

  // --- triage: render each LoRA (ungrouped) against a checkpoint of its own
  //     architecture (folder), classify its type, delete redundant ones ---
  let triagePrompt = $state('1girl, solo, standing, looking at viewer, simple background');
  let triageWeight = $state(0.8);
  let triageItems = $state([]);   // every lora: {name, arch, img, status, pct, type}
  let bases = $state([]);         // image workflows: [{key, base, checkpoint, arch}]
  let baseModel = $state('');     // the one chosen workflow
  let showAll = $state(false);    // override the architecture filter
  let triageRunning = $state(false);
  let scan = $state({ items: [] });  // signature-classified model index

  // Real architecture from the scan (tensor signatures), not folder names.
  const normRel = (n) => (n || '').replace(/\\/g, '/');
  let loraArchMap = $derived(Object.fromEntries((scan.items || []).filter((i) => i.kind === 'lora').map((i) => [i.rel, i.arch || 'unknown'])));
  let baseArchMap = $derived(Object.fromEntries((scan.items || []).filter((i) => i.kind === 'checkpoint' || i.kind === 'diffusion').map((i) => [i.rel, i.arch || 'unknown'])));
  const folderArch = (name) => { const p = name.split(/[\\/]/); return p.length > 1 ? p[0] : '(root)'; };
  const archOf = (name) => loraArchMap[normRel(name)] || folderArch(name);
  // which LoRA archs a workflow arch can use (sd = SD-family, ambiguous 15/XL)
  const FAM = { sdxl: ['sdxl', 'sd'], sd15: ['sd15', 'sd'], sd: ['sd', 'sdxl', 'sd15'], dit: ['dit'], flux: ['flux'] };
  const compatible = (la, wa) => { const a = (wa || '').toLowerCase(); if (!a || a === 'unknown') return true; return (FAM[a] || [a]).includes((la || '').toLowerCase()); };

  let selBase = $derived(bases.find((b) => b.key === baseModel) || null);
  let wfArch = $derived((selBase?.arch || '').toLowerCase());
  function archMatch(a) { return showAll || compatible(a, wfArch); }
  let baseItems = $derived(bases.map((b) => ({ value: b.key, label: `${b.key}  ·  ${b.arch}` })));
  let visibleTriage = $derived(triageItems.filter((t) => archMatch(t.arch)));
  let triageDone = $derived(visibleTriage.filter((t) => t.img).length);

  // test panel
  let testStack = $state('');
  let testText = $state('');
  let testTheme = $state('');
  let testModel = $state('illustrious');
  let resolved = $state(null);
  let sceneNL = $state('');
  let tagifying = $state(false);
  let rendering = $state(false);
  let renderImg = $state(null);
  let renderPct = $state(null);
  let renderErr = $state(null);

  let loraItems = $derived((choices.loras || []).map((c) => ({ value: c, label: c })));
  let ckItems = $derived([{ value: '', label: '— workflow default —' }, ...(choices.checkpoints || []).map((c) => ({ value: c, label: c }))]);
  let imgModelItems = $derived((app.models?.image || []).map((m) => ({ value: m.key, label: m.key })));
  let stackItems = $derived(cfg.stacks.filter((s) => s.name).map((s) => ({ value: s.name, label: s.name })));
  let themeItems = $derived([{ value: '', label: '— none —' }, ...cfg.library.filter((l) => l.type === 'theme' && l.name).map((l) => ({ value: l.name, label: l.name }))]);

  function hydrate(c) {
    return {
      library: (c.library || []).map((l) => ({ ...l, keys: (l.keys || []).join(', ') })),
      stacks: (c.stacks || []).map((s) => ({
        name: s.name, checkpoint: s.checkpoint || '',
        loras: (s.loras || []).map((m) => ({ name: m.name, weight: m.weight ?? 0.7, role: m.role || 'uncategorized', keys: (m.keys || []).join(', '), threshold: m.threshold ?? null }))
      }))
    };
  }

  onMount(async () => {
    try { cfg = hydrate(await get('/loras')); } catch { /* none yet */ }
    try { choices = await get('/comfy/choices'); } catch { /* comfy off */ }
    try { bases = await get('/loras/bases'); } catch { /* none */ }
    try { scan = await get('/comfy/models'); } catch { /* scan unavailable */ }
    baseModel = bases.find((b) => b.key === app.activeImage)?.key || bases[0]?.key || '';
    buildTriage();
  });

  function buildTriage() {
    triageItems = (choices.loras || []).map((n) => {
      const lib = cfg.library.find((l) => l.name === n);
      return { name: n, arch: archOf(n), img: null, status: 'idle', pct: null, type: lib?.type || null };
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
      ? { checkpoint: selBase.checkpoint, lora: item.name, weight: +triageWeight || 0.8, prompt: triagePrompt }
      : { model: baseModel, loras: [{ name: item.name, weight: +triageWeight || 0.8 }], prompt: triagePrompt };
    try {
      const res = await fetch(url, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
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
    if (!selBase) { msg = { err: true, text: 'pick a workflow first' }; return; }
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
    const i = cfg.library.findIndex((l) => l.name === item.name);
    if (!item.type || item.type === 'skip') { if (i >= 0) cfg.library.splice(i, 1); return; }
    const w = item.type === 'detail' ? 0.5 : item.type === 'theme' ? 0.8 : 0.7;
    if (i >= 0) cfg.library[i].type = item.type;
    else cfg.library.push({ name: item.name, type: item.type, weight: w, enabled: true, comment: archOf(item.name) });
  }

  async function deleteLora(item) {
    if (!await askConfirm({ title: 'Delete LoRA file?', message: `${item.name} — removes the .safetensors from disk. Cannot be undone.`, confirmLabel: 'Delete', danger: true })) return;
    const r = await fetch(`/api/loras/file?name=${encodeURIComponent(item.name)}`, { method: 'DELETE' });
    const d = await r.json().catch(() => ({}));
    if (d.ok) {
      triageItems = triageItems.filter((t) => t.name !== item.name);
      const i = cfg.library.findIndex((l) => l.name === item.name);
      if (i >= 0) cfg.library.splice(i, 1);
    } else msg = { err: true, text: d.error || 'delete failed' };
  }

  function removeLib(i) { cfg.library.splice(i, 1); }

  // --- stack builder (pick a stack via combobox, drag/click LoRAs in) ---
  let stackSearch = $state('');
  let dragName = $state(null);
  let editKey = $state('new');          // 'new' = draft, else String(index) into cfg.stacks
  let draft = $state({ name: '', checkpoint: '', loras: [] });
  let editing = $derived(editKey === 'new' ? -1 : Number(editKey));
  let current = $derived(editing === -1 ? draft : (cfg.stacks[editing] || draft));
  let stackOpts = $derived([{ value: 'new', label: '＋ New stack' },
    ...cfg.stacks.map((s, i) => ({ value: String(i), label: s.name || `stack ${i + 1}` }))]);

  let allLoras = $derived((choices.loras || []).map((n) => ({ name: n, arch: archOf(n) })));
  let stackArch = $derived((baseArchMap[normRel(current?.checkpoint)] || '').toLowerCase());
  function archCompat(a) { return compatible(a, stackArch); }
  let leftFiltered = $derived(allLoras.filter((l) =>
    (!stackSearch.trim() || l.name.toLowerCase().includes(stackSearch.toLowerCase())) && archCompat(l.arch)));

  const baseName = (n) => n.split(/[\\/]/).pop();
  const inCurrent = (name) => current?.loras.some((m) => m.name === name);

  function pickStack(v) { editKey = v; if (v === 'new') draft = { name: '', checkpoint: '', loras: [] }; }
  function materialize() {
    if (editing !== -1) return cfg.stacks[editing];
    const s = { name: draft.name || `stack-${cfg.stacks.length + 1}`, checkpoint: draft.checkpoint || '', loras: [] };
    cfg.stacks.push(s);
    editKey = String(cfg.stacks.length - 1);
    draft = { name: '', checkpoint: '', loras: [] };
    return s;
  }
  function addToActive(name) {
    const s = (editing === -1) ? materialize() : cfg.stacks[editing];
    if (!s || s.loras.some((m) => m.name === name)) return;
    s.loras.push({ name, weight: 0.7, role: 'uncategorized', keys: '', threshold: null });
  }
  function removeCurrentStack() {
    if (editing === -1) return;
    cfg.stacks.splice(editing, 1);
    editKey = 'new'; draft = { name: '', checkpoint: '', loras: [] };
  }
  function setRole(m, role) { m.role = (m.role === role) ? 'uncategorized' : role; }
  function removeMember(s, mi) { s.loras.splice(mi, 1); }

  const csv = (s) => (s || '').split(',').map((k) => k.trim()).filter(Boolean);
  const stk = (items) => (items || []).filter((x) => x.name).map((x) => ({ name: x.name, weight: +x.weight || 1 }));

  async function save() {
    saving = true; msg = { text: 'Saving…' };
    const payload = {
      library: cfg.library.filter((l) => l.name).map((l) => ({ name: l.name, type: l.type, weight: +l.weight || 1, enabled: !!l.enabled, comment: l.comment || '' })),
      stacks: cfg.stacks.filter((s) => s.name).map((s) => ({
        name: s.name, checkpoint: s.checkpoint || null,
        loras: (s.loras || []).filter((m) => m.name).map((m) => ({ name: m.name, weight: +m.weight || 0.7, role: m.role || 'uncategorized', keys: csv(m.keys), threshold: (m.threshold === null || m.threshold === '') ? null : +m.threshold }))
      }))
    };
    const res = await post('/loras', payload);
    msg = res.data?.ok ? { ok: true, text: '✓ Saved' } : { err: true, text: res.data?.error || 'save failed' };
    saving = false;
  }

  async function tagify() {
    if (!sceneNL.trim() || tagifying) return;
    tagifying = true; msg = { text: 'Generating tags…' };
    const r = await post('/loras/tagify', { scene: sceneNL });
    if (r.data?.text != null) { testText = r.data.text; msg = { ok: true, text: '✓ tags generated' }; }
    else msg = { err: true, text: r.data?.error || 'tagify failed' };
    tagifying = false;
  }

  async function resolve() {
    renderImg = null; renderErr = null;
    const r = await post('/loras/resolve', { stack: testStack, text: testText, theme: testTheme || null });
    resolved = r.data?.loras ? r.data : null;
    if (!resolved) msg = { err: true, text: r.data?.error || 'resolve failed' };
  }

  async function render() {
    if (!resolved || rendering) return;
    rendering = true; renderErr = null; renderImg = null; renderPct = null;
    try {
      const res = await fetch('/api/lora/sample', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: testModel, checkpoint: resolved.checkpoint, loras: resolved.loras, prompt: testText })
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') renderPct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
          else if (ev.type === 'image') renderImg = (ev.images || [])[0] || null;
          else if (ev.type === 'error') renderErr = ev.error;
        }
      }
    } catch (e) { renderErr = String(e); }
    rendering = false;
  }
</script>

<div class="hint">A self-contained LoRA system: classify shared LoRAs by type, build named <em>stacks</em> that route state LoRAs by keyword, and test them. A character points at a stack by name (Characters → Portrait preset).</div>

<!-- CLASSIFY (triage) -->
<section class="card">
  <h3>Classify <span class="sub">— pick a workflow; only its architecture's LoRAs show. Render, then tag each type.</span></h3>

  <div class="trow" style="grid-template-columns: 1.4fr 3fr 90px;">
    <div><label>Workflow</label><Combobox items={baseItems} value={baseModel} placeholder="workflow…" onpick={(v) => (baseModel = v)} /></div>
    <div><label>Test prompt</label><input bind:value={triagePrompt} placeholder="1girl, solo, standing…" /></div>
    <div><label>Weight</label><ScrubInput step={0.01} min={0} max={2} bind:value={triageWeight} title="drag ↕ or click to type" /></div>
  </div>
  <div class="trow2">
    <button onclick={startTriage}>{triageRunning ? '■ Stop' : (triageDone ? 'Resume' : 'Render all')}</button>
    <span class="m">{triageDone}/{visibleTriage.length} rendered{triageRunning ? ' — generating…' : ''}</span>
    {#if selBase}<span class="warnsm">{selBase.arch} · {visibleTriage.length} of {triageItems.length} LoRAs{triageItems.length - visibleTriage.length ? ` (${triageItems.length - visibleTriage.length} hidden as other architectures)` : ''}</span>{/if}
    <label class="allchk"><input type="checkbox" bind:checked={showAll} /> show all</label>
  </div>

  <div class="grid">
    {#each visibleTriage as it (it.name)}
      <div class="cell" class:classified={it.type && it.type !== 'skip'}>
        <div class="thumb">
          {#if it.img}<button class="imgbtn" onclick={() => openLightbox(it.img, it.name)} title="click to enlarge"><img src={it.img} alt={it.name} /></button>
          {:else if it.status === 'gen'}<div class="ph">{it.pct !== null ? it.pct + '%' : '…'}</div>
          {:else if it.status === 'err'}<div class="ph err" title={it.err || ''}>failed</div>
          {:else if it.status === 'nockpt' || !selBase}<div class="ph err" title="pick a workflow above">no base</div>
          {:else}<button class="ph go" onclick={() => renderOne(it)} title="render this one">▶</button>{/if}
        </div>
        <div class="cap" title={it.name}><span class="ach">{it.arch}</span><span class="fn">{it.name.split(/[\\/]/).pop()}</span></div>
        <div class="types">
          {#each ['detail', 'theme', 'character', 'skip'] as t}
            <button class="tbtn {t}" class:on={it.type === t} onclick={() => classify(it, t)}>{t === 'character' ? 'char' : t}</button>
          {/each}
          <button class="tbtn del" onclick={() => deleteLora(it)} title="delete file" aria-label="Delete">🗑</button>
        </div>
      </div>
    {/each}
  </div>
  <p class="hint" style="margin-top:10px">Classifications fill the Library below — <strong>Save</strong> to persist.</p>
</section>

<!-- LIBRARY -->
<section class="card">
  <h3>Library <span class="sub">— shared, constant LoRAs (detail = always-on, theme = picked)</span></h3>
  <div class="libhead"><span>LoRA</span><span>Type</span><span>Weight</span><span>On</span><span>Note</span><span></span></div>
  {#each cfg.library as l, i (i)}
    <div class="librow">
      <div class="lsel"><Combobox items={loraItems} value={l.name} placeholder="lora…" onpick={(v) => (l.name = v)} /></div>
      <select bind:value={l.type}><option value="detail">detail</option><option value="theme">theme</option><option value="character">character</option></select>
      <ScrubInput class="w" step={0.01} min={-2} max={2} bind:value={l.weight} title="drag ↕ or click to type" />
      <input class="ck" type="checkbox" bind:checked={l.enabled} title={l.type === 'detail' ? 'always-on' : 'available to pick'} />
      <input class="keys" bind:value={l.comment} placeholder="note (optional)" />
      <button class="ghost sm" onclick={() => removeLib(i)} aria-label="Remove">✕</button>
    </div>
  {/each}
  {#if !cfg.library.length}<p class="hint" style="margin-top:8px">No classified LoRAs yet — use Classify above. (LoRAs aren't added by hand; they're discovered from disk and tagged here.)</p>{/if}
</section>

<!-- STACKS -->
<section class="card">
  <h3>Stacks <span class="sub">— pick a stack, set its checkpoint, then drag/click compatible LoRAs in</span></h3>
  <div class="builder">
    <div class="bleft">
      <input class="search" bind:value={stackSearch} placeholder="search loras…" />
      {#if stackArch}<div class="filt">showing <strong>{stackArch}</strong> LoRAs (match the checkpoint)</div>{/if}
      <div class="loralist">
        {#each leftFiltered as l (l.name)}
          <button class="loraitem" class:used={inCurrent(l.name)} draggable="true"
            ondragstart={() => (dragName = l.name)} onclick={() => addToActive(l.name)} title={l.name}>
            <span class="ach">{l.arch}</span><span class="ln">{baseName(l.name)}</span><span class="plus">＋</span>
          </button>
        {/each}
        {#if !leftFiltered.length}<div class="filt">no LoRAs match{stackArch ? ` ${stackArch}` : ''}.</div>{/if}
      </div>
    </div>

    <div class="bright" role="group"
      ondragover={(e) => e.preventDefault()}
      ondrop={() => { if (dragName) addToActive(dragName); dragName = null; }}>
      <div class="stacksel">
        <div class="sssel"><Combobox items={stackOpts} value={editKey} onpick={pickStack} /></div>
        {#if editing !== -1}<button class="ghost sm" onclick={removeCurrentStack}>✕ delete stack</button>{/if}
      </div>

      <div class="stackcard active">
        <div class="schead">
          <input class="sname" value={current.name} oninput={(e) => (current.name = e.target.value)} placeholder="stack name" />
          <div class="sck"><Combobox items={ckItems} value={current.checkpoint} placeholder="checkpoint (sets compatible LoRAs)…" onpick={(v) => (current.checkpoint = v)} /></div>
        </div>
        {#if !current.loras.length}
          <div class="drophint">drag or click LoRAs from the left to add them</div>
        {:else}
          {#each current.loras as m, mi (m.name)}
            <div class="member role-{m.role}">
              <span class="mn" title={m.name}>{baseName(m.name)}</span>
              <ScrubInput class="w" step={0.01} min={-2} max={2} bind:value={m.weight} title="weight — drag ↕ or click to type" />
              <div class="roles">
                <button class="rb identity" class:on={m.role === 'identity'} onclick={() => setRole(m, 'identity')}>identity</button>
                <button class="rb state" class:on={m.role === 'state'} onclick={() => setRole(m, 'state')}>state</button>
              </div>
              <button class="ghost sm" onclick={() => removeMember(current, mi)} aria-label="Remove">✕</button>
            </div>
            {#if m.role === 'state'}
              <input class="mkeys" bind:value={m.keys} placeholder="pin keywords (optional, comma-separated)" />
            {/if}
          {/each}
        {/if}
      </div>
    </div>
  </div>
</section>

<div class="savebar">
  <button onclick={save} disabled={saving}>{saving ? 'Saving…' : 'Save library + stacks'}</button>
  {#if msg}<span class:ok={msg.ok} class:err={msg.err} class="m">{msg.text}</span>{/if}
</div>

<!-- TEST -->
<section class="card">
  <h3>Test a stack</h3>
  <div class="trow">
    <div class="tf"><label>Stack</label><Combobox items={stackItems} value={testStack} placeholder="stack…" onpick={(v) => (testStack = v)} /></div>
    <div class="tf"><label>Theme (optional)</label><Combobox items={themeItems} value={testTheme} placeholder="— none —" onpick={(v) => (testTheme = v)} /></div>
    <div class="tf"><label>Workflow</label><Combobox items={imgModelItems} value={testModel} placeholder="workflow…" onpick={(v) => (testModel = v)} /></div>
  </div>
  <label>Scene <span class="lo">— natural language; the Prompt Gen LLM turns it into tags</span></label>
  <div class="scenerow">
    <input bind:value={sceneNL} placeholder="a knight in gleaming armor under moonlight, looking tense" />
    <button class="ghost" onclick={tagify} disabled={tagifying || !sceneNL.trim()}>{tagifying ? 'Generating…' : 'Scene → tags'}</button>
  </div>
  <label>Prompt / tags <span class="lo">— what routing matches and the image renders</span></label>
  <input bind:value={testText} placeholder="1girl, armor, moonlight, … (or generate from a scene above)" />
  <div class="trow2">
    <button class="ghost" onclick={resolve} disabled={!testStack}>Resolve</button>
    <button onclick={render} disabled={!resolved || rendering}>{rendering ? 'Rendering…' : 'Render'}</button>
    {#if renderErr}<span class="err">{renderErr}</span>{/if}
  </div>

  {#if resolved}
    {#if resolved.routed?.length}
      <div class="routing">
        <div class="rlbl">State routing</div>
        {#each resolved.routed as r}
          <div class="rrow" class:on={r.fired}>
            <span class="rdot"></span>
            <span class="rname">{r.name}</span>
            <span class="rscore">{r.score} / {r.threshold}</span>
            <span class="rwhy">{r.matched?.length ? r.matched.join(', ') : (r.why || '—')}</span>
          </div>
        {/each}
      </div>
    {/if}
    <div class="resolved">
      {#each resolved.loras as l}<span class="chip {l.type}" title={l.why}>{l.type}: {l.name} @{l.weight}</span>{/each}
      {#if !resolved.loras.length}<span class="m">empty stack — nothing resolved</span>{/if}
    </div>
  {/if}
  {#if rendering || renderImg}
    <div class="rout">
      {#if rendering && renderPct !== null}<div class="bar"><span style={`width:${renderPct}%`}></span></div>
      {:else if rendering}<div class="bar indet"><span></span></div>{/if}
      {#if renderImg}<button class="imgbtn" onclick={() => openLightbox(renderImg, testText)} title="click to enlarge"><img class="rimg" src={renderImg} alt="stack render" /></button>{/if}
    </div>
  {/if}
</section>

<style>
  .hint { font-size: 12.5px; color: var(--muted); margin-bottom: 14px; }
  .hint em { color: var(--text); font-style: normal; font-weight: 600; }
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }

  .warnsm { font-size: 11.5px; color: var(--muted); }
  .allchk { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted); margin-left: auto; }
  .allchk input { width: auto; }
  .grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 14px; margin-top: 14px; }
  .cell { display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--border-soft); border-radius: 10px; padding: 8px; background: var(--elev); }
  .cell.classified { border-color: var(--accent); }
  .thumb { aspect-ratio: 1; width: 100%; border-radius: 7px; overflow: hidden; background: var(--panel); display: grid; place-items: center; }
  .imgbtn { padding: 0; border: none; background: none; box-shadow: none; cursor: zoom-in; display: block; width: 100%; height: 100%; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .imgbtn .rimg { width: auto; max-width: 512px; height: auto; }
  .ph { color: var(--faint); font-size: 13px; width: 100%; height: 100%; display: grid; place-items: center; background: none; border: none; }
  .ph.err { color: var(--bad); } .ph.go { cursor: pointer; font-size: 20px; color: var(--muted); } .ph.go:hover { color: var(--accent); background: var(--elev-2); }
  .cap { display: flex; align-items: center; gap: 5px; font-size: 11px; margin: 7px 0 6px; min-width: 0; }
  .cap .fn { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ach { flex: none; font-size: 9.5px; color: var(--faint); background: var(--panel); border: 1px solid var(--border-soft); border-radius: 4px; padding: 0 4px; }
  .types { display: grid; grid-template-columns: repeat(4, 1fr) 24px; gap: 3px; margin-top: auto; }
  .tbtn.del { color: var(--muted); } .tbtn.del:hover { color: var(--bad); border-color: var(--bad); }
  .tbtn { font-size: 10px; padding: 4px 0; border-radius: 6px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--muted); box-shadow: none; white-space: nowrap; min-width: 0; overflow: hidden; }
  .tbtn:hover { color: var(--text); }
  .tbtn.on { color: #fff; border-color: transparent; }
  .tbtn.detail.on { background: #3a6ea5; } .tbtn.theme.on { background: #7a5bbf; } .tbtn.character.on { background: var(--accent); } .tbtn.skip.on { background: #555; }

  .libhead, .librow { display: grid; grid-template-columns: 1fr 90px 70px 36px 1.2fr 32px; gap: 8px; align-items: center; }
  .libhead { font-size: 10.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-bottom: 6px; }
  .librow { margin-bottom: 6px; }
  .lsel { min-width: 0; }
  .librow :global(.w) { width: 100%; padding: 6px 8px; } .keys { width: 100%; } .ck { width: auto; justify-self: center; }

  .lo { color: var(--faint); text-transform: none; letter-spacing: 0; }
  .builder { display: grid; grid-template-columns: 280px 1fr; gap: 14px; }
  .bleft { display: flex; flex-direction: column; min-width: 0; }
  .bleft .search { margin-bottom: 8px; }
  .loralist { display: flex; flex-direction: column; gap: 3px; max-height: 62vh; overflow: auto; padding-right: 4px; }
  .loraitem {
    display: flex; align-items: center; gap: 7px; text-align: left; width: 100%; cursor: grab;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 8px; padding: 6px 9px;
    color: var(--text); font: inherit; box-shadow: none;
  }
  .loraitem:hover { border-color: var(--accent); }
  .loraitem:active { cursor: grabbing; }
  .loraitem.used { opacity: .45; }
  .loraitem .ln { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
  .loraitem .plus { color: var(--faint); flex: none; }

  .bleft .filt { font-size: 11px; color: var(--faint); margin-bottom: 6px; }
  .filt strong { color: var(--muted); }
  .bright { min-width: 0; }
  .stacksel { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .sssel { width: 240px; min-width: 0; }
  .stackcard { border: 1px solid var(--border-soft); border-radius: 11px; padding: 11px; margin-bottom: 10px; background: var(--elev); }
  .stackcard.active { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(124,109,255,.25); }
  .schead { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
  .sname { font-weight: 600; width: 180px; flex: none; }
  .sck { flex: 1; min-width: 0; }
  .drophint { font-size: 12px; color: var(--faint); border: 1px dashed var(--border); border-radius: 8px; padding: 14px; text-align: center; }
  .member { display: flex; align-items: center; gap: 8px; padding: 5px 7px; border-radius: 7px; border: 1px solid transparent; }
  .member.role-uncategorized { background: var(--panel); border-color: var(--border-soft); }
  .member.role-identity { background: rgba(60,180,120,.10); border-color: rgba(60,180,120,.4); }
  .member.role-state { background: rgba(124,109,255,.10); border-color: rgba(124,109,255,.4); }
  .mn { flex: 1; min-width: 0; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .member :global(.w) { width: 64px; flex: none; padding: 6px 8px; }
  .roles { display: flex; gap: 3px; flex: none; }
  .rb { font-size: 10.5px; padding: 3px 8px; border-radius: 6px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--muted); box-shadow: none; }
  .rb.identity.on { background: #2f8f5b; color: #fff; border-color: transparent; }
  .rb.state.on { background: var(--accent); color: #fff; border-color: transparent; }
  .mkeys { font-size: 12px; margin: 2px 0 6px 7px; width: calc(100% - 14px); }

  .routing { margin-top: 12px; border: 1px solid var(--border-soft); border-radius: 9px; padding: 8px 10px; background: var(--elev); }
  .rlbl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-bottom: 6px; }
  .rrow { display: grid; grid-template-columns: 12px 1.4fr 80px 2fr; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); padding: 2px 0; }
  .rdot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
  .rrow.on .rdot { background: var(--accent); box-shadow: 0 0 7px var(--accent-glow); }
  .rrow.on .rname { color: var(--text); }
  .rscore { font-family: ui-monospace, monospace; font-size: 11px; }
  .rwhy { color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  .savebar { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
  .m { font-size: 12.5px; } .ok { color: var(--good); } .err { color: var(--bad); }

  .trow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 10px; }
  .tf label, .card > label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }
  .scenerow { display: flex; gap: 10px; align-items: center; }
  .scenerow input { flex: 1; min-width: 0; }
  .trow2 { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
  .resolved { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .chip { font-size: 11.5px; border-radius: 999px; padding: 2px 9px; border: 1px solid var(--border-soft); background: var(--elev); color: var(--muted); }
  .chip.detail { color: #8fcaff; } .chip.theme { color: #c9a6ff; } .chip.identity { color: var(--good); } .chip.state { color: var(--accent); }
  .rout { margin-top: 14px; }
  .rimg { max-width: 512px; width: 100%; border-radius: 10px; border: 1px solid var(--border); display: block; }
  .bar { height: 9px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); margin-bottom: 10px; }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .bar.indet span { width: 30%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
</style>

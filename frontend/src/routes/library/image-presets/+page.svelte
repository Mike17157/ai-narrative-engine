<script>
  import { onMount } from 'svelte';
  import { get, post, del } from '$lib/api.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import { autosize } from '$lib/autosize.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { loraLib, famOf, normRel } from '$lib/lora-library.svelte.js';

  // Image Presets manager — the LOOK side of image generation: a named LoRA stack (+ optional
  // base checkpoint) injected at render time, replacing the anima workflow's baked styles.
  // Sibling of the Presets / Lorebooks managers; everything auto-saves (debounced).

  let presets = $state([]);
  let selId = $state(null);
  let sel = $derived(presets.find((p) => p.id === selId) || null);
  let snap = null, timer = null;

  // Pools for the LoRA + checkpoint pickers (shared store; populated here on mount).
  let families = $derived(loraLib.families || []);
  let allLoras = $derived(loraLib.choices?.loras || []);
  let allCkpts = $derived(loraLib.choices?.checkpoints || []);
  // LoRAs scoped to the selected preset's family (empty family → all).
  let famLoras = $derived(
    sel?.family ? allLoras.filter((n) => famOf(n) === sel.family) : allLoras
  );
  let loraItems = $derived(famLoras.map((n) => ({ value: n, label: shortName(n) })));
  let ckptItems = $derived([{ value: '', label: '— workflow default —' },
    ...allCkpts.map((c) => ({ value: c, label: shortName(c) }))]);
  const shortName = (n) => (n || '').split(/[\\/]/).pop().replace(/\.(safetensors|pt|ckpt)$/i, '');

  // A preset's LoRA is "missing" if it isn't in the available pool (renamed/deleted file).
  let availSet = $derived(new Set(allLoras.map(normRel)));
  const isMissing = (name) => allLoras.length > 0 && !availSet.has(normRel(name));

  // Group by category, ordered by `order`; ungrouped trail last.
  let grouped = $derived.by(() => {
    const m = new Map();
    for (const p of [...presets].sort((a, b) => (a.order || 0) - (b.order || 0))) {
      const g = p.category || 'Other';
      if (!m.has(g)) m.set(g, []);
      m.get(g).push(p);
    }
    return [...m.entries()]
      .map(([cat, items]) => ({ cat, items, ord: Math.min(...items.map((p) => p.order || 0)) }))
      .sort((a, b) => a.ord - b.ord);
  });

  onMount(load);
  async function load() {
    presets = (await get('/image-presets')).presets || [];
    // Populate the shared lora store so the family/lora/checkpoint pickers work here.
    try { loraLib.choices = await get('/comfy/choices'); } catch {}
    try { loraLib.scan = await get('/comfy/models'); } catch {}
    try { loraLib.families = (await get('/families')).families || []; } catch {}
    if (!selId || !presets.some((p) => p.id === selId)) selId = presets[0]?.id || null;
    snap = sel ? JSON.stringify(sel) : null;
  }

  // Debounced autosave of the selected preset.
  $effect(() => {
    if (!sel) return;
    const cur = JSON.stringify(sel);
    if (snap === null) { snap = cur; return; }
    if (cur === snap) return;
    clearTimeout(timer);
    timer = setTimeout(save, 600);
  });
  async function save() {
    if (!sel) return;
    const r = await post('/image-presets', $state.snapshot(sel));
    if (r.data?.presets) { presets = r.data.presets; snap = JSON.stringify(sel); }
  }
  async function newPreset() {
    const r = await post('/image-presets', { name: 'New preset', category: 'Custom', family: '' });
    if (r.data?.presets) { presets = r.data.presets; selId = r.data.id; snap = JSON.stringify(presets.find((p) => p.id === selId)); }
  }
  async function deletePreset() {
    if (!sel || sel.id === 'none') return;
    if (!await askConfirm({ title: `Delete “${sel.name}”?`, confirmLabel: 'Delete', danger: true,
      message: sel.builtin ? 'This is a built-in preset — it will reappear on reload.' : '' })) return;
    const r = await del(`/image-presets/${sel.id}`);
    if (r?.presets) { presets = r.presets; selId = presets[0]?.id || null; snap = sel ? JSON.stringify(sel) : null; }
  }
  function pick(p) { selId = p.id; snap = JSON.stringify(p); }

  // --- LoRA stack row ops ---
  function addLora() { if (sel) sel.loras = [...sel.loras, { name: '', weight: 0.8, trigger: '' }]; }
  // Auto-suggest a LoRA's trigger words (Civitai trainedWords, else the file's own metadata) —
  // only when the trigger field is still empty, so a typed value is never clobbered.
  async function fetchTrigger(lr) {
    if (!lr || (lr.trigger || '').trim() || !lr.name) return;
    try {
      const m = await get(`/loras/metadata?name=${encodeURIComponent(lr.name)}`);
      const tw = (m?.trained_words || []).join(', ').trim();
      if (tw && !(lr.trigger || '').trim()) lr.trigger = tw;
    } catch {}
  }
  function pickLora(lr, v) { lr.name = v; fetchTrigger(lr); }

  // --- Drag & drop: drag a LoRA from the palette onto the stack (adds at the drop position),
  // or drag an existing row to reorder it. dragItem distinguishes the two sources. ---
  let loraSearch = $state('');
  let dragItem = $state(null);   // {type:'palette', name} | {type:'row', index}
  let dropIndex = $state(-1);    // where a drop would land (for the insertion indicator)
  let paletteLoras = $derived(
    famLoras.filter((n) => !loraSearch.trim() || shortName(n).toLowerCase().includes(loraSearch.toLowerCase()))
  );
  const inStack = (n) => !!sel?.loras.some((m) => normRel(m.name) === normRel(n));

  function addLoraByName(name, at = -1) {
    if (!sel || !name || inStack(name)) return;
    const arr = [...sel.loras];
    const idx = (at < 0 || at > arr.length) ? arr.length : at;
    arr.splice(idx, 0, { name, weight: 0.8, trigger: '' });
    sel.loras = arr;
    fetchTrigger(sel.loras[idx]);   // mutate the reactive (proxied) element
  }
  function onStackDrop(at) {
    const it = dragItem; dragItem = null; dropIndex = -1;
    if (!it || !sel) return;
    if (it.type === 'palette') { addLoraByName(it.name, at); return; }
    // reorder an existing row from it.index → at
    const arr = [...sel.loras];
    const [m] = arr.splice(it.index, 1);
    let to = (at < 0 || at > arr.length) ? arr.length : at;
    if (it.index < to) to--;                 // account for the removed slot
    arr.splice(Math.max(0, to), 0, m);
    sel.loras = arr;
  }
  function rmLora(i) { if (sel) sel.loras = sel.loras.filter((_, j) => j !== i); }
  function moveLora(i, d) {   // keyboard reorder (↑/↓ on the grip) — the accessible path beside drag
    if (!sel) return;
    const j = i + d;
    if (j < 0 || j >= sel.loras.length) return;
    const a = [...sel.loras]; [a[i], a[j]] = [a[j], a[i]]; sel.loras = a;
  }
</script>

<div class="wrap">
  <div class="list">
    <div class="lhead">Image Presets <span class="lo">— LoRA stacks, grouped</span></div>
    {#each grouped as grp (grp.cat)}
      <div class="pgroup">{grp.cat}</div>
      {#each grp.items as p (p.id)}
        <button class="row" class:on={p.id === selId} onclick={() => pick(p)}>
          <span class="nm">{p.name || p.id}</span>
          <span class="tags">
            {#if p.builtin}<span class="btag" title="built-in (re-seeded from the workflow)">built-in</span>{/if}
            {#if p.id !== 'none'}<span class="ltag" title="LoRAs in this stack">{p.loras?.length || 0}</span>{/if}
          </span>
        </button>
      {/each}
    {/each}
    <button class="new" onclick={newPreset}>＋ New preset</button>
  </div>

  {#if sel}
    <div class="edit">
      <div class="erow">
        <label>Name</label>
        <input class="fld" bind:value={sel.name} disabled={sel.id === 'none'} />
      </div>
      <div class="erow">
        <label>Category</label>
        <input class="fld" list="ip-cats" bind:value={sel.category} placeholder="e.g. Custom" />
        <label class="ordl" title="Sort order within the category">order
          <input class="fld onum" type="number" step="1" bind:value={sel.order} />
        </label>
      </div>
      <datalist id="ip-cats">
        {#each [...new Set(presets.map((p) => p.category).filter(Boolean))] as c}<option value={c}></option>{/each}
      </datalist>
      <div class="erow">
        <label>Family</label>
        <select class="fld" bind:value={sel.family} title="Scopes the LoRA picker; drives which workflows this preset fits">
          <option value="">— any —</option>
          {#each families as f}<option value={f.id}>{f.label || f.id}</option>{/each}
        </select>
      </div>
      <div class="erow">
        <label title="Optional: override the workflow's base model for this look">Base model</label>
        <Combobox items={ckptItems} value={sel.base_checkpoint || ''} placeholder="— workflow default —"
          onpick={(v) => (sel.base_checkpoint = v)} />
      </div>
      <div class="erow col">
        <label>Description</label>
        <textarea class="fld ta" rows="1" use:autosize={sel.description} bind:value={sel.description}></textarea>
      </div>

      {#if sel.id === 'none'}
        <p class="lo none-note">“None” is the floor — selecting it renders the base model with no LoRA preset. It can't be edited or deleted.</p>
      {:else}
        <div class="stack">
          <div class="shd">LoRA stack <span class="lo">— drag LoRAs in from the left; reorder by dragging rows</span>
            <button class="addl" onclick={addLora}>＋ Add LoRA</button>
          </div>
          <div class="stackgrid">
            <!-- draggable palette of available LoRAs (scoped to the preset's family) -->
            <div class="palette">
              <input class="fld psearch" bind:value={loraSearch} placeholder="search LoRAs…" />
              <div class="plist">
                {#each paletteLoras as n (n)}
                  <button class="pitem" class:used={inStack(n)} draggable="true"
                    ondragstart={() => (dragItem = { type: 'palette', name: n })}
                    ondragend={() => { dragItem = null; dropIndex = -1; }}
                    onclick={() => addLoraByName(n)} title={n}>
                    <span class="pln">{shortName(n)}</span><span class="pplus">＋</span>
                  </button>
                {/each}
                {#if !paletteLoras.length}<div class="lo pad">no LoRAs{sel.family ? ` for “${sel.family}”` : ''}{loraSearch ? ' match' : ''}.</div>{/if}
              </div>
            </div>

            <!-- the stack: a drop zone; each row is a reorder target -->
            <div class="stackmain" role="list"
              ondragover={(e) => { e.preventDefault(); if (dragItem) dropIndex = sel.loras.length; }}
              ondrop={(e) => { e.preventDefault(); onStackDrop(dropIndex < 0 ? sel.loras.length : dropIndex); }}>
              {#if sel.loras.length}
                <div class="rows">
                  {#each sel.loras as lr, i (i)}
                    <div class="lrowwrap" class:missing={isMissing(lr.name)} class:dropbefore={dropIndex === i}
                      role="listitem"
                      ondragover={(e) => { if (!dragItem) return; e.preventDefault(); e.stopPropagation();
                        const r = e.currentTarget.getBoundingClientRect();
                        dropIndex = (e.clientY < r.top + r.height / 2) ? i : i + 1; }}
                      ondrop={(e) => { e.preventDefault(); e.stopPropagation(); onStackDrop(dropIndex); }}>
                      <div class="lrow">
                        <!-- only the grip starts a drag, so the row's inputs stay interactive;
                             it's also keyboard-operable (focus + ↑/↓) for an accessible reorder -->
                        <span class="grip" draggable="true" role="button" tabindex="0"
                          aria-label="Reorder LoRA — drag, or press up/down arrows"
                          title="drag to reorder (or focus + ↑/↓)"
                          ondragstart={() => (dragItem = { type: 'row', index: i })}
                          ondragend={() => { dragItem = null; dropIndex = -1; }}
                          onkeydown={(e) => { if (e.key === 'ArrowUp') { e.preventDefault(); moveLora(i, -1); }
                            else if (e.key === 'ArrowDown') { e.preventDefault(); moveLora(i, 1); } }}>⠿</span>
                        <div class="lname">
                          <Combobox items={loraItems} value={lr.name} placeholder="pick a LoRA…"
                            onpick={(v) => pickLora(lr, v)} />
                          {#if isMissing(lr.name)}<span class="miss" title="not in the current LoRA library">missing</span>{/if}
                        </div>
                        <input class="fld wt" type="number" step="0.05" min="-2" max="2" bind:value={lr.weight} />
                        <button class="rm" onclick={() => rmLora(i)} aria-label="remove">×</button>
                      </div>
                      <input class="fld trg" bind:value={lr.trigger}
                        placeholder="trigger words — appended to every prompt (auto-detected on pick)"
                        title="Text added to every prompt while this LoRA is active, so it fires. Auto-filled from the LoRA's metadata; edit freely." />
                    </div>
                  {/each}
                </div>
              {:else}
                <div class="drophint" class:over={dropIndex >= 0}>Drag LoRAs here, or click one on the left.</div>
              {/if}
            </div>
          </div>
        </div>
      {/if}

      <div class="acts">
        <span class="hint">Auto-saves</span>
        {#if sel.id !== 'none'}<button class="danger" onclick={deletePreset}>Delete</button>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .wrap { display: flex; gap: 16px; align-items: flex-start; max-width: 1000px; }
  .list { width: 230px; flex: none; display: flex; flex-direction: column; gap: 4px; }
  .lhead { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 4px 2px 6px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .row { display: flex; align-items: center; gap: 8px; text-align: left; padding: 9px 11px; border-radius: 9px;
    background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; }
  .row:hover { background: var(--elev); }
  .row.on { border-color: var(--accent); background: var(--elev-2); }
  .nm { flex: 1; font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .tags { display: flex; align-items: center; gap: 5px; flex: none; }
  .pgroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 10px 4px 3px; }
  .pgroup:first-child { padding-top: 2px; }
  .ordl { display: inline-flex; align-items: center; gap: 6px; flex: none; font-size: 11px; font-weight: 700;
    text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .onum { width: 60px; flex: none; padding: 6px 8px; font-size: 12.5px; }
  .btag { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--faint);
    background: var(--elev); border-radius: 999px; padding: 1px 6px; }
  .ltag { font-size: 10px; font-weight: 700; color: var(--accent); background: rgba(109,140,255,.14); border-radius: 999px; padding: 1px 7px; }
  .new { margin-top: 4px; padding: 9px 11px; border-radius: 9px; background: none; border: 1px dashed var(--border);
    color: var(--muted); font-size: 12.5px; cursor: pointer; }
  .new:hover { color: var(--accent); border-color: var(--accent); }

  .edit { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
  .erow { display: flex; align-items: center; gap: 10px; }
  .erow.col { flex-direction: column; align-items: stretch; gap: 5px; }
  .erow > label { width: 96px; flex: none; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .erow.col > label { width: auto; }
  .erow .fld, .erow :global(.combo) { flex: 1; min-width: 0; }
  .erow.col .fld { flex: none; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .fld:disabled { opacity: .55; }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }
  .none-note { font-size: 12.5px; line-height: 1.5; border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px 12px; background: var(--bg); }

  .stack { border-top: 1px solid var(--border-soft); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
  .shd { display: flex; align-items: center; gap: 10px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .addl { margin-left: auto; font-size: 11.5px; text-transform: none; letter-spacing: 0; font-weight: 600;
    padding: 4px 10px; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .addl:hover { color: var(--accent); border-color: var(--accent); }
  /* palette + stack drop zone */
  .stackgrid { display: grid; grid-template-columns: 200px 1fr; gap: 12px; align-items: start; }
  .palette { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  .psearch { padding: 6px 9px; font-size: 12px; }
  .plist { display: flex; flex-direction: column; gap: 3px; max-height: 320px; overflow: auto; padding-right: 3px; }
  .pitem { display: flex; align-items: center; gap: 6px; text-align: left; width: 100%; cursor: grab;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 7px; padding: 5px 8px; color: var(--text); font: inherit; }
  .pitem:hover { border-color: var(--accent); }
  .pitem:active { cursor: grabbing; }
  .pitem.used { opacity: .4; }
  .pln { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11.5px; }
  .pplus { color: var(--faint); flex: none; }
  .pad { padding: 8px 2px; }
  .stackmain { min-width: 0; }

  .rows { display: flex; flex-direction: column; gap: 10px; }
  .lrowwrap { display: flex; flex-direction: column; gap: 4px; border-top: 2px solid transparent; }
  .lrowwrap.dropbefore { border-top-color: var(--accent); }
  .lrow { display: flex; align-items: center; gap: 8px; }
  .lrowwrap.missing :global(.combo) { border-color: var(--bad); }
  .trg { margin-left: 22px; width: calc(100% - 22px); padding: 6px 9px; font-size: 12px; }
  .trg::placeholder { color: var(--faint); }
  .grip { flex: none; width: 14px; text-align: center; color: var(--faint); cursor: grab; font-size: 13px; line-height: 1; user-select: none; }
  .grip:active { cursor: grabbing; }
  .drophint { font-size: 12px; color: var(--faint); border: 1px dashed var(--border); border-radius: 8px; padding: 16px; text-align: center; }
  .drophint.over { border-color: var(--accent); color: var(--accent); }
  .lname { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; }
  .lname :global(.combo) { flex: 1; min-width: 0; }
  .miss { font-size: 9.5px; font-weight: 700; color: var(--bad); background: rgba(255,90,90,.12); border-radius: 999px; padding: 1px 7px; flex: none; }
  .wt { width: 64px; flex: none; padding: 6px 8px; font-size: 12.5px; text-align: center; }
  .rm { flex: none; width: 24px; height: 24px; padding: 0; font-size: 16px; line-height: 1; border-radius: 6px;
    background: none; border: 1px solid transparent; color: var(--faint); cursor: pointer; }
  .rm:hover { color: var(--bad); border-color: rgba(255,90,90,.4); background: rgba(255,90,90,.1); }

  .acts { display: flex; align-items: center; gap: 12px; margin-top: 4px; }
  .hint { font-size: 12px; }
  .danger:hover { color: var(--bad); border-color: var(--bad); }
</style>

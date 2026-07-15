<script>
  import { loraLib } from '$lib/lora-library.svelte.js';
  import { img } from '$lib/images.svelte.js';

  let { nodeId, widgetName } = $props();

  function parse(text) {
    const re = /<lora:([^:>\s]+):([0-9.-]+)>/g;
    const out = []; let m;
    while ((m = re.exec(text ?? '')) !== null)
      out.push({ name: m[1], weight: parseFloat(m[2]) });
    return out;
  }

  const serialize = (es) => es.map(e => `<lora:${e.name}:${e.weight.toFixed(2)}>`).join('\n');

  let entries = $state([]);
  let _writing = false;

  $effect(() => {
    const text = img.workflow?.[nodeId]?.inputs?.[widgetName] ?? '';
    if (!_writing) entries = parse(text);
  });

  function commit(newEntries) {
    _writing = true;
    entries = newEntries;
    const node = img.workflow?.[nodeId];
    if (node?.inputs) node.inputs[widgetName] = serialize(newEntries);
    Promise.resolve().then(() => { _writing = false; });
  }

  function setWeight(i, raw) {
    const w = parseFloat(raw);
    if (!Number.isFinite(w)) return;
    commit(entries.map((e, j) => j === i ? { ...e, weight: w } : e));
  }

  function remove(i) { commit(entries.filter((_, j) => j !== i)); }

  // ── Picker ──────────────────────────────────────────────────────────────────
  // swapIdx: which entry is being replaced (null = appending a new entry)
  let pickerOpen = $state(false);
  let swapIdx = $state(null);
  let pickerQ = $state('');
  let pickerEl = $state();
  let searchEl = $state();

  function openAdd() {
    swapIdx = null;
    pickerOpen = true;
    pickerQ = '';
    queueMicrotask(() => searchEl?.focus());
  }

  function openSwap(i) {
    swapIdx = i;
    pickerOpen = true;
    pickerQ = '';
    queueMicrotask(() => searchEl?.focus());
  }

  function closePicker() { pickerOpen = false; swapIdx = null; }

  // Available LoRAs: family-filtered, excluding stack members except the one being swapped out.
  const pickerRows = $derived.by(() => {
    const fam = img.wfFamily;
    const famMap = Object.fromEntries(
      (loraLib.scan?.items || [])
        .filter(i => i.kind === 'lora')
        .map(i => [i.rel || i.name, i.family])
    );
    // Exclude entries already in the stack, BUT allow the currently-swapped entry
    const excluded = new Set(entries.filter((_, j) => j !== swapIdx).map(e => e.name));
    const all = (loraLib.choices?.loras || []).filter(n =>
      !excluded.has(n) && (!fam || fam === 'unknown' || !famMap[n] || famMap[n] === fam)
    );
    const q = pickerQ.trim().toLowerCase();
    return (q ? all.filter(n => n.toLowerCase().includes(q)) : all).slice(0, 60);
  });

  function commitPick(name) {
    if (swapIdx !== null) {
      commit(entries.map((e, j) => j === swapIdx ? { ...e, name } : e));
    } else {
      commit([...entries, { name, weight: 0.80 }]);
    }
    closePicker();
  }

  $effect(() => {
    if (!pickerOpen) return;
    const close = (e) => { if (pickerEl && !pickerEl.contains(e.target)) closePicker(); };
    const id = setTimeout(() => document.addEventListener('mousedown', close), 0);
    return () => { clearTimeout(id); document.removeEventListener('mousedown', close); };
  });

  const shortName = (n) => n.replace(/\.(safetensors|pt|ckpt)$/i, '');

  const totalLoras = $derived((loraLib.choices?.loras || []).length);
</script>

<div class="lse">
  {#each entries as e, i (e.name + i)}
    <div class="row">
      <!-- Name is a button: click to swap this LoRA -->
      <button class="lname nodrag nopan" title="Click to swap · {e.name}"
        onclick={() => openSwap(i)}>
        {shortName(e.name)}
      </button>
      <input
        class="wt nodrag nopan"
        type="number" step="0.01" min="-2" max="2"
        value={e.weight}
        onchange={(ev) => setWeight(i, ev.currentTarget.value)}
      />
      <button class="rm nodrag nopan" onclick={() => remove(i)} title="Remove LoRA">×</button>
    </div>
  {/each}

  <div class="picker-anchor" bind:this={pickerEl}>
    <button class="addlora nodrag nopan" onclick={openAdd}>＋ Add LoRA</button>

    {#if pickerOpen}
      <div class="picker nodrag nopan" onclick={(e) => e.stopPropagation()}>
        <div class="picker-hd">
          {#if swapIdx !== null}
            <span class="phd-label">Replace</span>
            <span class="phd-cur">{shortName(entries[swapIdx]?.name ?? '')}</span>
          {:else}
            <span class="phd-label">Add LoRA</span>
          {/if}
          <button class="phd-close nodrag nopan" onclick={closePicker}>✕</button>
        </div>
        <input bind:this={searchEl} bind:value={pickerQ}
          class="nodrag nopan"
          placeholder="Search {totalLoras} LoRAs…" />
        <div class="plist">
          {#each pickerRows as name (name)}
            <button class="prow nodrag nopan" onclick={() => commitPick(name)}>
              {shortName(name)}
            </button>
          {:else}
            <div class="pempty">{totalLoras === 0 ? 'no LoRAs loaded' : 'no matches'}</div>
          {/each}
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .lse { display: flex; flex-direction: column; gap: 2px; padding: 4px 0; }

  .row {
    display: flex; align-items: center; gap: 5px;
    padding: 2px 8px 2px 10px; min-height: 26px;
  }

  /* Name chip — clickable button for swap */
  .lname {
    flex: 1; min-width: 0;
    font-size: 11.5px; font-family: ui-monospace, monospace;
    color: var(--text); text-align: left;
    background: none; border: 1px solid transparent;
    border-radius: 5px; padding: 1px 6px; margin-left: -6px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    cursor: pointer;
  }
  .lname:hover {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border-color: color-mix(in srgb, var(--accent) 35%, transparent);
    color: var(--text);
  }

  .wt {
    width: 52px; flex: none; padding: 2px 5px; font-size: 12px; text-align: center;
    border-radius: 5px; background: var(--bg); border: 1px solid var(--border);
    color: var(--text); font-family: ui-monospace, monospace;
    -moz-appearance: textfield;
  }
  .wt::-webkit-outer-spin-button, .wt::-webkit-inner-spin-button { display: none; }
  .wt:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }

  .rm {
    flex: none; width: 20px; height: 20px; padding: 0; font-size: 14px; line-height: 1;
    background: none; border: 1px solid transparent; border-radius: 4px;
    color: var(--faint); display: grid; place-items: center;
  }
  .rm:hover { color: var(--bad); border-color: rgba(255,122,122,.4); background: rgba(255,122,122,.1); }

  /* Picker */
  .picker-anchor { position: relative; padding: 4px 10px 6px; }
  .addlora {
    font-size: 11.5px; font-weight: 600; color: var(--accent); background: none;
    border: 1px dashed color-mix(in srgb, var(--accent) 40%, transparent);
    border-radius: 6px; padding: 4px 10px; width: 100%;
  }
  .addlora:hover { background: color-mix(in srgb, var(--accent) 10%, transparent); }

  .picker {
    position: absolute; bottom: calc(100% + 4px); left: 0; right: 0; z-index: 60;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    box-shadow: 0 8px 28px rgba(0,0,0,.55); overflow: hidden;
  }

  .picker-hd {
    display: flex; align-items: center; gap: 6px;
    padding: 7px 10px 6px; border-bottom: 1px solid var(--border-soft);
    background: var(--elev);
  }
  .phd-label { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; }
  .phd-cur {
    font-size: 11.5px; font-family: ui-monospace, monospace; color: var(--accent);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; flex: 1;
  }
  .phd-close {
    flex: none; background: none; border: none;
    color: var(--faint); font-size: 13px; padding: 0 3px; line-height: 1;
  }
  .phd-close:hover { color: var(--text); }

  .picker input {
    border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0;
    background: var(--elev); font-size: 12px;
  }
  .picker input:focus { border-color: var(--border-soft); }
  .plist { max-height: 200px; overflow-y: auto; padding: 4px; }
  .prow {
    width: 100%; text-align: left; padding: 5px 9px; font-size: 12px;
    background: none; border-radius: 6px; color: var(--text);
    font-family: ui-monospace, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .prow:hover { background: var(--elev-2); }
  .pempty { padding: 10px; color: var(--muted); font-size: 12px; text-align: center; }
</style>

<script>
  // Stack builder: pick a named stack, set its checkpoint, then drag/click
  // compatible LoRAs in. Members get an identity (always-on) or state
  // (keyword-routed) role. Stacks are referenced by characters by name.
  import Combobox from '$lib/components/Combobox.svelte';
  import ScrubInput from '$lib/components/ScrubInput.svelte';
  import {
    loraLib, famOf, famLabel, compat, baseFamMap, normRel, ckItems,
  } from '$lib/lora-library.svelte.js';

  let { onchange = null } = $props();

  let stackSearch = $state('');
  let dragName = $state(null);
  let editKey = $state('new');          // 'new' = draft, else String(index) into cfg.stacks
  let draft = $state({ name: '', checkpoint: '', loras: [] });
  let editing = $derived(editKey === 'new' ? -1 : Number(editKey));
  let current = $derived(editing === -1 ? draft : (loraLib.cfg.stacks[editing] || draft));
  let stackOpts = $derived([
    { value: 'new', label: '＋ New stack' },
    ...loraLib.cfg.stacks.map((s, i) => ({ value: String(i), label: s.name || `stack ${i + 1}` })),
  ]);

  let allLoras = $derived((loraLib.choices.loras || []).map((n) => ({ name: n, fam: famOf(n) })));
  let stackFam = $derived(baseFamMap()[normRel(current?.checkpoint)] || 'unknown');
  function famCompat(f) { const c = compat(f, stackFam); return loraLib.showAll ? c !== 'incompatible' : c === 'native'; }
  let leftFiltered = $derived(allLoras.filter((l) =>
    (!stackSearch.trim() || l.name.toLowerCase().includes(stackSearch.toLowerCase())) && famCompat(l.fam)));

  const baseName = (n) => n.split(/[\\/]/).pop();
  const inCurrent = (name) => current?.loras.some((m) => m.name === name);

  function pickStack(v) {
    editKey = v;
    if (v === 'new') draft = { name: '', checkpoint: '', loras: [] };
    const name = v === 'new' ? '' : (loraLib.cfg.stacks[Number(v)]?.name || '');
    onchange?.(name);
  }
  function materialize() {
    if (editing !== -1) return loraLib.cfg.stacks[editing];
    const s = { name: draft.name || `stack-${loraLib.cfg.stacks.length + 1}`, checkpoint: draft.checkpoint || '', loras: [] };
    loraLib.cfg.stacks.push(s);
    editKey = String(loraLib.cfg.stacks.length - 1);
    draft = { name: '', checkpoint: '', loras: [] };
    return s;
  }
  function addToActive(name) {
    const s = (editing === -1) ? materialize() : loraLib.cfg.stacks[editing];
    if (!s || s.loras.some((m) => m.name === name)) return;
    s.loras.push({ name, weight: 0.7, role: 'uncategorized', keys: '', threshold: null });
  }
  function removeCurrentStack() {
    if (editing === -1) return;
    loraLib.cfg.stacks.splice(editing, 1);
    editKey = 'new'; draft = { name: '', checkpoint: '', loras: [] };
  }
  function setRole(m, role) { m.role = (m.role === role) ? 'uncategorized' : role; }
  function removeMember(s, mi) { s.loras.splice(mi, 1); }
</script>

<section class="card">
  <h3>Stacks <span class="sub">— pick a stack, set its checkpoint, then drag/click compatible LoRAs in</span></h3>
  <div class="builder">
    <div class="bleft">
      <input class="search" bind:value={stackSearch} placeholder="search loras…" />
      {#if stackFam && stackFam !== 'unknown'}<div class="filt">showing <strong>{famLabel()[stackFam] || stackFam}</strong> LoRAs (match the checkpoint){loraLib.showAll ? ' + compatible' : ''}</div>{/if}
      <div class="loralist">
        {#each leftFiltered as l (l.name)}
          <button class="loraitem" class:used={inCurrent(l.name)} draggable="true"
            ondragstart={() => (dragName = l.name)} onclick={() => addToActive(l.name)} title={l.name}>
            <span class="ach">{famLabel()[l.fam] || l.fam}</span><span class="ln">{baseName(l.name)}</span><span class="plus">＋</span>
          </button>
        {/each}
        {#if !leftFiltered.length}<div class="filt">no LoRAs match{stackFam !== 'unknown' ? ` ${famLabel()[stackFam] || stackFam}` : ''}.</div>{/if}
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
          <div class="sck"><Combobox items={ckItems()} value={current.checkpoint} placeholder="checkpoint (sets compatible LoRAs)…" onpick={(v) => (current.checkpoint = v)} /></div>
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

<style>
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
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
  .ach { flex: none; font-size: 9.5px; color: var(--faint); background: var(--panel); border: 1px solid var(--border-soft); border-radius: 4px; padding: 0 4px; }
</style>

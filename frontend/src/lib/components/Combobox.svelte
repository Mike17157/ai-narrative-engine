<script>
  // Searchable combobox. Single-select by default; set multi for tag-style
  // multiselect. items: [{ value, label }]. `value` is bindable.
  let {
    items = [],
    value = $bindable(),
    placeholder = 'Select…',
    multi = false,
    disabled = false,
    onpick = undefined // optional callback(selectedValue) for action-on-select
  } = $props();

  let open = $state(false);
  let query = $state('');
  let root;

  const norm = (s) => (s || '').toString().toLowerCase();
  let filtered = $derived(
    items.filter((it) => norm(it.label).includes(norm(query))).slice(0, 300)
  );

  function labelFor(v) {
    const it = items.find((i) => i.value === v);
    return it ? it.label : v;
  }
  function isSel(v) {
    return multi ? Array.isArray(value) && value.includes(v) : value === v;
  }
  function pick(it) {
    if (multi) {
      const arr = Array.isArray(value) ? value : [];
      value = arr.includes(it.value) ? arr.filter((x) => x !== it.value) : [...arr, it.value];
      onpick?.(value);
    } else {
      value = it.value;
      open = false;
      query = '';
      onpick?.(it.value);
    }
  }

  $effect(() => {
    function onDoc(e) { if (root && !root.contains(e.target)) open = false; }
    document.addEventListener('click', onDoc);
    return () => document.removeEventListener('click', onDoc);
  });
</script>

<div class="cb" bind:this={root} class:disabled>
  <div class="control" onclick={() => !disabled && (open = !open)}>
    {#if multi}
      {#if Array.isArray(value) && value.length}
        {#each value as v}
          <span class="chip">{labelFor(v)}<button onclick={(e) => { e.stopPropagation(); pick({ value: v }); }}>×</button></span>
        {/each}
      {:else}
        <span class="ph">{placeholder}</span>
      {/if}
    {:else}
      <span class={value ? '' : 'ph'}>{value ? labelFor(value) : placeholder}</span>
    {/if}
    <span class="caret">▾</span>
  </div>

  {#if open}
    <div class="pop">
      <input class="search" placeholder="search…" bind:value={query} onclick={(e) => e.stopPropagation()} />
      <div class="list">
        {#each filtered as it (it.value)}
          <div class="opt" class:sel={isSel(it.value)} onclick={(e) => { e.stopPropagation(); pick(it); }}>
            {it.label}
          </div>
        {:else}
          <div class="empty">no matches</div>
        {/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .cb { position: relative; width: 100%; }
  .cb.disabled { opacity: .5; pointer-events: none; }
  .control {
    display: flex; flex-wrap: wrap; gap: 5px; align-items: center; min-height: 38px;
    background: var(--elev); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 6px 10px; cursor: pointer; transition: border-color .15s, box-shadow .15s;
  }
  .control:hover { border-color: #323847; }
  .ph { color: var(--faint); }
  .caret { margin-left: auto; color: var(--muted); font-size: 11px; }
  .chip {
    display: inline-flex; align-items: center; gap: 5px; background: rgba(109,140,255,.16);
    color: #cdd8ff; border: 1px solid rgba(109,140,255,.3); border-radius: 7px; padding: 2px 7px; font-size: 13px;
  }
  .chip button { background: none; border: 0; color: #9fb3d8; cursor: pointer; padding: 0; font-size: 14px; box-shadow: none; }
  .pop {
    position: absolute; z-index: 30; left: 0; right: 0; margin-top: 6px;
    background: var(--elev); border: 1px solid var(--border); border-radius: var(--radius);
    box-shadow: var(--shadow); overflow: hidden;
  }
  .search { border: 0; border-bottom: 1px solid var(--border-soft); border-radius: 0; background: var(--panel); }
  .search:focus { box-shadow: none; border-color: var(--border-soft); }
  .list { max-height: 280px; overflow: auto; padding: 4px; }
  .opt { padding: 8px 10px; cursor: pointer; font-size: 13.5px; border-radius: 7px; }
  .opt:hover { background: var(--elev-2); }
  .opt.sel { background: rgba(109,140,255,.16); color: #cdd8ff; }
  .empty { padding: 12px; color: var(--muted); font-size: 13px; }
</style>

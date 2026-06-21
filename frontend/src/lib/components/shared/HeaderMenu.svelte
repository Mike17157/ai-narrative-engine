<script>
  // A page-header title that doubles as a dropdown. `title` is the big label (e.g. a
  // story name or section group); `items` are [{id,label}]; the selected item's label
  // shows next to the title with a caret. Picking fires onpick(id). Closes on outside click.
  let { title = '', items = [], value = '', onpick } = $props();
  let open = $state(false);
  let selected = $derived(items.find((i) => i.id === value) || null);
  function choose(id) { open = false; onpick?.(id); }
</script>

<svelte:window onclick={() => (open = false)} />

<div class="hm">
  <button class="hmbtn" onclick={(e) => { e.stopPropagation(); open = !open; }} aria-haspopup="menu" aria-expanded={open}>
    {#if title}<span class="hmtitle">{title}</span>{/if}
    {#if selected}<span class="hmsel">{selected.label}</span>{/if}
    <span class="hmcaret" class:open>▾</span>
  </button>
  {#if open}
    <div class="hmpop" role="menu" onclick={(e) => e.stopPropagation()}>
      {#each items as it (it.id)}
        <button class="hmitem" class:on={it.id === value} role="menuitem" onclick={() => choose(it.id)}>{it.label}</button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .hm { position: relative; }
  .hmbtn {
    display: inline-flex; align-items: baseline; gap: 9px; padding: 5px 10px 5px 8px;
    background: none; border: 0; box-shadow: none; color: var(--text); cursor: pointer; border-radius: 8px;
  }
  .hmbtn:hover { background: var(--elev); filter: none; }
  .hmtitle { font-size: 15px; font-weight: 660; letter-spacing: .2px; }
  .hmsel { font-size: 13px; font-weight: 600; color: var(--accent); }
  .hmcaret { font-size: 12px; color: var(--muted); transition: transform .15s; transform: translateY(-1px); }
  .hmcaret.open { transform: rotate(180deg) translateY(1px); }

  .hmpop {
    position: absolute; top: calc(100% + 6px); left: 0; z-index: 40; min-width: 220px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 11px;
    box-shadow: var(--shadow); padding: 6px; display: flex; flex-direction: column; gap: 2px;
  }
  .hmitem {
    text-align: left; padding: 9px 12px; border-radius: 7px; box-shadow: none; border: 0;
    background: none; color: var(--text); font-size: 13.5px; font-weight: 560;
  }
  .hmitem:hover { background: var(--elev); filter: none; }
  .hmitem.on { background: var(--elev-2); color: #fff; box-shadow: inset 0 0 0 1px var(--accent); }
</style>

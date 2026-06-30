<script>
  // Generic searchable/selectable list of entities. The reusable body behind the browse
  // modal (and any "pick from a set" surface). Items: { id, name, badge?, badgeKind?, meta? }.
  let {
    items = [],
    selected = [],          // array of selected ids
    multi = true,
    onToggle = null,        // (id) => void
    placeholder = 'Search…',
    empty = 'Nothing here yet.',
  } = $props();

  let q = $state('');
  let filtered = $derived(
    !q.trim() ? items
      : items.filter((it) => `${it.name} ${it.id} ${it.meta || ''}`.toLowerCase().includes(q.trim().toLowerCase()))
  );
  const isSel = (id) => selected.includes(id);
</script>

<div class="el">
  <input class="search" placeholder={placeholder} bind:value={q} />
  <div class="rows">
    {#if !filtered.length}<div class="none">{empty}</div>{/if}
    {#each filtered as it (it.id)}
      <button class="row" class:on={isSel(it.id)} onclick={() => onToggle?.(it.id)}>
        {#if multi}<span class="box" class:checked={isSel(it.id)}>{isSel(it.id) ? '✓' : ''}</span>{/if}
        <span class="name">{it.name || it.id}</span>
        {#if it.badge}<span class="badge {it.badgeKind || ''}">{it.badge}</span>{/if}
        {#if it.meta}<span class="meta">{it.meta}</span>{/if}
        {#if !multi && isSel(it.id)}<span class="cur">current</span>{/if}
      </button>
    {/each}
  </div>
</div>

<style>
  .el { display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .search { width: 100%; padding: 9px 11px; font-size: 13px; border-radius: 9px; box-sizing: border-box;
    background: var(--bg); border: 1px solid var(--border); color: var(--text); margin-bottom: 8px; }
  .search:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .rows { display: flex; flex-direction: column; gap: 2px; overflow-y: auto; min-height: 0; flex: 1; }
  .row { display: flex; align-items: center; gap: 9px; text-align: left; padding: 8px 10px; border-radius: 8px;
    background: none; border: 1px solid transparent; color: var(--text); cursor: pointer; }
  .row:hover { background: var(--elev); }
  .row.on { background: var(--elev-2); border-color: var(--border-soft); }
  .box { width: 17px; height: 17px; flex: none; border-radius: 5px; border: 1px solid var(--border);
    display: grid; place-items: center; font-size: 11px; color: #fff; }
  .box.checked { background: var(--accent); border-color: var(--accent); }
  .name { flex: 1; font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; padding: 1px 6px; border-radius: 5px;
    color: var(--faint); background: var(--elev); }
  .badge.nsfw { color: #e9a0b8; background: rgba(230,120,160,.14); }
  .badge.sfw { color: #8fc7a0; background: rgba(120,200,140,.12); }
  .badge.accent { color: var(--accent); background: rgba(109,140,255,.14); }
  .meta { font-size: 11px; color: var(--faint); flex: none; }
  .cur { font-size: 9.5px; font-weight: 700; color: var(--accent); }
  .none { color: var(--faint); font-size: 12.5px; padding: 12px; text-align: center; }
</style>

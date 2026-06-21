<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';

  // Attach lorebooks to a thread. `value` is an array of book ids; `onchange` fires with
  // the new array. Shows real book names + sfw/nsfw badges (not bare scope strings).
  // `exclude` hides books the caller manages elsewhere (e.g. the always-on _craft book).
  let { value = [], onchange, exclude = [] } = $props();

  let books = $state([]);
  let open = $state(false);
  let root;

  onMount(async () => {
    const r = await get('/lorebooks');
    books = (r?.books || []).filter((b) => !exclude.includes(b.id));
  });

  let attached = $derived(value.map((id) => books.find((b) => b.id === id) || { id, name: id, rating: 'sfw' }));
  let available = $derived(books.filter((b) => !value.includes(b.id)));

  function toggle(id) {
    const next = value.includes(id) ? value.filter((x) => x !== id) : [...value, id];
    onchange?.(next);
  }
  function remove(id) { onchange?.(value.filter((x) => x !== id)); }

  $effect(() => {
    function onDoc(e) { if (root && !root.contains(e.target)) open = false; }
    document.addEventListener('click', onDoc);
    return () => document.removeEventListener('click', onDoc);
  });
</script>

<div class="lp" bind:this={root}>
  <div class="chips">
    {#each attached as b}
      <span class="chip {b.rating}" title={b.name}>{b.name}<button class="rm" onclick={() => remove(b.id)} title="Detach">✕</button></span>
    {/each}
    <button class="add" onclick={(e) => { e.stopPropagation(); open = !open; }}>＋ lorebook</button>
  </div>
  {#if open}
    <div class="pop">
      {#if !available.length}<div class="none">All books attached.</div>{/if}
      {#each available as b (b.id)}
        <button class="opt" onclick={() => { toggle(b.id); }}>
          <span class="oname">{b.name}</span>
          <span class="badge {b.rating}">{b.rating}</span>
          <span class="ocount">{b.entries}</span>
        </button>
      {/each}
      <a class="manage" href="/lorebooks">Manage lorebooks →</a>
    </div>
  {/if}
</div>

<style>
  .lp { position: relative; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .chip { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; padding: 2px 4px 2px 8px;
    border-radius: 7px; border: 1px solid var(--border-soft); background: var(--elev-2); color: var(--text); }
  .chip.nsfw { border-color: rgba(230,120,160,.4); }
  .rm { background: none; border: 0; color: inherit; opacity: .55; cursor: pointer; padding: 0 1px; font-size: 12px; box-shadow: none; }
  .rm:hover { opacity: 1; }
  .add { font-size: 11.5px; padding: 2px 8px; border-radius: 7px; background: var(--elev); border: 1px dashed var(--border); color: var(--muted); box-shadow: none; }
  .add:hover { color: var(--accent); border-color: var(--accent); }
  .pop { position: absolute; z-index: 50; top: 100%; left: 0; margin-top: 5px; min-width: 240px; max-height: 280px; overflow: auto;
    background: var(--elev); border: 1px solid var(--border); border-radius: 10px; box-shadow: var(--shadow); padding: 4px; }
  .opt { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; padding: 6px 9px; border-radius: 7px;
    background: none; border: 0; cursor: pointer; font-size: 12.5px; color: var(--text); }
  .opt:hover { background: var(--elev-2); }
  .oname { flex: 1; }
  .badge { font-size: 9px; font-weight: 700; text-transform: uppercase; padding: 1px 5px; border-radius: 5px; }
  .badge.sfw { color: #8fc7a0; background: rgba(120,200,140,.12); }
  .badge.nsfw { color: #e9a0b8; background: rgba(230,120,160,.14); }
  .ocount { color: var(--faint); font-size: 11px; }
  .none { color: var(--faint); font-size: 12px; padding: 6px 9px; }
  .manage { display: block; font-size: 11.5px; color: var(--muted); padding: 6px 9px; border-top: 1px solid var(--border); margin-top: 4px; text-decoration: none; }
  .manage:hover { color: var(--accent); }
</style>

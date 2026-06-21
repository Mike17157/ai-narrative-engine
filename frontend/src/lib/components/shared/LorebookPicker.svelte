<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { openBrowse } from '$lib/browse.svelte.js';

  // Attach lorebooks to a thread/preset. `value` is an array of book ids; `onchange` fires
  // with the new array. Shows real book names + sfw/nsfw badges. `exclude` hides books the
  // caller manages elsewhere. Picking now goes through the shared browse modal (openBrowse),
  // so every "attach lorebooks" surface looks and behaves the same.
  let { value = [], onchange, exclude = [] } = $props();

  let allBooks = $state([]);
  let books = $derived(allBooks.filter((b) =>
    !exclude.includes(b.id) && (app.allowNsfw || b.rating !== 'nsfw' || value.includes(b.id))));
  onMount(async () => { allBooks = (await get('/lorebooks'))?.books || []; });

  let attached = $derived(value.map((id) => books.find((b) => b.id === id) || { id, name: id, rating: 'sfw' }));
  function remove(id) { onchange?.(value.filter((x) => x !== id)); }
  function browse() {
    openBrowse({
      kind: 'lorebook', multi: true, value, title: 'Attach lorebooks',
      // Only offer GLOBAL books — local ones (function/craft/character) belong to a place,
      // not a hand-attached chat. Keep any already-attached book visible so it can be removed.
      filter: (b) => !exclude.includes(b.id) && (b.scope !== 'local' || value.includes(b.id)),
      onConfirm: (ids) => onchange?.(ids),
    });
  }
</script>

<div class="lp">
  <div class="chips">
    {#each attached as b}
      <span class="chip {b.rating}" title={b.name}>{b.name}<button class="rm" onclick={() => remove(b.id)} title="Detach">✕</button></span>
    {/each}
    <button class="add" onclick={browse}>＋ lorebook</button>
  </div>
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
</style>

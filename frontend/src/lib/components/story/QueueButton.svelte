<script>
  // The To-do button — opens the workflow queue modal (the ordered unfinished sections). Replaces
  // the always-on CardRail/QueueRail so the page isn't cluttered with everything at once; the
  // badge shows how many items are pending. Refreshes on the 'queue:refresh' event that the
  // panels/modal dispatch after generating or resolving work. See workflow.svelte.js.
  import { get } from '$lib/api.js';
  import { openWorkflow } from '$lib/workflow.svelte.js';

  let { storyKey } = $props();
  let count = $state(0);
  async function load() {
    try { count = ((await get(`/stories/${storyKey}/queue`))?.items || []).length; } catch { /* keep */ }
  }
  $effect(() => { if (storyKey) load(); });
  $effect(() => {
    const h = () => load();
    window.addEventListener('queue:refresh', h);
    return () => window.removeEventListener('queue:refresh', h);
  });
</script>

<button class="qbtn" class:clear={!count} onclick={() => openWorkflow(storyKey)}
        title={count ? `${count} unfinished section${count > 1 ? 's' : ''} — open the to-do queue` : 'Everything is done'}>
  ☑ To-do{#if count}<span class="badge">{count}</span>{:else}<span class="tick">✓</span>{/if}
</button>

<style>
  .qbtn { display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; font-weight: 600;
          padding: 6px 12px; border-radius: 999px; cursor: pointer; color: var(--text);
          background: color-mix(in srgb, var(--accent) 12%, transparent);
          border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent); }
  .qbtn:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .qbtn.clear { color: var(--muted); background: none; border-color: var(--border-soft); }
  .badge { min-width: 17px; height: 17px; padding: 0 4px; box-sizing: border-box; border-radius: 999px;
           display: grid; place-items: center; font-size: 10px; font-weight: 800;
           background: var(--accent); color: #0b0e14; }
  .tick { color: var(--good, #5ec27a); font-weight: 800; }
</style>

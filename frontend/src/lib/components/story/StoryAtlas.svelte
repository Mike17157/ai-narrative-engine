<script>
  import StoryAtlasCard from './StoryAtlasCard.svelte';
  import { buildStoryAtlasKernels } from '$lib/story-atlas.js';

  /**
   * A small card-first view of the dramatic kernels a writer needs at a
   * glance: world, premise, people, pressure, possible scenes, and a sealed
   * director layer when one exists. It intentionally
   * accepts either a normal public story card or pre-normalised kernels.
   */
  let {
    story = null,
    graph = null,
    kernels = null,
    selected = '',
    compact = $bindable(true),
    onselect = null
  } = $props();

  let expanded = $state(new Set());
  let atlasKernels = $derived(Array.isArray(kernels) && kernels.length
    ? kernels
    : buildStoryAtlasKernels({ story, graph }));

  function selectedFor(kernel) {
    const value = String(selected || '');
    return value === String(kernel?.id || '') || value === String(kernel?.section || '');
  }

  function compactFor(kernel) {
    return compact && !expanded.has(kernel?.id);
  }

  function toggleKernel(kernel) {
    const id = kernel?.id;
    if (!id) return;
    const next = new Set(expanded);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    expanded = next;
  }

  function selectKernel(kernel) {
    onselect?.(kernel);
  }

  function selectDetail(detail, kernel) {
    if (detail?.target) onselect?.({ ...kernel, target: detail.target });
    else onselect?.(kernel);
  }

  function toggleAll() {
    compact = !compact;
    if (!compact) expanded = new Set();
  }
</script>

<section class="story-atlas" aria-label="Story atlas">
  <header class="atlas-head">
    <div>
      <span>Story atlas</span>
      <h2>The dramatic core</h2>
      <p>{graph?.summary || 'Opening, people, pressure, and possible scenes—kept readable before you enter a focused edit.'}</p>
    </div>
    <button type="button" class="atlas-mode" aria-pressed={!compact} onclick={toggleAll}>{compact ? 'Expand all' : 'Compact all'}</button>
  </header>

  <div class="atlas-grid">
    {#each atlasKernels as kernel (kernel.id)}
      <StoryAtlasCard
        {kernel}
        compact={compactFor(kernel)}
        selected={selectedFor(kernel)}
        canExpand={compact}
        onselect={selectKernel}
        ontoggle={toggleKernel}
        ondetail={selectDetail}
      />
    {/each}
  </div>

  <p class="atlas-help">Choose a kernel to discuss that section. Expand one for its public dramatic details; selecting a detail opens only that item’s focused conversation.</p>
</section>

<style>
  .story-atlas { display: grid; gap: 12px; min-width: 0; padding: 14px; border: 1px solid color-mix(in srgb, var(--accent) 32%, var(--border)); border-radius: 14px; background: color-mix(in srgb, var(--accent) 4%, var(--panel)); }
  .atlas-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
  .atlas-head > div { min-width: 0; }
  .atlas-head span { color: var(--accent); font-size: 10px; font-weight: 850; letter-spacing: .08em; text-transform: uppercase; }
  .atlas-head h2 { margin: 3px 0 0; color: var(--text); font-size: 17px; letter-spacing: -.018em; }
  .atlas-head p { max-width: 52rem; margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.4; }
  .atlas-mode { flex: none; border: 1px solid var(--border-soft); border-radius: 7px; padding: 6px 8px; background: var(--elev); color: var(--text); font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; }
  .atlas-mode:hover, .atlas-mode:focus-visible { border-color: var(--accent); outline: none; }
  .atlas-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; align-items: start; }
  .atlas-help { margin: 0; padding-top: 9px; border-top: 1px solid var(--border-soft); color: var(--faint); font-size: 10px; line-height: 1.35; }
  @media (max-width: 700px) { .atlas-grid { grid-template-columns: 1fr; } }
  @media (max-width: 430px) { .story-atlas { padding: 12px; } .atlas-head { align-items: stretch; flex-direction: column; } .atlas-mode { align-self: flex-start; } }
</style>

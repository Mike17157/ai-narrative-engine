<script>
  // Step 3 — Outfits. For each cast character, generate a series of full-body images from
  // their description + outfit descriptions. Per-character tabs; reuses the portrait studio.
  import { goto } from '$app/navigation';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import OutfitPanel from '$lib/components/story/OutfitPanel.svelte';

  let wz = $derived(stories.wizard);
  let castRoster = $derived((chars.list || []).filter((c) => wz.castKeys.includes(c.key)));
  let sel = $state('');
  let selChar = $derived(castRoster.find((c) => c.key === sel) || castRoster[0] || null);

  $effect(() => { if (!sel && castRoster.length) sel = castRoster[0].key; });
</script>

<div class="page"><div class="col wide">
  <div class="head">
    <div>
      <h2 class="title">Outfits</h2>
      <p class="sub">Generate looks for each character — a description + an outfit becomes a full-body image.</p>
    </div>
    <div class="right">
      {#if castRoster.length > 1}
        <div class="tabs">
          {#each castRoster as c (c.key)}
            <button class="tab" class:on={sel === c.key} onclick={() => (sel = c.key)}>{c.name || c.key}</button>
          {/each}
        </div>
      {/if}
      <button class="ghost" onclick={() => { wz.step = 1; goto('/stories/new/characters'); }}>← Characters</button>
      <button class="next" onclick={() => { wz.step = 3; goto('/stories/new/scenes'); }}>Scenes →</button>
    </div>
  </div>

  {#if selChar}
    {#key sel}<OutfitPanel charKey={selChar.key} charName={selChar.name || selChar.key} />{/key}
  {:else}
    <div class="muted">No cast — go back and pick characters.</div>
  {/if}
</div></div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .title { margin: 0; font-size: 19px; font-weight: 680; }
  .sub { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); }
  .right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .tabs { display: flex; gap: 5px; flex-wrap: wrap; }
  .tab { font-size: 12.5px; padding: 5px 12px; border-radius: 8px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .tab.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .next { padding: 8px 16px; font-size: 13px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .ghost { padding: 8px 14px; font-size: 13px; border-radius: 9px; background: transparent; border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .muted { color: var(--muted); }
</style>

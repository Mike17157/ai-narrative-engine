<script>
  // Step 2 — Characters. Develop the cast through conversation (interview), let them bounce
  // off each other (improv → harvest), keep them distinct (differentiate), and OPTIONALLY weave
  // themed arcs. Reuses the Character Lab components, scoped to this story's cast.
  import { goto } from '$app/navigation';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import CharacterInterview from '$lib/components/story/CharacterInterview.svelte';
  import ImprovPanel from '$lib/components/story/ImprovPanel.svelte';
  import ArcsPanel from '$lib/components/story/ArcsPanel.svelte';

  let wz = $derived(stories.wizard);
  let castRoster = $derived((chars.list || []).filter((c) => wz.castKeys.includes(c.key)));
  let sel = $state('');
  let selChar = $derived(castRoster.find((c) => c.key === sel) || castRoster[0] || null);
  let reloadToken = $state(0);

  $effect(() => { if (!sel && castRoster.length) sel = castRoster[0].key; });
</script>

<div class="page"><div class="col wide">
  <div class="head">
    <div>
      <h2 class="title">Develop your cast</h2>
      <p class="sub">Build each character through conversation; agreed moments become their exemplars.</p>
    </div>
    <div class="right">
      {#if castRoster.length > 1}
        <div class="tabs">
          {#each castRoster as c (c.key)}
            <button class="tab" class:on={sel === c.key} onclick={() => (sel = c.key)}>{c.name || c.key}</button>
          {/each}
        </div>
      {/if}
      <button class="next" onclick={() => { wz.step = 2; goto('/stories/new/outfits'); }}>Outfits →</button>
    </div>
  </div>

  <ImprovPanel roster={castRoster} defaultKeys={wz.castKeys} onHarvest={() => reloadToken++} />
  <ArcsPanel roster={castRoster} defaultKeys={wz.castKeys} />

  {#if selChar}
    {#key sel}
      <CharacterInterview charKey={selChar.key} charName={selChar.name || selChar.key} {reloadToken} />
    {/key}
  {:else}
    <div class="empty"><p>No cast — go back and pick characters.</p>
      <button class="ghost" onclick={() => goto('/stories/new/premise')}>← Premise</button></div>
  {/if}
</div></div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
  .title { margin: 0; font-size: 19px; font-weight: 680; }
  .sub { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); }
  .right { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .tabs { display: flex; gap: 5px; flex-wrap: wrap; }
  .tab { font-size: 12.5px; padding: 5px 12px; border-radius: 8px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .tab.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .next { padding: 8px 16px; font-size: 13px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .empty { margin: 50px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; gap: 10px; align-items: center; }
  .ghost { padding: 8px 16px; border-radius: 9px; background: transparent; border: 1px solid var(--border); color: var(--text); cursor: pointer; }
</style>

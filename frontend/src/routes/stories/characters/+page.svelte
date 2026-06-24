<script>
  // Character Lab — develop characters one at a time through conversation. Pick a character
  // (the roster) and interview them; agreed exemplars accrete into their lorebook.
  import { chars } from '$lib/characters.svelte.js';
  import CharacterInterview from '$lib/components/story/CharacterInterview.svelte';
  import ImprovPanel from '$lib/components/story/ImprovPanel.svelte';
  import ArcsPanel from '$lib/components/story/ArcsPanel.svelte';

  let roster = $derived(chars.list || []);
  let sel = $state('');
  let selChar = $derived(roster.find((c) => c.key === sel) || null);
  let reloadToken = $state(0);   // bumped after an improv harvest so cards refresh

  // Default to the first character once the list loads.
  $effect(() => { if (!sel && roster.length) sel = roster[0].key; });

  let idx = $derived(roster.findIndex((c) => c.key === sel));
  function step(d) {
    if (!roster.length) return;
    const n = (idx + d + roster.length) % roster.length;
    sel = roster[n].key;
  }
</script>

<div class="page"><div class="col wide">
  <div class="head">
    <div>
      <h2 class="title">Character Lab</h2>
      <p class="sub">Build a character through conversation — propose, react, and the agent commits the moments you agree on.</p>
    </div>
    <label class="picker">
      <span>Character {#if roster.length}<i>{idx + 1} / {roster.length}</i>{/if}</span>
      <div class="pickrow">
        <button class="nav" title="Previous" onclick={() => step(-1)} disabled={roster.length < 2}>‹</button>
        <select bind:value={sel}>
          {#each roster as c (c.key)}<option value={c.key}>{c.name || c.key}</option>{/each}
        </select>
        <button class="nav" title="Next character" onclick={() => step(1)} disabled={roster.length < 2}>›</button>
      </div>
    </label>
  </div>

  {#if selChar}
    <ImprovPanel {roster} defaultKeys={[selChar.key]} onHarvest={() => reloadToken++} />
    <ArcsPanel {roster} defaultKeys={[selChar.key]} />
    {#key sel}
      <CharacterInterview charKey={selChar.key} charName={selChar.name || selChar.key} {reloadToken} />
    {/key}
  {:else}
    <div class="empty"><p>No characters yet — create one first.</p></div>
  {/if}
</div></div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
  .title { margin: 0; font-size: 19px; font-weight: 680; }
  .sub { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); max-width: 540px; }
  .picker { display: flex; flex-direction: column; gap: 4px; }
  .picker span { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .picker span i { font-style: normal; color: var(--faint); font-weight: 400; margin-left: 4px; }
  .pickrow { display: flex; gap: 6px; align-items: stretch; }
  .picker select { padding: 8px 10px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border);
    color: var(--text); font: inherit; font-size: 13px; cursor: pointer; min-width: 180px; }
  .nav { width: 30px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border);
    color: var(--text); cursor: pointer; font-size: 15px; line-height: 1; }
  .nav:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .nav:disabled { opacity: .4; cursor: default; }
  .empty { margin: 60px auto; text-align: center; color: var(--muted); }
</style>

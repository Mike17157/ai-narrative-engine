<script>
  // Draft build hub — the non-linear entry point for an in-progress story. Shows every
  // step's status + output preview and lets the user jump to / re-run any step.
  import { goto } from '$app/navigation';
  import { stories, generateSpine, genStoryboard, genScenes, genCharacters } from '$lib/stories.svelte.js';
  import StoryBuildHub from '$lib/components/story/StoryBuildHub.svelte';

  let wz = $derived(stories.wizard);
  const go = (route) => goto(`/stories/new/${route}`);
  const RERUN = { spine: generateSpine, storyboard: genStoryboard, scenes: genScenes, characters: genCharacters };
  function rerun(route) { go(route); RERUN[route]?.(); }
</script>

<div class="page"><div class="col">
  {#if !wz.character}
    <div class="empty">
      <div class="emk">🧭</div>
      <p>No story in progress.</p>
      <span>Start a new story or resume a draft from the library.</span>
      <button onclick={() => goto('/stories')}>← Library</button>
    </div>
  {:else}
    <div class="head">
      <div>
        <h2 class="title">{wz.name || wz.charName || 'New story'}</h2>
        <p class="sub">Build progress — jump to or re-run any step.</p>
      </div>
      <button class="ghost" onclick={() => go('setup')}>Open setup</button>
    </div>
    <StoryBuildHub source={wz} onGoStep={go} onRerun={rerun} />
  {/if}
</div></div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 16px; }
  .title { margin: 0; font-size: 19px; font-weight: 680; }
  .sub { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); }
  .ghost { padding: 7px 14px; font-size: 13px; border-radius: 9px; background: transparent;
    border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .ghost:hover { border-color: var(--accent); color: var(--accent); }
  .empty { margin: 60px auto; text-align: center; color: var(--muted);
    display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .empty p { margin: 0; color: var(--text); font-size: 16px; }
  .empty span { font-size: 12.5px; max-width: 360px; }
  .emk { font-size: 34px; }
  .empty button { margin-top: 12px; }
</style>

<script>
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { stories } from '$lib/stories.svelte.js';

  let { children } = $props();
  let wz = $derived(stories.wizard);

  // The story-GENERATION steps render as an in-page header strip (the editing sections live in
  // the sidebar instead). Each step unlocks as the draft progresses.
  const WSTEPS = [
    { id: 'setup', label: '1 · Setup' },
    { id: 'storyboard', label: '2 · Storyboard' },
    { id: 'scenes', label: '3 · Scenes' },
    { id: 'characters', label: '4 · Characters' }
  ];
  // Which steps are reachable given the draft's progress.
  let reach = $derived({
    setup: true,
    storyboard: !!wz.board || wz.streaming,
    scenes: !!wz.locations,
    characters: wz.cast !== null && wz.cast !== undefined
  });
  let step = $derived($page.url.pathname.split('/')[3] || 'setup');  // /stories/new/<step>

  // Guard: deep-linking past your progress bounces to the furthest reached step.
  $effect(() => {
    if (!step) return;
    if (!reach[step]) {
      const furthest = reach.characters ? 'characters' : reach.scenes ? 'scenes' : reach.storyboard ? 'storyboard' : 'setup';
      goto(`/stories/new/${furthest}`, { replaceState: true });
    }
  });
</script>

<header class="whead">
  <div class="wtitle">
    <a class="back" href="/stories" title="All stories">←</a>
    <h2>New story</h2>
  </div>
  <nav class="wsteps">
    {#each WSTEPS as s (s.id)}
      <button class:on={step === s.id} disabled={!reach[s.id]}
        onclick={() => reach[s.id] && goto(`/stories/new/${s.id}`)}>{s.label}</button>
    {/each}
  </nav>
</header>

{@render children()}

<style>
  .whead {
    position: sticky; top: 0; z-index: 5; background: var(--bg);
    border-bottom: 1px solid var(--border-soft); padding: 14px 24px 0;
  }
  .wtitle { display: flex; align-items: center; gap: 12px; max-width: 1040px; margin: 0 auto; }
  .back {
    text-decoration: none; color: var(--muted); font-size: 18px; line-height: 1;
    width: 30px; height: 30px; display: grid; place-items: center; flex: none;
    border-radius: 8px; border: 1px solid var(--border); background: var(--elev);
  }
  .back:hover { color: var(--text); background: var(--elev-2); }
  .wtitle h2 { margin: 0; font-size: 20px; font-weight: 680; }

  .wsteps { display: flex; gap: 4px; max-width: 1040px; margin: 10px auto 0; }
  .wsteps button {
    background: none; border: none; box-shadow: none; color: var(--muted); font-size: 13.5px; font-weight: 600;
    padding: 9px 14px; border-radius: 9px 9px 0 0; border-bottom: 2px solid transparent;
  }
  .wsteps button:hover:not(:disabled) { color: var(--text); filter: none; background: var(--elev); }
  .wsteps button.on { color: var(--text); border-bottom-color: var(--accent); }
  .wsteps button:disabled { opacity: .4; cursor: not-allowed; }
</style>

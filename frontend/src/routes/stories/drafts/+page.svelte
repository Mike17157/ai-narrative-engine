<script>
  // In progress — stories still being built (wizard drafts), separated from the
  // Library/Finished gallery. Each card shows its per-step progress with Resume
  // (continue at your step) and Overview (the non-linear build hub).
  import { del } from '$lib/api.js';
  import { chars } from '$lib/characters.svelte.js';
  import { stories, resumeDraft, resetWizard, startWizard } from '$lib/stories.svelte.js';
  import StoryProgressCard from '$lib/components/story/StoryProgressCard.svelte';

  let drafts = $derived((stories.list || []).filter((s) => s.draft));

  function newStory() {
    const c = chars.list.find((x) => !x.story) || chars.list[0];
    startWizard(c?.key || '', c?.name || '');
  }
  function discardDraft(s) {
    del(`/stories/draft/${s.id}`).then(() => {
      stories.list = stories.list.filter((x) => x.id !== s.id);
      if (stories.wizard?.draftId === s.id) resetWizard();
    });
  }
</script>

<div class="page"><div class="col wide">

  {#if stories.msg}<div class="msg" class:ok={stories.msg.ok} class:err={stories.msg.err}>{stories.msg.text}</div>{/if}

  <div class="sechead">
    <span class="sectitle">In progress{drafts.length ? ` · ${drafts.length}` : ''}</span>
    <button onclick={newStory}>＋ New story</button>
  </div>

  {#if drafts.length}
    <div class="drafts">
      {#each drafts as s (s.id)}
        <StoryProgressCard draft={s}
          onResume={(d) => resumeDraft(d.id, 'step')}
          onOverview={(d) => resumeDraft(d.id, 'step')}
          onDiscard={discardDraft} />
      {/each}
    </div>
  {:else}
    <div class="empty">
      <div class="emk">🧭</div>
      <p>Nothing in progress.</p>
      <span>Start a new story from a character — build its characters, outfits and scenes.</span>
      <button onclick={newStory}>＋ New story</button>
    </div>
  {/if}

</div></div>

<style>
  .sechead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  .sectitle { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .msg { margin: 10px 0; font-size: 13px; }
  .msg.ok { color: var(--good); } .msg.err { color: var(--bad); }
  .drafts { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
  .empty { margin: 60px auto; text-align: center; color: var(--muted);
    display: flex; flex-direction: column; align-items: center; gap: 8px; }
  .empty p { margin: 0; color: var(--text); font-size: 16px; }
  .empty span { font-size: 12.5px; max-width: 360px; line-height: 1.5; }
  .emk { font-size: 34px; }
  .empty button { margin-top: 12px; }
</style>

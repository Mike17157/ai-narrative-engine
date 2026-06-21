<script>
  // Iterate a SAVED story as a graph, conversationally. Seeds the console from the
  // story's graph; "Save changes" persists the edited graph back (storyboard + arcs).
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import StoryConsole from '$lib/components/story/StoryConsole.svelte';
  import { storyToGraph, graphToBoard, graphToArcs } from '$lib/story_graph_model.js';
  import { stories, loadStory, expandGraphRequest } from '$lib/stories.svelte.js';
  import { put } from '$lib/api.js';

  let key = $derived($page.params.key);
  let story = $state(stories.current?.key === ($page.params.key) ? stories.current : null);
  let saving = $state(false);
  let drafting = $state(false);
  let msg = $state(null);

  onMount(async () => { if (!story) story = await loadStory(key); });

  let charKey = $derived(
    story?.fields?.source_character ||
    story?.cast?.find((m) => m.primary)?.character ||
    story?.cast?.[0]?.character || '');
  let charName = $derived(story?.cast?.find((m) => m.primary)?.name || story?.name || '');

  async function persist(graph) {
    const body = { storyboard: graphToBoard(graph, story?.storyboard || {}), arcs: graphToArcs(graph) };
    const r = await put(`/stories/${key}`, body);
    msg = r.ok ? { ok: true, text: '✓ Saved' } : { err: true, text: r.data?.error || 'save failed' };
    if (r.ok) story = await loadStory(key);
  }

  // Save the graph as-is (developmental skeleton).
  async function save(graph) {
    if (!graph || saving || drafting) return;
    saving = true; msg = null;
    await persist(graph);
    saving = false;
  }

  // Faithfully expand each beat into a chapter, then save.
  async function draftAndSave(graph) {
    if (!graph?.nodes?.length || saving || drafting) return;
    drafting = true; msg = null;
    const enriched = await expandGraphRequest(charKey, graph);
    await persist(enriched);
    drafting = false;
  }
</script>

<div class="page"><div class="col wide">
  <div class="wshd">
    <div>
      <h2>Iterate — {story?.name || key}</h2>
      <p class="lo">Talk through changes; the consultant revises the graph. Drag to branch or merge beats. Save when you're happy.</p>
    </div>
    <a class="back" href={`/stories/${key}/overview`}>← Overview</a>
  </div>

  {#if msg}<div class="msg" class:ok={msg.ok} class:err={msg.err}>{msg.text}</div>{/if}

  {#if story}
    {#key story.key}
      <StoryConsole
        character={charKey}
        {charName}
        sessionId={`story-${key}`}
        title="Story Workshop"
        subtitle={story.name}
        initialGraph={storyToGraph(story)}
        autostart={false}
      >
        {#snippet actions({ graph, busy })}
          <span style="flex:1"></span>
          <button class="ghost sm" disabled={busy || saving || drafting} onclick={() => save(graph)}>{saving ? 'Saving…' : 'Save graph'}</button>
          <button class="go" disabled={busy || saving || drafting || !graph?.nodes?.length} onclick={() => draftAndSave(graph)}>{drafting ? 'Drafting…' : '✓ Draft & save'}</button>
        {/snippet}
      </StoryConsole>
    {/key}
  {:else}
    <p class="lo">Loading story…</p>
  {/if}
</div></div>

<style>
  .col.wide { max-width: 1180px; }
  .wshd { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px; }
  .wshd h2 { margin: 0 0 2px; font-size: 19px; }
  .lo { margin: 0; font-size: 12.5px; color: var(--faint); }
  .back { margin-left: auto; flex: none; font-size: 13px; color: var(--accent); text-decoration: none; padding-top: 4px; }
  .back:hover { text-decoration: underline; }
  .msg { font-size: 12.5px; padding: 7px 11px; border-radius: 8px; margin-bottom: 10px; }
  .msg.ok { background: rgba(100,210,130,.1); border: 1px solid rgba(100,210,130,.25); color: rgba(100,210,130,.95); }
  .msg.err { background: rgba(255,122,122,.1); border: 1px solid rgba(255,122,122,.25); color: var(--bad, #ff7a7a); }
  .go { font-size: 13px; font-weight: 700; padding: 8px 18px; border-radius: 9px; background: var(--accent); border: 0; color: #fff; cursor: pointer; }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: .4; cursor: not-allowed; }
  .ghost.sm { font-size: 12px; padding: 7px 12px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .ghost.sm:hover:not(:disabled) { color: var(--text); }
  .ghost.sm:disabled { opacity: .4; cursor: not-allowed; }
</style>

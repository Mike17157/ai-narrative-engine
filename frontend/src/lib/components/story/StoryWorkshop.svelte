<script>
  // Story SETUP: the consultation console for a fresh story. Builds the development
  // graph (arc of change) from the running conversation; "Generate" faithfully expands
  // that graph into a draft, or — without a graph — seeds a fresh spine.
  import StoryConsole from '$lib/components/story/StoryConsole.svelte';

  let {
    character = '',
    charName  = '',
    sessionId = '',
    onGenerate = null,   // ({ graph?, premise? }) => advance to draft
  } = $props();
</script>

<StoryConsole
  {character}
  {charName}
  {sessionId}
  title="Story Workshop"
  subtitle={`Literary consultation — ${charName || character}`}
>
  {#snippet actions({ graph, busy, hasExchange, lastAssistant, ready })}
    {#if ready}<span class="ready">● ready to draft</span>{/if}
    <span style="flex:1"></span>
    <button class="soft sm" disabled={busy || !hasExchange}
      onclick={() => onGenerate?.({ premise: graph?.logline?.trim() || lastAssistant })}>Quick draft (skip graph) →</button>
    <button class="go" class:pulse={ready} disabled={busy || !hasExchange || !graph?.nodes?.length}
      onclick={() => onGenerate?.({ graph })}>
      {graph?.nodes?.length ? `Draft from this arc (${graph.nodes.length}) →` : 'Build the arc first…'}
    </button>
  {/snippet}
</StoryConsole>

<style>
  .go { font-size: 13px; font-weight: 700; padding: 8px 18px; border-radius: 9px; background: var(--accent); border: 0; color: #fff; cursor: pointer; }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: .4; cursor: not-allowed; }
  .ready { font-size: 11px; font-weight: 700; color: rgba(100,210,130,.95); align-self: center; }
  .go.pulse:not(:disabled) { box-shadow: 0 0 0 0 rgba(100,210,130,.5); animation: gopulse 1.6s ease-out infinite; }
  @keyframes gopulse { 0% { box-shadow: 0 0 0 0 rgba(100,210,130,.45); } 70% { box-shadow: 0 0 0 7px rgba(100,210,130,0); } 100% { box-shadow: 0 0 0 0 rgba(100,210,130,0); } }
</style>

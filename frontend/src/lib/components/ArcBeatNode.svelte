<script>
  import { Handle, Position } from '@xyflow/svelte';

  let { data, selected } = $props();
  // data mirrors ArcBeat: title, emotional_core, hook (description), characters, location, bg
</script>

<div class="beat-node" class:selected class:has-bg={data.bg}>
  <Handle type="target" position={Position.Top} id="in" style="opacity:0;pointer-events:none;width:1px;height:1px;min-width:0;min-height:0;border:0;" />

  {#if data.bg}
    <img class="beat-bg" src={data.bg} alt="" />
    <div class="beat-scrim"></div>
  {/if}

  <div class="beat-title">{data.title || 'Chapter'}</div>

  {#if data.emotional_core}
    <div class="beat-ec">♡ {data.emotional_core}</div>
  {/if}

  {#if data.hook}
    <div class="beat-desc">{data.hook}</div>
  {/if}

  {#if data.characters?.length}
    <div class="beat-chars">
      {#each data.characters as c}
        <span class="char-chip">{c}</span>
      {/each}
    </div>
  {/if}

  <Handle type="source" position={Position.Bottom} id="out" style="opacity:0;pointer-events:none;width:1px;height:1px;min-width:0;min-height:0;border:0;" />
</div>

<style>
  /* Height is FIXED and must equal BEAT_H in story_graph.js — ELK + edge anchors
     assume it. Content is clamped to fit so the box never grows. */
  .beat-node {
    width: 300px;
    height: 188px;
    box-sizing: border-box;
    background: var(--panel, #1a1d27);
    border: 1px solid var(--border-soft, rgba(255,255,255,.09));
    border-radius: 11px;
    padding: 11px 13px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    cursor: pointer;
    box-shadow: 0 2px 10px rgba(0,0,0,.35);
    position: relative;
    overflow: hidden;
    transition: border-color .15s, box-shadow .15s;
  }
  .beat-node:hover { border-color: var(--accent, #6d8cff); }
  .beat-node.selected {
    border-color: var(--accent, #6d8cff);
    box-shadow: 0 0 0 2px rgba(109,140,255,.25), 0 2px 10px rgba(0,0,0,.35);
  }

  /* Scene background: the location's image, dimmed for text legibility. */
  .beat-bg { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; z-index: 0; }
  .beat-scrim { position: absolute; inset: 0; z-index: 1; background: linear-gradient(180deg, rgba(10,12,18,.5) 0%, rgba(10,12,18,.82) 100%); }
  .beat-node.has-bg > :global(*:not(.beat-bg):not(.beat-scrim)) { position: relative; z-index: 2; }

  .beat-title {
    font-size: 14.5px; font-weight: 700; color: var(--text, #e0e4f0); line-height: 1.3;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .beat-ec {
    font-size: 11.5px; color: var(--accent, #6d8cff); font-style: italic; line-height: 1.35;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; flex: none;
  }
  /* The description — the main body of the card. */
  .beat-desc {
    flex: 1; min-height: 0;
    font-size: 13px; color: var(--muted, #8a92b0); line-height: 1.45;
    display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden;
  }
  .beat-chars { display: flex; flex-wrap: wrap; gap: 4px; flex: none; max-height: 22px; overflow: hidden; }
  .char-chip {
    font-size: 10px; padding: 1px 7px; border-radius: 999px;
    background: rgba(109,140,255,.14); border: 1px solid rgba(109,140,255,.26); color: var(--accent, #6d8cff); white-space: nowrap;
  }

  .beat-node.has-bg { border-color: rgba(255,255,255,.14); }
  .beat-node.has-bg .beat-title { color: #fff; text-shadow: 0 1px 4px rgba(0,0,0,.6); }
  .beat-node.has-bg .beat-ec { color: #b9c6ff; }
  .beat-node.has-bg .beat-desc { color: rgba(255,255,255,.86); }
</style>

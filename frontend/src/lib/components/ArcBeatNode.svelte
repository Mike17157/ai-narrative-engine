<script>
  import { Handle, Position } from '@xyflow/svelte';

  let { id, data, selected } = $props();
  // data mirrors ArcBeat: title, emotional_core, hook, characters, location
</script>

<div class="beat-node" class:selected>
  <Handle type="target" position={Position.Top} id="in" />

  <div class="beat-title">{data.title || 'Chapter'}</div>

  {#if data.emotional_core}
    <div class="beat-ec">♡ {data.emotional_core}</div>
  {/if}

  {#if data.hook}
    <div class="beat-hook">{data.hook}</div>
  {/if}

  {#if data.characters?.length}
    <div class="beat-chars">
      {#each data.characters as c}
        <span class="char-chip">{c}</span>
      {/each}
    </div>
  {/if}

  <Handle type="source" position={Position.Bottom} id="out" />
</div>

<style>
  .beat-node {
    width: 264px;
    background: var(--panel, #1a1d27);
    border: 1px solid var(--border-soft, rgba(255,255,255,.09));
    border-radius: 10px;
    padding: 10px 12px 10px;
    display: flex;
    flex-direction: column;
    gap: 5px;
    cursor: default;
    box-shadow: 0 2px 10px rgba(0,0,0,.35);
    position: relative;
    transition: border-color .15s;
  }
  .beat-node.selected {
    border-color: var(--accent, #6d8cff);
    box-shadow: 0 0 0 2px rgba(109,140,255,.2), 0 2px 10px rgba(0,0,0,.35);
  }

  .beat-title {
    font-size: 13px;
    font-weight: 700;
    color: var(--text, #e0e4f0);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .beat-ec {
    font-size: 11.5px;
    color: var(--muted, #8a92b0);
    font-style: italic;
    line-height: 1.4;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .beat-hook {
    font-size: 11px;
    color: var(--faint, #555b78);
    line-height: 1.35;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .beat-chars {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    margin-top: 1px;
  }
  .char-chip {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 999px;
    background: rgba(109,140,255,.12);
    border: 1px solid rgba(109,140,255,.22);
    color: var(--accent, #6d8cff);
    white-space: nowrap;
  }
</style>

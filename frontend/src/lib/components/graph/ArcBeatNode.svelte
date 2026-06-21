<script>
  import { Handle, Position } from '@xyflow/svelte';

  let { data, selected } = $props();
  // data._onClick injected by StoryGraph.rebuild(); data.tlColor set for timeline nodes
  const H = 'opacity:0;pointer-events:none;width:1px;height:1px;min-width:0;min-height:0;border:0;';
</script>

<div class="chapter-card" class:selected
  style={data.tlColor ? `--tl-color:${data.tlColor}` : ''}
  onclick={() => data._onClick?.(data)}>

  <!-- Six handles (invisible) — edges reference by id for precise routing -->
  <Handle type="target" position={Position.Top}    id="in-top"    style={H} />
  <Handle type="target" position={Position.Left}   id="in-left"   style={H} />
  <Handle type="target" position={Position.Right}  id="in-right"  style={H} />
  <Handle type="source" position={Position.Bottom} id="out-bottom" style={H} />
  <Handle type="source" position={Position.Right}  id="out-right"  style={H} />
  <Handle type="source" position={Position.Left}   id="out-left"   style={H} />

  <!-- Scene image -->
  <div class="scene-img">
    {#if data.bg}
      <img src={data.bg} alt={data.location || ''} />
      {#if data.location}<span class="scene-loc">{data.location}</span>{/if}
    {:else}
      <div class="scene-ph">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="2"/>
          <circle cx="8.5" cy="8.5" r="1.5"/>
          <polyline points="21 15 16 10 5 21"/>
        </svg>
        <span>{data.location || 'No scene image'}</span>
      </div>
    {/if}
  </div>

  <div class="chapter-body">
    <div class="chapter-title">{data.title || 'Chapter'}</div>
    {#if data.emotional_core}
      <div class="chapter-ec">♡ {data.emotional_core}</div>
    {/if}
    {#if data.hook}
      <div class="chapter-desc">{data.hook}</div>
    {/if}
    {#if data.characters?.length}
      <div class="chapter-chars">
        {#each data.characters as c}
          <span class="char-chip">{c}</span>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .chapter-card {
    width: 300px;
    box-sizing: border-box;
    background: var(--panel, #1a1d27);
    border: 1.5px solid var(--border-soft, rgba(255,255,255,.09));
    border-radius: 11px;
    display: flex; flex-direction: column;
    cursor: pointer;
    box-shadow: 0 2px 12px rgba(0,0,0,.4);
    overflow: hidden;
    transition: border-color .15s, box-shadow .15s;
  }
  /* Timeline-coloured accent on left edge */
  .chapter-card:has(+ *) { border-left-width: 3px; }
  .chapter-card[style] { border-left-color: var(--tl-color, var(--border-soft)); }

  .chapter-card:hover {
    border-color: var(--tl-color, var(--accent, #6d8cff));
    box-shadow: 0 4px 20px rgba(109,140,255,.15), 0 2px 8px rgba(0,0,0,.4);
  }
  .chapter-card.selected {
    border-color: var(--tl-color, var(--accent, #6d8cff));
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--tl-color, var(--accent, #6d8cff)) 40%, transparent), 0 2px 12px rgba(0,0,0,.4);
  }

  .scene-img {
    flex: none; height: 108px; position: relative;
    background: color-mix(in srgb, var(--elev, #22263a) 80%, var(--tl-color, var(--accent, #6d8cff)) 8%);
    border-bottom: 1px solid var(--border-soft, rgba(255,255,255,.06));
    overflow: hidden;
  }
  .scene-img img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .scene-loc {
    position: absolute; bottom: 0; left: 0; right: 0;
    padding: 20px 9px 5px;
    background: linear-gradient(to top, rgba(8,10,18,.85) 0%, transparent 100%);
    font-size: 10px; font-weight: 600; color: rgba(255,255,255,.9);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .scene-ph {
    height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 6px;
    color: var(--faint, #555b78);
  }
  .scene-ph svg { opacity: .4; }
  .scene-ph span { font-size: 10.5px; font-style: italic; }

  .chapter-body {
    flex: 1; padding: 10px 13px 12px;
    display: flex; flex-direction: column; gap: 5px;
  }
  .chapter-title { font-size: 14px; font-weight: 700; color: var(--text, #e0e4f0); line-height: 1.35; }
  .chapter-ec { font-size: 11.5px; color: var(--tl-color, var(--accent, #6d8cff)); font-style: italic; line-height: 1.3; }
  .chapter-desc { font-size: 12.5px; color: var(--muted, #8a92b0); line-height: 1.5; }
  .chapter-chars { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
  .char-chip {
    font-size: 10px; padding: 1px 7px; border-radius: 999px;
    background: color-mix(in srgb, var(--tl-color, var(--accent, #6d8cff)) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--tl-color, var(--accent, #6d8cff)) 25%, transparent);
    color: var(--tl-color, var(--accent, #6d8cff)); white-space: nowrap;
  }
</style>

<script>
  // Standard chapter display card for story storyboard beats.
  // Shows chapter number, title, summary, emotional core, location, characters, and hook.
  // Hover overlay (bottom-right) exposes ↻ regen and ✎ edit buttons.
  let { chapter = {}, index = 0, onRegen = null, onEdit = null, active = true } = $props();

  const MAX_SUMMARY = 160;
  let expanded = $state(false);

  let summary = $derived(chapter.summary || '');
  let truncated = $derived(summary.length > MAX_SUMMARY && !expanded);
  let displaySummary = $derived(truncated ? summary.slice(0, MAX_SUMMARY).trimEnd() + '…' : summary);
</script>

<div class="cc" class:dim={!active}>
  <div class="cchead">
    <span class="cnum">{index + 1}</span>
    <span class="ctitle">{chapter.title || 'Untitled Chapter'}</span>
  </div>

  {#if summary}
    <p class="csummary">
      {displaySummary}
      {#if summary.length > MAX_SUMMARY}
        <button class="expand" onclick={() => (expanded = !expanded)}>{expanded ? 'less' : 'more'}</button>
      {/if}
    </p>
  {/if}

  {#if chapter.emotional_core}
    <p class="cemotional">♡ {chapter.emotional_core}</p>
  {/if}

  <div class="cmeta">
    {#if chapter.location}
      <span class="cloc">{chapter.location}</span>
    {/if}
    {#each (chapter.characters || []) as name (name)}
      <span class="cchar">{name}</span>
    {/each}
  </div>

  {#if chapter.hook}
    <p class="chook">→ {chapter.hook}</p>
  {/if}

  <!-- hover overlay: regen + edit buttons (bottom-right) -->
  {#if onRegen || onEdit}
    <div class="ccbtns">
      {#if onRegen}
        <button class="ccb" title="Regenerate chapter" onclick={(e) => { e.stopPropagation(); onRegen(index); }}>↻</button>
      {/if}
      {#if onEdit}
        <button class="ccb" title="Edit chapter" onclick={(e) => { e.stopPropagation(); onEdit(index); }}>✎</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .cc {
    position: relative;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 11px 13px 10px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    transition: border-color .12s, opacity .12s;
  }
  .cc:hover { border-color: var(--accent); }
  .cc.dim { opacity: .45; pointer-events: none; }

  .cchead {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .cnum {
    width: 20px;
    height: 20px;
    flex: none;
    border-radius: 50%;
    display: grid;
    place-items: center;
    font-size: 10px;
    font-weight: 700;
    background: var(--accent);
    color: #0b0e14;
  }
  .ctitle {
    font-size: 13.5px;
    font-weight: 700;
    color: var(--text);
    line-height: 1.3;
  }

  .csummary {
    margin: 0;
    font-size: 12.5px;
    color: var(--text);
    line-height: 1.55;
  }
  .expand {
    background: none;
    border: none;
    padding: 0;
    box-shadow: none;
    font-size: 11px;
    color: var(--accent);
    cursor: pointer;
    margin-left: 3px;
    text-decoration: underline;
    vertical-align: baseline;
  }
  .expand:hover { filter: brightness(1.15); }

  .cemotional {
    margin: 0;
    font-size: 12px;
    color: var(--muted);
    font-style: italic;
    line-height: 1.4;
  }

  .cmeta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .cloc, .cchar {
    font-size: 10.5px;
    padding: 2px 7px;
    border-radius: 999px;
    line-height: 1.4;
  }
  .cloc {
    background: rgba(109, 140, 255, .13);
    color: var(--accent);
    border: 1px solid rgba(109, 140, 255, .25);
  }
  .cchar {
    background: var(--elev);
    color: var(--muted);
    border: 1px solid var(--border-soft);
  }

  .chook {
    margin: 0;
    font-size: 11.5px;
    color: var(--faint);
    line-height: 1.4;
  }

  /* hover overlay buttons — bottom-right corner, reveal on card hover */
  .ccbtns {
    position: absolute;
    bottom: 6px;
    right: 6px;
    display: flex;
    gap: 3px;
    opacity: 0;
    transition: opacity .12s;
  }
  .cc:hover .ccbtns { opacity: 1; }

  .ccb {
    width: 24px;
    height: 24px;
    padding: 0;
    border-radius: 6px;
    font-size: 12px;
    line-height: 1;
    background: rgba(10, 12, 20, .72);
    border: 1px solid rgba(255, 255, 255, .14);
    color: #fff;
    backdrop-filter: blur(4px);
    box-shadow: none;
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  .ccb:hover { background: rgba(30, 38, 60, .92); filter: none; }
</style>

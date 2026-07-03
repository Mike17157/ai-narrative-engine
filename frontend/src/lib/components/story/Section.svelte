<script>
  // The ONE collapsible pane section for story surfaces — a consistent summary chrome
  // (icon · TITLE · count) over a stacked body. Standardizes the drifted per-tab section
  // styles (dp-details & co). Put an explanatory <p class="hint"> first in the body when
  // the pane needs one; the global .hint is the one hint style.
  let { icon = '', title, count = '', open = false, children } = $props();
</script>

<details class="sect" {open}>
  <summary>{icon} {title}{#if count !== '' && count != null} <span class="n">{count}</span>{/if}</summary>
  <div class="body">{@render children?.()}</div>
</details>

<style>
  .sect { margin: 4px 0; }
  summary {
    cursor: pointer; user-select: none; list-style: none; display: inline-flex; align-items: center; gap: 7px;
    font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); padding: 4px 0;
  }
  summary::-webkit-details-marker { display: none; }
  summary:hover { color: var(--muted); }
  .n { font-size: 10px; color: var(--accent); }
  .body { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; }
  .body :global(p.hint) { margin: 0; line-height: 1.5; max-width: 560px; }
</style>

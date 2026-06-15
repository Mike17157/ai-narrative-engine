<script>
  // Faceted suggestion list — graph-compatible tags grouped by facet (hair/top/legwear/…), each
  // facet sliced to `per` (the length slider). Click a pill → onpick(tag). `swapLabel` (the targeted
  // tag, or null) just tunes the tooltip.
  let { palette = {}, per = 24, swapLabel = null, busy = false, onpick } = $props();
  let facetOrder = $derived(Object.keys(palette));
</script>

<div class="sug">
  {#if busy && !facetOrder.length}<p class="lo">finding compatible tags…</p>
  {:else if !facetOrder.length}<p class="lo">no graph suggestions for these tags — use search above.</p>
  {:else}
    {#each facetOrder as f (f)}
      <div class="facet">
        <div class="fname">{f}</div>
        <div class="pills">
          {#each palette[f].slice(0, per) as t (t)}<button class="pill" onclick={() => onpick?.(t)} title={swapLabel ? 'swap for ' + swapLabel : 'add'}>{t}</button>{/each}
        </div>
      </div>
    {/each}
  {/if}
</div>

<style>
  .sug { flex: 1; overflow: auto; padding-right: 4px; }
  .facet { margin-bottom: 10px; }
  .fname { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 5px; }
  .pills { display: flex; flex-wrap: wrap; gap: 6px; }
  .pill { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); box-shadow: none; cursor: pointer; }
  .pill:hover { border-color: var(--accent); color: var(--accent); filter: none; }
  .lo { color: var(--faint); font-size: 12px; }
</style>

<script>
  // Cast height chart — every member's full-body reference scaled to a SHARED baseline with feet on
  // one floor line, so relative stature reads at a glance. Height can't render in a solo sprite, so
  // it lives as height_cm metadata; here we apply it. The tallest fills the chart; others scale by
  // height_cm / max. Members without a reference image are skipped; a missing height_cm shows '—'
  // and sits at a neutral fallback so it doesn't distort the scale.
  let { cast = [] } = $props();
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);
  const FALLBACK = 168;
  const hOf = (c) => Number(c.height) || FALLBACK;
  let shown = $derived(cast.filter((c) => c.hasRef));
  let maxH = $derived(Math.max(172, ...shown.map(hOf)));
  const pct = (c) => ((hOf(c) / maxH) * 96).toFixed(1);
  function ftin(cm) { const t = Math.round(cm / 2.54); return `${Math.floor(t / 12)}'${t % 12}"`; }
</script>

{#if shown.length > 1}
  <div class="lineup">
    <div class="cap">Cast — relative height</div>
    <div class="row">
      {#each shown as c (c.character)}
        <div class="fig">
          <div class="chart">
            <img src={abs(`/api/characters/${c.character}/reference`)} alt={c.name} style="height:{pct(c)}%" />
          </div>
          <div class="lab">
            <span class="nm">{c.name}</span>
            <span class="cm">{Number(c.height) ? `${c.height} cm · ${ftin(c.height)}` : '—'}</span>
          </div>
        </div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .lineup { background: var(--elev); border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px 10px; }
  .cap { font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin-bottom: 8px; }
  .row { display: flex; align-items: flex-end; gap: 14px; overflow-x: auto; padding-bottom: 2px; }
  .fig { display: flex; flex-direction: column; align-items: center; flex: 0 0 auto; }
  /* fixed chart height; each figure scaled within it, feet on the shared bottom line */
  .chart { height: 300px; display: flex; align-items: flex-end; justify-content: center;
           border-bottom: 2px solid var(--border); padding: 0 4px; }
  .chart img { width: auto; object-fit: contain; filter: drop-shadow(0 4px 12px rgba(0, 0, 0, .45)); }
  .lab { text-align: center; margin-top: 6px; max-width: 140px; }
  .lab .nm { display: block; font-size: 12px; font-weight: 600; color: var(--text); }
  .lab .cm { display: block; font-size: 11px; color: var(--accent); font-variant-numeric: tabular-nums; }
</style>

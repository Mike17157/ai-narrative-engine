<script>
  // A compact valence/arousal circumplex plot of a character's personality-rooted emotion range.
  // Each dot is one emotion they express, placed at its (valence, arousal) coordinate; the dot is
  // filled when a sprite is rendered for it (in the first outfit). An art-direction affordance so
  // the range is visible at a glance — complements the Sprites carousel (which lays the same range
  // out on its X axis).
  import { get } from '$lib/api.js';

  let { charKey, refresh = 0, compact = false } = $props();
  let data = $state(null);

  async function load() {
    try { data = await get(`/characters/${charKey}/portraits`); }
    catch { data = null; }
  }
  $effect(() => { charKey; refresh; load(); });

  // SVG geometry: a square plot, valence on X (left -1 → right +1), arousal on Y (bottom -1 → top +1).
  const SIZE = 150, PAD = 16;
  const x = (v) => PAD + ((v + 1) / 2) * (SIZE - 2 * PAD);
  const y = (a) => SIZE - PAD - ((a + 1) / 2) * (SIZE - 2 * PAD);   // invert: +arousal = up

  let range = $derived(data?.affect?.range || []);
  // which emotions have a rendered sprite (first outfit) → filled dot
  let rendered = $derived(new Set(
    (data?.outfits?.[0]?.expression_set || [])
      .filter((e) => e.url).map((e) => e.emotion)));
</script>

{#if range.length}
  <div class="scatter" class:compact
       title="valence (↔) × arousal (↕) — the character's emotional expression range">
    <svg viewBox={`0 0 ${SIZE} ${SIZE}`} class="plot">
      <!-- axes -->
      <line class="axis" x1={PAD} y1={SIZE / 2} x2={SIZE - PAD} y2={SIZE / 2} />
      <line class="axis" x1={SIZE / 2} y1={PAD} x2={SIZE / 2} y2={SIZE - PAD} />
      <text class="alab" x={SIZE - PAD} y={SIZE / 2 - 3}>+v</text>
      <text class="alab" x={PAD} y={SIZE / 2 - 3}>-v</text>
      <text class="alab" x={SIZE / 2 + 3} y={PAD + 4}>+a</text>
      <text class="alab" x={SIZE / 2 + 3} y={SIZE - PAD + 2}>-a</text>
      <!-- emotion dots -->
      {#each range as e (e.emotion)}
        {@const has = rendered.has(e.emotion)}
        <circle class="dot" class:filled={has}
          cx={x(e.valence)} cy={y(e.arousal)} r={has ? 3.4 : 2.6}>
          <title>{e.emotion} — v{e.valence.toFixed(2)}, a{e.arousal.toFixed(2)}{has ? ' (rendered)' : ''}</title>
        </circle>
      {/each}
    </svg>
    <span class="cap">{range.length} emotions in range · {rendered.size} rendered</span>
  </div>
{/if}

<style>
  .scatter { display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .plot { width: 150px; height: 150px; }
  .axis { stroke: var(--border); stroke-width: 1; }
  .alab { font-size: 7px; fill: var(--faint); }
  .dot { fill: var(--faint); stroke: none; }
  .dot.filled { fill: var(--accent); }
  .cap { font-size: 10px; color: var(--muted); }
  /* compact legend variant — sits inline in a header row: smaller plot, horizontal layout */
  .scatter.compact { flex-direction: row; gap: 8px; }
  .scatter.compact .plot { width: 84px; height: 84px; }
  .scatter.compact .cap { font-size: 9.5px; }
</style>

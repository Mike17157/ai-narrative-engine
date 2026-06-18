<script>
  // The Library table: shared, constant LoRAs (detail = always-on, theme =
  // picked, character = state-routing pool). Populated by Classify; rows are
  // mutated in place against the shared config.
  import Combobox from '$lib/components/Combobox.svelte';
  import ScrubInput from '$lib/components/ScrubInput.svelte';
  import { loraLib, loraItems } from '$lib/lora-library.svelte.js';

  function removeLib(i) { loraLib.cfg.library.splice(i, 1); }
</script>

<section class="card">
  <h3>Library <span class="sub">— shared, constant LoRAs (detail = always-on, theme = picked)</span></h3>
  <div class="libhead"><span>LoRA</span><span>Type</span><span>Weight</span><span>On</span><span>Note</span><span></span></div>
  {#each loraLib.cfg.library as l, i (i)}
    <div class="librow">
      <div class="lsel"><Combobox items={loraItems} value={l.name} placeholder="lora…" onpick={(v) => (l.name = v)} /></div>
      <select bind:value={l.type}><option value="detail">detail</option><option value="theme">theme</option><option value="character">character</option></select>
      <ScrubInput class="w" step={0.01} min={-2} max={2} bind:value={l.weight} title="drag ↕ or click to type" />
      <input class="ck" type="checkbox" bind:checked={l.enabled} title={l.type === 'detail' ? 'always-on' : 'available to pick'} />
      <input class="keys" bind:value={l.comment} placeholder="note (optional)" />
      <button class="ghost sm" onclick={() => removeLib(i)} aria-label="Remove">✕</button>
    </div>
  {/each}
  {#if !loraLib.cfg.library.length}<p class="hint" style="margin-top:8px">No classified LoRAs yet — use Classify above. (LoRAs aren't added by hand; they're discovered from disk and tagged here.)</p>{/if}
</section>

<style>
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 16px 18px; margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .sub { color: var(--faint); font-weight: 400; font-size: 12.5px; }
  .hint { font-size: 12.5px; color: var(--muted); }
  .libhead, .librow { display: grid; grid-template-columns: 1fr 90px 70px 36px 1.2fr 32px; gap: 8px; align-items: center; }
  .libhead { font-size: 10.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-bottom: 6px; }
  .librow { margin-bottom: 6px; }
  .lsel { min-width: 0; }
  .librow :global(.w) { width: 100%; padding: 6px 8px; }
  .keys { width: 100%; }
  .ck { width: auto; justify-self: center; }
</style>

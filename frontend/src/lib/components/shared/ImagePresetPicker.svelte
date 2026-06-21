<script>
  // Single-select image-preset picker (a render uses ONE LoRA-stack preset). Point-of-use:
  // the chat ⚙ sets the global default; the grid passes the choice per-render. Sibling of
  // LorebookPicker, but single-value. Self-loads /image-presets; emits the chosen id.
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';

  let { value = 'none', onchange, family = null } = $props();

  let presets = $state([]);
  onMount(async () => { try { presets = (await get('/image-presets')).presets || []; } catch {} });

  // Family-scoped (the 'none' floor always shows), enabled only, ordered.
  let shown = $derived(
    [...presets]
      .filter((p) => p.id === 'none' || (p.enabled !== false && (!family || p.family === family)))
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
  );
  const count = (p) => (p.loras?.length || 0);
</script>

<div class="ipp">
  {#each shown as p (p.id)}
    <button class="prow" class:on={p.id === value} onclick={() => onchange?.(p.id)} title={p.description || p.name}>
      <span class="pname">{p.name}</span>
      {#if p.id !== 'none'}<span class="pc">{count(p)} LoRA{count(p) === 1 ? '' : 's'}</span>{/if}
      {#if p.id === value}<span class="badge">active</span>{/if}
    </button>
  {/each}
  {#if !shown.length}<p class="empty">No image presets{family ? ` for ${family}` : ''}.</p>{/if}
</div>

<style>
  .ipp { display: flex; flex-wrap: wrap; gap: 6px; }
  .prow {
    display: inline-flex; align-items: center; gap: 7px; text-align: left;
    padding: 7px 11px; border-radius: 9px; background: var(--bg);
    border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; box-shadow: none;
  }
  .prow:hover { background: var(--elev); filter: none; }
  .prow.on { border-color: var(--accent); background: var(--elev-2); }
  .pname { font-size: 13px; font-weight: 600; }
  .pc { font-size: 10.5px; color: var(--faint); }
  .badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px;
    color: var(--accent); background: rgba(109,140,255,.14); border-radius: 999px; padding: 1px 6px; }
  .empty { font-size: 12.5px; color: var(--faint); margin: 2px 0; }
</style>

<script>
  import { onMount } from 'svelte';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import { get, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let { modelItems = [] } = $props();

  let enabled = $state(true);
  let model = $state('');
  let system = $state('');
  let saving = $state(false);
  let msg = $state(null);
  let loading = $state(true);
  let snap = $state(null);
  let saveTimer = null;

  const sig = () => JSON.stringify([enabled, model, system]);

  onMount(async () => {
    const data = await get('/promptgen');
    enabled = data.enabled ?? true;
    model = data.model || '';
    system = data.system || '';
    loading = false;
    snap = sig();
  });

  async function save() {
    saving = true; msg = null;
    const r = await post('/promptgen', { enabled, model, system });
    saving = false;
    msg = r.ok ? { ok: true, text: '✓ saved' } : { err: true, text: r.data?.error || 'save failed' };
    snap = sig();
  }

  $effect(() => {
    const cur = sig();
    if (loading || snap === null || cur === snap) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  });
</script>

<div class="cfg">
  <p class="intro">Controls how image prompts are generated from scene descriptions. The model converts
    prose scene text into Danbooru-style tags; the system prompt governs that conversion.</p>

  {#if !loading}
    <label class="toggle-row">
      <input type="checkbox" bind:checked={enabled} />
      <span>Enable tag-prompt generation</span>
      <span class="lo">— when off, scene descriptions are sent to the image model as-is</span>
    </label>

    <label>Model <span class="lo">— text model used for tag generation; blank = active chat model</span></label>
    <Combobox items={modelItems} bind:value={model} placeholder="pick a model…" />

    <label>System prompt <span class="lo">— instructs the model how to produce Danbooru tags</span></label>
    <textarea class="fld ta mono" use:autosize={system} bind:value={system}></textarea>

    <div class="prow">
      <span class="pm" class:ok={msg?.ok} class:err={msg?.err}>{saving ? 'Saving…' : (msg?.text || 'Auto-saves')}</span>
    </div>
  {:else}
    <p class="lo">Loading…</p>
  {/if}
</div>

<style>
  .cfg { max-width: 760px; }
  .intro { font-size: 12.5px; color: var(--muted); margin: 0 0 6px; }
  label { display: block; font-size: 11px; color: var(--muted); margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .toggle-row { display: flex; align-items: baseline; gap: 6px; cursor: pointer; }
  .toggle-row input { margin: 0; }
  .toggle-row span { font-size: 12px; color: var(--text); font-weight: 500; text-transform: none; letter-spacing: 0; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .ta { line-height: 1.5; font-family: inherit; min-height: 38px; overflow: hidden; resize: none; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .prow { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  .pm { font-size: 12px; color: var(--muted); } .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }
</style>

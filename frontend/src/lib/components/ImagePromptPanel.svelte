<script>
  import { onMount } from 'svelte';
  import { get, post } from '../api.js';
  import { refreshAll } from '../app.svelte.js';
  import Combobox from './Combobox.svelte';

  // The image-prompt model: turns a chat scene into a Stable-Diffusion prompt.
  // Its MODEL comes from its own (image_prompt) connection; its instructions live
  // in promptgen.json. Falls back to the chat model when no connection is set.
  let tm = $state({ models: [], active: null, connected: false });
  let cfg = $state({ enabled: true, system: '' });
  let loaded = $state(false);
  let modelMsg = $state(null);
  let cfgMsg = $state(null);
  let timer;

  let items = $derived(tm.models.map((m) => ({ value: m.id, label: m.name })));

  async function loadModels() { tm = await get('/text-models?kind=image_prompt'); }
  onMount(async () => {
    await loadModels();
    cfg = await get('/promptgen');
    loaded = true;
  });

  async function pickModel(model) {
    modelMsg = { text: 'Setting…' };
    const r = await post('/text/model', { kind: 'image_prompt', model });
    if (r.data?.ok) { modelMsg = { ok: true, text: '✓ ' + model }; await loadModels(); await refreshAll(); }
    else modelMsg = { err: true, text: r.data?.error || 'failed' };
  }

  // Auto-persist instructions (enabled + system), debounced.
  $effect(() => {
    const snap = JSON.stringify({ enabled: cfg.enabled, system: cfg.system });
    if (!loaded) return;
    clearTimeout(timer);
    cfgMsg = { text: 'saving…' };
    timer = setTimeout(async () => {
      const r = await post('/promptgen', JSON.parse(snap));
      cfgMsg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: 'save failed' };
    }, 500);
  });
</script>

<div class="hint">Turns the scene into a Stable-Diffusion image prompt — independent of the chat model.</div>

{#if tm.connected}
  <label>Model</label>
  <Combobox items={items} value={tm.active} placeholder="search models…" onpick={pickModel} />
  {#if modelMsg}<div class:ok={modelMsg.ok} class:err={modelMsg.err} style="font-size:13px;margin-top:8px">{modelMsg.text}</div>{/if}
{:else}
  <div class="hint warn">No language connection — set one up in <a href="/settings/models">Settings ▸ Generation</a>. Falls back to your chat model.</div>
{/if}

<label class="chk"><input type="checkbox" bind:checked={cfg.enabled} /> Enabled</label>

<label>Prompt instructions (system)</label>
<textarea bind:value={cfg.system} placeholder="How this model should write image prompts…"></textarea>
{#if cfgMsg}<div class:ok={cfgMsg.ok} class:err={cfgMsg.err} class="save">{cfgMsg.text}</div>{/if}

<style>
  .chk { display: flex; align-items: center; gap: 8px; color: var(--text); margin-top: 14px; }
  .chk input { width: auto; }
  .warn { color: #ffd479; }
  textarea { width: 100%; resize: none; flex: 1; min-height: 140px; margin-top: 8px; }
  .save { font-size: 12.5px; color: var(--muted); margin-top: 8px; }
</style>

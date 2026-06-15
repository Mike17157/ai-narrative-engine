<script>
  import { onMount } from 'svelte';
  import { get, post } from '../api.js';
  import { refreshAll } from '../app.svelte.js';
  import Combobox from './Combobox.svelte';

  let data = $state({ models: [], active: null, connected: false });
  let loading = $state(true);
  let msg = $state(null);

  // Global system prompt for the chat model (layered on top of the character's own).
  let sys = $state({ system: '' });
  let sysLoaded = $state(false);
  let sysMsg = $state(null);
  let sysTimer;

  let items = $derived(data.models.map((m) => ({ value: m.id, label: m.name })));

  async function load() { loading = true; data = await get('/text-models'); loading = false; }
  onMount(async () => {
    await load();
    sys = await get('/chatgen');
    sysLoaded = true;
  });

  // Auto-persist the system prompt (debounced), like Prompt Gen.
  $effect(() => {
    const snap = JSON.stringify($state.snapshot(sys));
    if (!sysLoaded) return;
    clearTimeout(sysTimer);
    sysMsg = { text: 'saving…' };
    sysTimer = setTimeout(async () => {
      const r = await post('/chatgen', JSON.parse(snap));
      sysMsg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: 'save failed' };
    }, 500);
  });

  async function pick(model) {
    msg = { text: 'Setting…' };
    const r = await post('/text/model', { model });
    if (r.data?.ok) { msg = { ok: true, text: '✓ ' + model }; await load(); await refreshAll(); }
    else msg = { err: true, text: r.data?.error || 'failed' };
  }
</script>

{#if loading}
  <div class="hint">Loading models…</div>
{:else if !data.connected}
  <div class="hint">No OpenRouter connection yet — set it up in the <b>Connection</b> tab.{#if data.error} <span class="err">({data.error})</span>{/if}</div>
{:else}
  <div class="hint">The model used for chat. {data.models.length} models available from your connection.</div>
  <label>Chat model</label>
  <Combobox items={items} value={data.active} placeholder="search models…" onpick={pick} />
  {#if msg}<div class:ok={msg.ok} class:err={msg.err} style="font-size:13px;margin-top:8px">{msg.text}</div>{/if}
{/if}

<label style="margin-top:16px">System prompt</label>
<div class="hint">Standing instructions for the chat model — applied on top of the selected character's own system. Leave blank to let the character govern entirely. Saves automatically.</div>
<textarea bind:value={sys.system} placeholder="e.g. Always write in third person, present tense. Keep replies under 200 words…"></textarea>
{#if sysMsg}<div class:ok={sysMsg.ok} class:err={sysMsg.err} class="save">{sysMsg.text}</div>{/if}

<style>
  .err { color: var(--bad); }
  textarea { width: 100%; resize: none; flex: 1; min-height: 140px; margin-top: 8px; }
  .save { font-size: 12.5px; color: var(--muted); margin-top: 8px; }
</style>

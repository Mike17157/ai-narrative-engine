<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app, refreshAll, refreshModels, setActiveImage } from '$lib/app.svelte.js';
  import Combobox from '$lib/components/Combobox.svelte';

  // ── Language API connection ────────────────────────────────────────────────
  let providers = $state([]);
  let provider = $state('');
  let baseUrl = $state('');
  let apiKey = $state('');
  let connecting = $state(false);
  let connStatus = $state(null);
  let touchedProvider = $state(false);

  let providerItems = $derived(providers.map(p => ({ value: p.id, label: p.label })));
  let curProvider = $derived(providers.find(p => p.id === provider));
  let baseEditable = $derived(curProvider?.base_url_editable ?? false);
  let needsKey = $derived(curProvider?.needs_key ?? true);
  let canConnect = $derived(!connecting && (!needsKey || apiKey));

  $effect(() => { if (curProvider && touchedProvider) baseUrl = curProvider.default_base_url || ''; });

  // ── Chat model + system prompt ─────────────────────────────────────────────
  let modelItems = $state([]);
  let chatActive = $state('');
  let chatMsg = $state(null);
  let chatSys = $state({ system: '' });
  let chatSysLoaded = $state(false);
  let chatSysMsg = $state(null);
  let chatSysTimer;

  // ── Prompt generator ───────────────────────────────────────────────────────
  let promptActive = $state('');
  let promptMsg = $state(null);
  let promptCfg = $state({ enabled: true, system: '' });
  let promptCfgLoaded = $state(false);
  let promptCfgMsg = $state(null);
  let promptCfgTimer;

  // ── Image workflow ─────────────────────────────────────────────────────────
  const famCap = (f) => f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other';
  let imageItems = $derived((app.models.image || []).map(m => ({ value: m.key, label: m.key, group: famCap(m.family) })));

  onMount(async () => {
    providers = await get('/providers?kind=text');

    const [chat, prompt, sys, pcfg] = await Promise.all([
      get('/text-models'),
      get('/text-models?kind=image_prompt'),
      get('/chatgen'),
      get('/promptgen'),
    ]);

    chatSys = sys; chatSysLoaded = true;
    promptCfg = pcfg; promptCfgLoaded = true;
    promptActive = prompt.active || '';

    if (chat.connected) {
      modelItems = chat.models.map(m => ({ value: m.id, label: m.name }));
      chatActive = chat.active || '';
      connStatus = { ok: true, text: `Connected — ${chat.models.length} models` };
      const conns = await get('/connections');
      const c = conns.connections.find(c => c.kind === 'text' && c.id === chat.connection);
      if (c) { provider = c.provider || ''; baseUrl = c.base_url || ''; }
    } else if (providers[0]) {
      provider = providers[0].id;
      baseUrl = providers[0].default_base_url || '';
    }

    if (!app.models.image?.length) refreshModels();
  });

  function setProvider(v) { touchedProvider = true; provider = v; }

  async function connect() {
    connecting = true;
    connStatus = { ok: false, text: 'Connecting…' };
    const r = await post('/connections/test', { kind: 'text', provider, api_key: apiKey, base_url: baseUrl });
    connecting = false;
    if (!r.ok) { connStatus = { ok: false, text: '✗ ' + (r.data?.error || 'failed') }; return; }
    // Save as both text and image_prompt so the prompt generator can use the same connection.
    const payload = { id: provider, provider, api_key: apiKey, base_url: baseUrl, model: '' };
    await Promise.all([
      post('/connections', { ...payload, kind: 'text' }),
      post('/connections', { ...payload, kind: 'image_prompt' }),
    ]);
    apiKey = '';
    modelItems = r.data.models.map(m => ({ value: m.id, label: m.name }));
    connStatus = { ok: true, text: `Connected — ${r.data.count} models` };
    await refreshAll();
  }

  async function pickChatModel(model) {
    chatMsg = { text: '…' };
    const r = await post('/text/model', { model });
    if (r.data?.ok) { chatActive = model; chatMsg = { ok: true, text: '✓' }; await refreshAll(); }
    else chatMsg = { err: true, text: r.data?.error || 'failed' };
  }

  async function pickPromptModel(model) {
    promptMsg = { text: '…' };
    const r = await post('/text/model', { kind: 'image_prompt', model });
    if (r.data?.ok) { promptActive = model; promptMsg = { ok: true, text: '✓' }; }
    else promptMsg = { err: true, text: r.data?.error || 'failed' };
  }

  $effect(() => {
    const snap = JSON.stringify($state.snapshot(chatSys));
    if (!chatSysLoaded) return;
    clearTimeout(chatSysTimer);
    chatSysMsg = { text: 'saving…' };
    chatSysTimer = setTimeout(async () => {
      const r = await post('/chatgen', JSON.parse(snap));
      chatSysMsg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: 'failed' };
    }, 500);
  });

  $effect(() => {
    const snap = JSON.stringify({ enabled: promptCfg.enabled, system: promptCfg.system });
    if (!promptCfgLoaded) return;
    clearTimeout(promptCfgTimer);
    promptCfgMsg = { text: 'saving…' };
    promptCfgTimer = setTimeout(async () => {
      const r = await post('/promptgen', JSON.parse(snap));
      promptCfgMsg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: 'failed' };
    }, 500);
  });
</script>

<div class="pane">

  <!-- ── Language API ─────────────────────────────────────────────────────── -->
  <div class="section">
    <div class="section-head">
      <span class="section-title">Language API</span>
      {#if connStatus}
        <span class="conn-pill" class:ok={connStatus.ok} class:err={!connStatus.ok}>{connStatus.text}</span>
      {/if}
    </div>
    <div class="api-form">
      <div class="fld">
        <label>Provider</label>
        <Combobox items={providerItems} value={provider} placeholder="Provider…" onpick={setProvider} />
      </div>
      {#if baseEditable}
        <div class="fld">
          <label>Base URL</label>
          <input bind:value={baseUrl} placeholder="http://localhost:11434" />
        </div>
      {/if}
      {#if needsKey}
        <div class="fld">
          <label>API key</label>
          <input type="password" bind:value={apiKey} placeholder="sk-…" autocomplete="off" />
        </div>
      {/if}
      <div class="fld-btn">
        <button onclick={connect} disabled={!canConnect}>{connecting ? 'Connecting…' : 'Connect'}</button>
      </div>
    </div>
  </div>

  <hr />

  <!-- ── Chat + Prompt gen ────────────────────────────────────────────────── -->
  <div class="two-col">
    <div class="section">
      <div class="section-head">
        <span class="section-title">Chat model</span>
        {#if chatMsg}<span class="smsg" class:ok={chatMsg.ok} class:err={chatMsg.err}>{chatMsg.text}</span>{/if}
      </div>
      {#if modelItems.length}
        <Combobox items={modelItems} value={chatActive} placeholder="select model…" onpick={pickChatModel} />
      {:else}
        <p class="hint dim">Connect a provider above to pick a model.</p>
      {/if}
      <div class="sub-head">
        System prompt
        {#if chatSysMsg}<span class="smsg" class:ok={chatSysMsg.ok} class:err={chatSysMsg.err}>{chatSysMsg.text}</span>{/if}
      </div>
      <textarea bind:value={chatSys.system} placeholder="Standing instructions layered on top of the character's own…"></textarea>
    </div>

    <div class="section">
      <div class="section-head">
        <span class="section-title">Prompt generator</span>
        <label class="toggle">
          <input type="checkbox" bind:checked={promptCfg.enabled} />
          <span>Enabled</span>
        </label>
        {#if promptMsg}<span class="smsg" class:ok={promptMsg.ok} class:err={promptMsg.err}>{promptMsg.text}</span>{/if}
        {#if promptCfgMsg}<span class="smsg" class:ok={promptCfgMsg.ok} class:err={promptCfgMsg.err}>{promptCfgMsg.text}</span>{/if}
      </div>
      {#if modelItems.length}
        <Combobox items={modelItems} value={promptActive} placeholder="model (defaults to chat model)…" onpick={pickPromptModel} />
      {:else}
        <p class="hint dim">Connect a provider above to pick a model.</p>
      {/if}
      <div class="sub-head">Instructions</div>
      <textarea bind:value={promptCfg.system} placeholder="How to write Stable Diffusion tag prompts — booru tags, comma-separated, no prose…"></textarea>
    </div>
  </div>

  <hr />

  <!-- ── Image workflow ───────────────────────────────────────────────────── -->
  <div class="section">
    <div class="section-head">
      <span class="section-title">Image workflow</span>
    </div>
    {#if imageItems.length}
      <Combobox items={imageItems} value={app.activeImage} placeholder="ComfyUI workflow…" onpick={setActiveImage} />
    {:else}
      <p class="hint dim">No workflows available — connect ComfyUI in <a href="/settings/comfyui">ComfyUI settings</a>.</p>
    {/if}
  </div>

</div>

<style>
  .pane {
    flex: 1; min-height: 0; overflow: auto;
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg);
    margin: 18px 20px 18px 4px;
    display: flex; flex-direction: column;
  }

  .section { padding: 18px 22px; }

  hr { border: none; border-top: 1px solid var(--border-soft); margin: 0; flex: none; }

  /* Two-column panel: chat + prompt gen side by side */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; flex: 1; min-height: 0; }
  .two-col .section:last-child { border-left: 1px solid var(--border-soft); }
  .two-col .section { display: flex; flex-direction: column; }
  .two-col textarea { flex: 1; min-height: 80px; }

  /* Section heading */
  .section-head {
    display: flex; align-items: center; gap: 10px; margin-bottom: 12px; flex-wrap: wrap;
  }
  .section-title { font-size: 13px; font-weight: 660; color: var(--text); }

  /* Sub-heading inside a section */
  .sub-head {
    font-size: 12px; color: var(--muted); margin: 14px 0 6px;
    display: flex; align-items: center; gap: 8px;
  }

  /* Connection form */
  .api-form { display: flex; flex-direction: column; gap: 10px; max-width: 460px; }
  .fld { display: flex; flex-direction: column; gap: 4px; }
  .fld-btn { margin-top: 2px; }

  label { font-size: 12px; color: var(--muted); text-transform: none; letter-spacing: 0; display: block; }
  input:not([type='checkbox']) { width: 100%; }

  /* Connection status pill */
  .conn-pill {
    font-size: 12px; border-radius: 999px; padding: 2px 10px; margin-left: auto;
    font-weight: 500;
  }
  .conn-pill.ok { background: rgba(87, 217, 163, .12); color: var(--good); }
  .conn-pill.err { background: rgba(255, 100, 100, .12); color: var(--bad); }

  /* Inline save/status message */
  .smsg { font-size: 12px; }
  .smsg.ok { color: var(--good); }
  .smsg.err { color: var(--bad); }

  /* Prompt / sys prompt textarea */
  textarea { width: 100%; resize: vertical; min-height: 90px; font-size: 13px; }

  .hint { font-size: 12.5px; color: var(--muted); line-height: 1.5; margin: 0 0 8px; }
  .hint.dim { opacity: .6; }
  .hint a { color: var(--accent); }

  /* Enabled toggle next to heading */
  .toggle {
    display: flex; align-items: center; gap: 5px; cursor: pointer;
    font-size: 12.5px; color: var(--muted); font-weight: 400;
  }
  .toggle input { width: auto; margin: 0; }
</style>

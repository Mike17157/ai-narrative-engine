<script>
  import { onMount } from 'svelte';
  import { get, post, del } from '$lib/api.js';
  import { app, refreshAll } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';

  // kind: "text" (TextGen) or "image" (Images). Each manages its own connections.
  let { kind = 'text' } = $props();

  let providers = $state([]);
  let provider = $state('');
  let baseUrl = $state('');
  let apiKey = $state('');
  let profileName = $state('');
  let status = $state(null);
  let modelItems = $state([]);
  let model = $state('');
  let connecting = $state(false);
  let touchedProvider = $state(false);

  let providerItems = $derived(providers.map((p) => ({ value: p.id, label: p.label })));
  let curProvider = $derived(providers.find((p) => p.id === provider));
  let baseEditable = $derived(curProvider ? curProvider.base_url_editable : true);
  let needsKey = $derived(curProvider ? curProvider.needs_key : true);

  let myConns = $derived(app.conns.connections.filter((c) => c.kind === kind));
  let activeId = $derived(app.conns.active?.[kind] || '');
  let profileItems = $derived(myConns.map((c) => ({ value: c.id, label: c.model ? `${c.id} — ${c.model}` : c.id })));

  $effect(() => { if (curProvider && touchedProvider) baseUrl = curProvider.default_base_url; });

  onMount(async () => {
    providers = await get(`/providers?kind=${kind}`);
    // Auto-connect the active profile so it's ready without a manual Connect.
    const activeId = app.conns.active?.[kind];
    if (activeId) {
      await selectProfile(activeId);
    } else if (providers[0]) {
      provider = providers[0].id; baseUrl = providers[0].default_base_url; profileName = providers[0].id;
    }
  });

  function setProvider(v) { touchedProvider = true; provider = v; profileName = v; }

  async function connectNew() {
    connecting = true; status = { ok: false, text: 'Connecting…' };
    const r = await post('/connections/test', { kind, provider, api_key: apiKey, base_url: baseUrl });
    connecting = false;
    if (!r.ok) { status = { ok: false, text: '✗ ' + (r.data?.error || 'failed') }; return; }
    modelItems = r.data.models.map((m) => ({ value: m.id, label: m.name }));
    status = { ok: true, text: `✓ Connected — ${r.data.count} ${kind === 'image' ? 'image models' : 'models'}` };
    connSnap = connSig();
  }

  let connSnap = $state(null);   // auto-save baseline
  let connTimer = null;
  const connSig = () => JSON.stringify([provider, baseUrl, profileName, model]);

  async function save() {
    await post('/connections', {
      kind, id: (profileName || provider).trim(), provider, api_key: apiKey, base_url: baseUrl, model
    });
    apiKey = '';              // key is stored server-side; empty resends preserve it
    connSnap = connSig();
    await refreshAll();
  }

  // Auto-save the profile once it's valid (a model + name). The API key is captured on
  // Connect; empty resends preserve the stored key, so model/name edits save safely.
  $effect(() => {
    const cur = connSig();
    if (!model || !profileName || connSnap === null || cur === connSnap) return;
    clearTimeout(connTimer);
    connTimer = setTimeout(save, 800);
  });

  // Select a saved profile -> activate + connect using its stored key.
  async function selectProfile(id) {
    if (!id) return;
    await post(`/connections/${id}/activate`);
    status = { ok: false, text: 'Connecting…' };
    const r = await post(`/connections/${id}/test`);
    await refreshAll();
    if (!r.ok) { status = { ok: false, text: '✗ ' + (r.data?.error || 'failed') }; return; }
    touchedProvider = false;
    provider = r.data.provider; baseUrl = r.data.base_url || baseUrl;
    profileName = id;
    modelItems = r.data.models.map((m) => ({ value: m.id, label: m.name }));
    model = r.data.model || '';
    status = { ok: true, text: `✓ Connected as “${id}” — ${r.data.count} models` };
    connSnap = connSig();
  }

  async function deleteActive() {
    if (!activeId) return;
    if (!await askConfirm({ title: `Delete the “${activeId}” profile?`,
        message: 'This removes its saved API key.', confirmLabel: 'Delete', danger: true })) return;
    await del(`/connections/${activeId}`);
    status = null; model = ''; modelItems = [];
    await refreshAll();
  }
</script>

<label>Profile</label>
<div class="row">
  <div style="flex:1">
    <Combobox items={profileItems} value={activeId} placeholder={myConns.length ? 'Select a profile…' : 'no profiles yet'} onpick={selectProfile} />
  </div>
  <button class="ghost sm" onclick={deleteActive} disabled={!activeId} title="Delete profile">Delete</button>
</div>

<div class="divider"></div>
<div class="addhdr">{myConns.length ? 'Add / update profile' : 'New connection'}</div>

<label>Provider</label>
<Combobox items={providerItems} value={provider} placeholder="Provider…" onpick={setProvider} />

{#if baseEditable}
  <label>{kind === 'image' ? 'ComfyUI URL' : 'Base URL'}</label>
  <input bind:value={baseUrl} placeholder={kind === 'image' ? 'http://127.0.0.1:8188 or a RunPod URL' : ''} />
{/if}

{#if needsKey}
  <label>API key</label>
  <input type="password" bind:value={apiKey} placeholder="paste your key" />
{/if}

<label>Profile name</label>
<input bind:value={profileName} placeholder="e.g. {kind === 'image' ? 'local-comfy' : 'openrouter'}" />

<div class="row" style="margin-top:12px; align-items:center; gap:10px">
  <button onclick={connectNew} disabled={connecting || (needsKey && !apiKey)}>Connect</button>
  <span class="hint">{model && profileName ? 'profile auto-saves' : 'connect, then pick a model'}</span>
</div>

{#if status}<div class="status" class:ok={status.ok} class:err={!status.ok}>{status.text}</div>{/if}

<label>{kind === 'image' ? 'Workflow' : 'Model'}</label>
{#if modelItems.length}
  <Combobox items={modelItems} bind:value={model} placeholder="Search models…" />
{:else}
  <div class="hint">connect to list models</div>
{/if}

<style>
  .addhdr { font-size: 12px; font-weight: 700; letter-spacing: .3px; color: var(--muted); text-transform: uppercase; }
  .status { font-size: 13px; margin: 11px 0; min-height: 18px; }
</style>

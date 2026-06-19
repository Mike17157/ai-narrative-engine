<script>
  // Settings ▸ Models. The single authority for "which model do we use":
  //   · the active chat model + its global system prompt (reads the active text connection)
  //   · the active image workflow (reads the active image connection)
  // Each section links to Connections (credentials) when no connection is set.
  // Per-role image overrides for story generation live in Generation, not here.
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app, refreshAll, setActiveImage } from '$lib/app.svelte.js';
  import Combobox from '$lib/components/Combobox.svelte';

  // ── Chat model ──
  let chat = $state({ models: [], active: null, connected: false });
  let chatMsg = $state(null);

  // ── Chat system prompt (chatgen.json) ──
  let sys = $state({ system: '' });
  let sysLoaded = $state(false);
  let sysMsg = $state(null);
  let sysTimer;

  // ── Image workflow ──
  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other');
  let imageItems = $derived((app.models.image || []).map((m) => ({ value: m.key, label: m.key, group: famCap(m.family) })));
  let imageActive = $derived(app.conns.active?.image);

  onMount(async () => {
    await refreshAll();
    try { chat = await get('/text-models'); } catch { /* no text connection */ }
    try { sys = await get('/chatgen'); } catch { /* no chatgen config */ }
    sysLoaded = true;
  });

  let chatItems = $derived((chat.models || []).map((m) => ({ value: m.id, label: m.name })));

  async function pickChatModel(model) {
    chatMsg = { text: 'Setting…' };
    const r = await post('/text/model', { model });
    if (r.data?.ok) { chat.active = model; chatMsg = { ok: true, text: '✓ ' + model }; await refreshAll(); }
    else chatMsg = { err: true, text: r.data?.error || 'failed' };
  }

  // Debounced autosave of the global chat system prompt.
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
</script>

<div class="screen">

  <!-- ── Chat model ────────────────────────────────────────────────────────── -->
  <section class="card">
    <div class="card-head">
      <h3>Chat model</h3>
      {#if chatMsg}<span class="status-lbl" class:ok={chatMsg.ok} class:err={chatMsg.err}>{chatMsg.text}</span>{/if}
    </div>
    {#if chat.connected}
      <p class="hint">The model used for chat, story generation, and all text work. {(chat.models || []).length} available from your connection.</p>
      <label>Active model</label>
      <Combobox items={chatItems} value={chat.active} placeholder="search models…" onpick={pickChatModel} />
    {:else}
      <p class="hint">No language connection yet — <a href="/settings/connections">set one up in Connections</a>.</p>
    {/if}

    <label style="margin-top:16px">System prompt</label>
    <p class="hint">Standing instructions for the chat model — applied on top of the selected character's own. Leave blank to let the character govern entirely. Saves automatically.</p>
    <textarea bind:value={sys.system} disabled={!sysLoaded}
      placeholder="e.g. Always write in third person, present tense. Keep replies under 200 words…"></textarea>
    {#if sysMsg}<div class="status-lbl" class:ok={sysMsg.ok} class:err={sysMsg.err}>{sysMsg.text}</div>{/if}
  </section>

  <!-- ── Image workflow ────────────────────────────────────────────────────── -->
  <section class="card">
    <div class="card-head">
      <h3>Image workflow</h3>
    </div>
    {#if imageActive}
      <p class="hint">The image workflow used to render pictures in chat &amp; stories. {imageItems.length} available — edit individual workflows in the <a href="/images/graph">Images ▸ Graph</a> section.</p>
      <label>Active workflow</label>
      {#if imageItems.length}
        <Combobox items={imageItems} value={app.activeImage} placeholder="workflow…" onpick={(v) => setActiveImage(v)} />
      {:else}
        <p class="hint">Connected, but no workflows found. Manage the model tree in <a href="/images/models">Images ▸ Models</a>.</p>
      {/if}
    {:else}
      <p class="hint">No image connection yet — <a href="/settings/connections">connect ComfyUI in Connections</a>.</p>
    {/if}
  </section>

</div>

<style>
  .screen { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 16px; padding: 18px 20px 18px 4px; }
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 20px 22px; display: flex; flex-direction: column; }
  .card-head { display: flex; align-items: center; gap: 9px; padding-bottom: 14px; border-bottom: 1px solid var(--border-soft); margin-bottom: 14px; }
  h3 { margin: 0; font-size: 14px; font-weight: 660; color: var(--text); }
  .status-lbl { font-size: 12px; color: var(--muted); margin-left: auto; }
  .status-lbl.ok { color: var(--good); } .status-lbl.err { color: var(--bad); }
  label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); margin: 0 0 6px; }
  textarea { width: 100%; resize: vertical; min-height: 130px; font-size: 13px; margin-top: 6px; }
  .hint { font-size: 12.5px; color: var(--muted); line-height: 1.55; margin: 0 0 12px; }
  .hint a { color: var(--accent); }
</style>

<script>
  // The config-agnostic generalization of StoryConsole: run ANY chat config as an
  // interactive flow over ANY JSON artifact. Chat goes through the generic /api/chat
  // (the config supplies model + system + data lorebooks + the artifact in context);
  // the artifact renders as a GRAPH when it's node-shaped, else as an editable JSON
  // document; attached FUNCTION books let the model edit the artifact via /graph-ops.
  import LlmConsole from '$lib/components/story/LlmConsole.svelte';
  import StoryGraphCanvas from '$lib/components/graph/StoryGraphCanvas.svelte';
  import JsonArtifact from '$lib/components/shared/JsonArtifact.svelte';
  import { app } from '$lib/app.svelte.js';
  import { post } from '$lib/api.js';

  let {
    title = 'Flow',
    subtitle = '',
    character = '',
    config = '',                 // chat-config id (drives model/system/lorebooks + graph-ops)
    artifactLabel = 'Document',
    initialArtifact = null,
    lorebooks = $bindable([]),   // data + function books for this flow
    onArtifactChange = null,     // (artifact) => void
    placeholder = 'Message…  (Enter to send)',
    actions: hostActions = null,
  } = $props();

  let messages = $state([]);
  let busy = $state(false);
  let err = $state(null);
  let model = $state('');
  let workingArtifact = $state(initialArtifact);
  let artSeq = $state(0);
  let isGraph = $derived(Array.isArray(workingArtifact?.nodes));

  function setArtifact(a, bump) {
    workingArtifact = a;
    if (bump) artSeq++;
    onArtifactChange?.(a);
  }

  let abortCtl = null;
  async function callChat(history) {
    busy = true; err = null;
    messages = [...history, { role: 'assistant', content: '' }];
    const idx = messages.length - 1;
    const persona = app.personas.find((p) => p.id === app.activePersona);
    abortCtl = new AbortController();
    try {
      const res = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: abortCtl.signal,
        body: JSON.stringify({
          history, character: character || undefined, config: config || undefined,
          persona: persona ? { name: persona.name, description: persona.description || '' } : undefined,
          lorebooks, artifact: workingArtifact || undefined, artifact_label: artifactLabel,
        }),
      });
      if (!res.ok || !res.body) { err = (await res.json().catch(() => ({})))?.error || `HTTP ${res.status}`; messages = history; return; }
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      for (;;) {
        const { done, value } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 2);
          if (!line.startsWith('data: ')) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === 'delta') { messages[idx].content += ev.text; }
          else if (ev.type === 'error') err = ev.error;
        }
      }
    } catch (e) { if (e.name !== 'AbortError') err = e.message || 'chat failed'; }
    finally {
      busy = false; abortCtl = null;
      if (!messages[idx]?.content && !err) messages = messages.slice(0, idx);
      if (autoFns) void runOps();
    }
  }
  function send(text) { callChat([...messages.filter((m) => m.content), { role: 'user', content: text }]); }

  // Data-driven functions over the artifact (config-driven provider).
  let fnBusy = $state(false);
  let fnMsg = $state(null);
  let autoFns = $state(false);
  async function runOps() {
    if (fnBusy || busy) return;
    fnBusy = true; fnMsg = null;
    const r = await post('/stories/graph-ops', {
      config, character, graph: workingArtifact || {}, lorebooks, artifact_label: artifactLabel,
      messages: messages.filter((m) => m.content),
    });
    fnBusy = false;
    if (r.data?.ok) {
      if (r.data.graph) setArtifact(r.data.graph, true);
      const ok = (r.data.applied || []).filter((a) => a.ok);
      fnMsg = ok.length ? `✓ ${ok.map((a) => a.fn).join(', ')}`
        : ((r.data.offered || []).length ? 'no changes called for' : 'attach a function book first');
    } else fnMsg = r.data?.error || 'failed';
  }
</script>

<LlmConsole {title} {subtitle} {messages} {busy} {err} bind:model bind:lorebooks
  models={[]} onSend={send} {placeholder}>
  {#snippet right()}
    {#if isGraph}
      <StoryGraphCanvas graph={workingArtifact} seq={artSeq} {busy} onChange={(g) => setArtifact(g, false)} />
    {:else}
      <JsonArtifact value={workingArtifact} label={artifactLabel} onChange={(v) => setArtifact(v, false)} />
    {/if}
  {/snippet}

  {#snippet actions()}
    <button class="fnbtn" onclick={runOps} disabled={busy || fnBusy}
      title="Apply functions from attached function books — triggers offer, the model decides">
      {fnBusy ? '⚙ working…' : '⚙ Functions'}
    </button>
    <label class="fnauto" title="Run functions automatically after every turn">
      <input type="checkbox" bind:checked={autoFns} /> auto
    </label>
    {#if fnMsg}<span class="fnmsg">{fnMsg}</span>{/if}
    {#if hostActions}{@render hostActions({ artifact: workingArtifact, busy })}{/if}
  {/snippet}
</LlmConsole>

<style>
  .fnbtn { font-size: 12px; padding: 7px 12px; border-radius: 8px; cursor: pointer;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .fnbtn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .fnbtn:disabled { opacity: .5; cursor: not-allowed; }
  .fnauto { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--muted); cursor: pointer; }
  .fnauto input { width: 13px; height: 13px; }
  .fnmsg { font-size: 11.5px; color: var(--muted); align-self: center; }
</style>

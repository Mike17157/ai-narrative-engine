<script>
  // The shared story-iteration surface: a two-pane LLM console whose canvas is the
  // editable story graph. The consultant (DeepSeek V3, modern-craft lorebook) builds
  // and revises the graph as you converse; you drag to branch/merge beats and edit
  // each beat's start / what-happens / end. Used by setup, the storyboard step, and
  // the saved story — each supplies its own action buttons via the `actions` snippet.
  import { onMount } from 'svelte';
  import LlmConsole from '$lib/components/story/LlmConsole.svelte';
  import StoryGraphCanvas from '$lib/components/graph/StoryGraphCanvas.svelte';
  import { stories, loadModels } from '$lib/stories.svelte.js';
  import { get, put, post } from '$lib/api.js';
  import { consumeSse } from '$lib/sse.js';

  let {
    character = '',
    charName  = '',
    title = 'Story Workshop',
    subtitle = '',
    initialGraph = null,    // seed graph (storyboard / saved-story); null in setup
    autostart = true,       // auto-send the opening turn on mount
    openingMessage = '',    // first user turn (defaults to the endpoint's character read)
    sessionId = '',         // server-side checkpoint id; '' = no persistence
    storyKey = '',          // the SAVED story key (if any) — lets story tools persist title/cover
    onGraphChange = null,   // (graph) => void — fires on model AND user edits
    onBoard = null,         // (board) => void — fires when an agent runs the storyboarder stage tool
    onImage = null,         // (dataUri, prompt) => void — fires when an agent renders via generate_image
    // The host's action buttons. Aliased to `hostActions` so it doesn't collide with the
    // `{#snippet actions()}` we pass down to LlmConsole — that shadowing made
    // `{@render actions(...)}` recurse into the local snippet (invalid_snippet_arguments
    // → stack overflow), which is what crashed the workshop.
    actions: hostActions = null,   // snippet(ctx) — ctx = { graph, busy, hasExchange, lastAssistant, ready }
  } = $props();

  const SPINE_MARKER = '<<<SPINE>>>';

  let messages = $state([]);
  let busy = $state(false);
  let err = $state(null);
  let workingGraph = $state(initialGraph || null);
  let graphSeq = $state(initialGraph ? 1 : 0);
  let retrievedLore = $state([]);
  let usage = $state(null);
  let ready = $state(false);     // "draftable shape" signal from the turn graph's decision

  let model = $state('');
  let lorebooks = $state([character, '_global'].filter(Boolean));
  let models = $derived(stories.textModels.map((m) => ({ value: m.id, label: m.name || m.id })));

  let hasExchange = $derived(messages.some((m) => m.role === 'user'));
  let lastAssistant = $derived([...messages].reverse().find((m) => m.role === 'assistant')?.content?.trim() || '');

  onMount(() => { init(); });

  async function init() {
    if (!stories.textModels.length) loadModels?.();
    // Resume from the server-side checkpoint if one exists.
    if (sessionId) {
      try {
        const s = await get(`/stories/session/${sessionId}`);
        if (s?.messages?.length) {
          messages = s.messages;
          if (s.graph) { workingGraph = s.graph; graphSeq++; }
          onGraphChange?.(workingGraph);
          return;   // resumed — don't auto-open
        }
      } catch { /* no session yet */ }
    }
    if (autostart) callWorkshop(openingMessage ? [{ role: 'user', content: openingMessage }] : []);
  }

  function setGraph(g, bump) {
    workingGraph = g;
    if (bump) graphSeq++;
    onGraphChange?.(g);
  }

  // ── Server-side checkpoint: persist conversation + graph (debounced on edits) ──
  function saveSession() {
    if (!sessionId) return;
    put(`/stories/session/${sessionId}`, { character, messages, graph: workingGraph }).catch(() => {});
  }
  let _saveTimer;
  $effect(() => {
    workingGraph;   // track graph edits (model- or user-driven)
    if (!sessionId) return;
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(saveSession, 1000);
  });

  // The agent may end a reply with a stage-tool sentinel like `[[run: storyboard]]` or, for a
  // tool that takes input, `[[run: generate_image | a girl in a red dress]]` (injected tool
  // protocol). Strip it from what the writer sees — runStageTools() acts on it after the turn.
  const RUN_SENTINEL = () => /\[\[run:\s*([a-z_]+)\s*(?:\|\s*([^\]]*?))?\s*\]\]/gi;
  function visibleProse(raw) {
    return raw.split(SPINE_MARKER)[0]
      .replace(RUN_SENTINEL(), '')
      .replace(/\s*<{1,3}S?P?I?N?E?>{0,3}\s*$/i, '')
      .trimEnd();
  }

  async function callWorkshop(msgs) {
    busy = true;
    err = null;
    const withPlaceholder = [...msgs, { role: 'assistant', content: '' }];
    const idx = withPlaceholder.length - 1;
    messages = withPlaceholder;
    let raw = '';

    try {
      const res = await fetch('/api/stories/workshop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character,
          messages: msgs,
          graph: workingGraph || undefined,
          model: model || undefined,
          lorebooks,
          session_id: sessionId || undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        err = data?.error || `HTTP ${res.status}`;
        messages = withPlaceholder.slice(0, idx);
        return;
      }

      await consumeSse(res, (ev) => {
        if (ev.type === 'delta') {
          raw += ev.text;
          const upd = [...messages];
          upd[idx] = { ...upd[idx], content: visibleProse(raw) };
          messages = upd;
        } else if (ev.type === 'spine') {
          if (ev.spine && typeof ev.spine === 'object') setGraph(ev.spine, true);
        } else if (ev.type === 'usage') {
          usage = ev;
        } else if (ev.type === 'ready') {
          ready = !!ev.ready;
        } else if (ev.type === 'lore') {
          retrievedLore = ev.entries || [];
        } else if (ev.type === 'error') {
          err = ev.error;
        }
      });
    } catch (e) {
      err = String(e);
      messages = messages.slice(0, idx);
    } finally {
      busy = false;
      saveSession();   // checkpoint the turn (conversation + graph) server-side
      if (autoFns) void runGraphOps();   // apply tools (graph edits, renders…) as part of the turn
    }
  }

  function send(text) {
    callWorkshop([...messages.filter((m) => m.content || m.role === 'user'), { role: 'user', content: text }]);
  }

  // Data-driven graph FUNCTIONS: attached function books expose ops; the transcript's
  // trigger terms offer them and the model decides which to call (graph_ops). Applied
  // server-side to the working graph; we swap the canvas to the result.
  let fnBusy = $state(false);
  let fnMsg = $state(null);
  let autoFns = $state(false);   // run graph functions automatically after each turn
  async function runGraphOps() {
    if (fnBusy || busy) return;
    fnBusy = true; fnMsg = null;
    const r = await post('/stories/graph-ops', {
      character, graph: workingGraph || {}, lorebooks, artifact_label: 'DEVELOPMENT GRAPH',
      story: storyKey || undefined,   // lets story tools (title/cover) persist to the saved story
      messages: messages.filter((m) => m.content),
    });
    fnBusy = false;
    if (r.data?.ok) {
      if (r.data.graph) setGraph(r.data.graph, true);
      // Action tools (image render, storyboard…) come back as artifacts — show them.
      for (const a of (r.data.artifacts || [])) {
        if (a.image) { messages = [...messages, { role: 'assistant', content: '', image: a.image, imageAlt: a.prompt }]; onImage?.(a.image, a.prompt); }
        else if (a.board) onBoard?.(a.board);
        else if (a.graph) setGraph(a.graph, true);
      }
      const ok = (r.data.applied || []).filter((a) => a.ok);
      fnMsg = ok.length ? `✓ ${ok.map((a) => a.fn).join(', ')}`
        : ((r.data.offered || []).length ? 'no changes called for' : 'attach a function book first');
    } else fnMsg = r.data?.error || 'failed';
  }
</script>

<LlmConsole
  {title}
  {subtitle}
  assistantLabel="Consultant"
  {messages}
  {busy}
  {err}
  {usage}
  {retrievedLore}
  {models}
  bind:model
  bind:lorebooks
  onSend={send}
  placeholder="React, push back, redirect…  (Enter to send)"
>
  {#snippet right()}
    <StoryGraphCanvas graph={workingGraph} seq={graphSeq} {busy} onChange={(g) => setGraph(g, false)} />
  {/snippet}

  {#snippet actions()}
    <button class="fnbtn" onclick={runGraphOps} disabled={busy || fnBusy}
      title="Apply graph functions from attached function books — triggers offer, the model decides">
      {fnBusy ? '⚙ working…' : '⚙ Functions'}
    </button>
    <label class="fnauto" title="Run graph functions automatically after every turn">
      <input type="checkbox" bind:checked={autoFns} /> auto
    </label>
    {#if fnMsg}<span class="fnmsg">{fnMsg}</span>{/if}
    {#if hostActions}{@render hostActions({ graph: workingGraph, busy, hasExchange, lastAssistant, ready })}{/if}
  {/snippet}
</LlmConsole>

<style>
  .fnbtn {
    font-size: 12px; padding: 7px 12px; border-radius: 8px; cursor: pointer;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted);
  }
  .fnbtn:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); }
  .fnbtn:disabled { opacity: .5; cursor: not-allowed; }
  .fnauto { display: inline-flex; align-items: center; gap: 4px; font-size: 11.5px; color: var(--muted); cursor: pointer; }
  .fnauto input { width: 13px; height: 13px; }
  .fnmsg { font-size: 11.5px; color: var(--muted); align-self: center; }
</style>

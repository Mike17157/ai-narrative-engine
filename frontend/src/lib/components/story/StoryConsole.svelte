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
  import { get, put } from '$lib/api.js';
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
    onGraphChange = null,   // (graph) => void — fires on model AND user edits
    actions = null,         // snippet(ctx) — ctx = { graph, busy, hasExchange, lastAssistant, ready }
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

  function visibleProse(raw) {
    return raw.split(SPINE_MARKER)[0].replace(/\s*<{1,3}S?P?I?N?E?>{0,3}\s*$/i, '');
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
    }
  }

  function send(text) {
    callWorkshop([...messages.filter((m) => m.content || m.role === 'user'), { role: 'user', content: text }]);
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
    {#if actions}{@render actions({ graph: workingGraph, busy, hasExchange, lastAssistant, ready })}{/if}
  {/snippet}
</LlmConsole>

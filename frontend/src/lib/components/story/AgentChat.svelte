<script>
  // The story agent as a docked left-SIDEBAR chat (promoted from the floating bubble) — an ongoing
  // conversation that applies edits via graph-ops and drives the canvas. Voice in (Web Speech),
  // voice out (Kokoro/Web Speech), hands-free VAD loop, mode override. See [[voice-agent-ui]].
  // The ONE story-chat window — used by Overview, Cast, and Structure (no duplicates).
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import { voice, startListening, stopListening, speak, isSpeaking, toggleTts, toggleHandsFree } from '$lib/voice.svelte.js';

  // dock: 'left' (overview/structure sidebar) | 'bottom' (cast/outfit strip, in normal flow).
  // propose: when true, the agent's tool calls are SHOWN as approval cards instead of auto-applied —
  // a suggest→approve flow (used by the Structure creation step). Default false = auto-apply.
  //
  // ── The universal edit loop ── This ONE chat edits whatever ARTIFACT it's handed, via the same
  // suggest→approve→apply machinery. The only per-surface knob is `target` (where approved ops land):
  //   • artifact=null + target='story'      → the story graph (relationships/locations/places). Default.
  //   • artifact={cast,relationships} + target='draft' + commit=false → a CLIENT-HELD draft (the genesis
  //       cast queue); approved ops come back via onArtifact, nothing is persisted until the surface commits.
  //   • artifact={cast:[entry]} + target='character:<key>' → edits that one character's fields, persisted.
  // initialMode: pin a starting mode (the user can still switch). The draft cast queue pins
  // 'characters' so cast/relationship tools are always offered — its context isn't inferrable
  // from free chat the way a committed story's is.
  // firstMessage: a default opening assistant bubble shown before the conversation starts — explains
  // how to begin (e.g. on the new-story surface, how to describe a cast to generate).
  // initialConvo/initialHistory/initialBehaviour restore a cached transcript (the genesis draft caches
  // it so a "New story" survives navigation); onConvo emits the transcript out after every change.
  // dock 'corner' = a solid, in-flow FOOTER at the bottom of the pane (genesis/Structure): the chat
  // log, suggestion cards, and input live together in one panel that reserves its own room (a flex
  // sibling of the scrollable content above it, like the 'bottom' dock) rather than floating over it.
  // Collapses to just its header bar. onFocus({subjects,fields}) fires each turn so the caller can
  // highlight whatever the agent is working on (a cast node, a world field).
  // syncMode: the caller drives the agent mode from the SELECTED TAB — whenever it changes the chat
  // adopts that mode (the user can still override via the mode select until the next tab switch).
  let { storyKey, primaryChar = '', onApplied = () => {}, onSpeaker = () => {}, dock = 'left',
        propose = false, artifact = null, label = 'STORY', target = 'story', commit = true,
        onArtifact = () => {}, initialMode = '', firstMessage = '', model = '', onFocus = () => {},
        syncMode = null, nextStep = '',
        initialConvo = [], initialHistory = [], initialBehaviour = '', onConvo = () => {} } = $props();
  let isDraft = $derived(target === 'draft' || !commit);
  let isCorner = $derived(dock === 'corner');
  let collapsed = $state(false);   // corner mode: shrink to just the header
  // Pretty label for a Kokoro voice id (bm_george → "George · British ♂").
  const voiceLabel = (id) => {
    const acc = id[0] === 'b' ? 'British' : 'American', sex = id[1] === 'f' ? '♀' : '♂';
    const nm = id.slice(3).replace(/^./, (c) => c.toUpperCase());
    return `${nm} · ${acc} ${sex}`;
  };
  // The subjects/fields the agent's latest proposals touch — the caller highlights them in the canvas.
  function emitFocus(proposed) {
    const subjects = [], fields = [];
    for (const p of proposed || []) {
      const pr = p.params || {};
      if (p.fn === 'set_world_field' && pr.field) fields.push(String(pr.field));
      for (const k of ['name', 'target', 'source', 'id']) if (pr[k]) subjects.push(String(pr[k]));
    }
    if (subjects.length || fields.length) onFocus({ subjects, fields });
  }
  // Drop base64 `data:` URIs (face/reference portraits) anywhere in the doc — the model never needs the
  // image bytes, and a few of them blow past the context limit (one portrait ≈ hundreds of k tokens).
  function stripDataURIs(v) {
    if (typeof v === 'string') return v.startsWith('data:') ? '' : v;
    if (Array.isArray(v)) return v.map(stripDataURIs);
    if (v && typeof v === 'object') {
      const o = {};
      for (const k in v) o[k] = stripDataURIs(v[k]);
      return o;
    }
    return v;
  }
  // The doc to send: the handed artifact, else the story graph (back-compat). Portraits stripped.
  function graphNow() {
    if (artifact != null) return stripDataURIs($state.snapshot(artifact));
    const cur = stories.current || {};
    return stripDataURIs({ relationships: cur.relationships || [], locations: cur.locations || [], places: cur.places || [] });
  }

  let modes = $state([]);
  let mode = $state(initialMode);
  onMount(async () => { try { modes = (await get('/agent/modes')).modes || []; } catch { modes = []; } });
  // Selected tab drives the mode: adopt syncMode whenever it changes (null = caller isn't steering).
  $effect(() => { if (syncMode !== null) mode = syncMode; });

  let convo = $state([...(initialConvo || [])]);    // display transcript: {role:'user'|'assistant', content}
  let history = $state([...(initialHistory || [])]); // model context (user turns; cut to latest on a persona switch)
  let activeBehaviour = $state(initialBehaviour || '');
  let typed = $state('');
  let scroller;

  $effect(() => { onSpeaker(mode || activeBehaviour); });
  // Emit the transcript out so the caller can cache it (genesis draft). No-op for callers that don't pass onConvo.
  $effect(() => { onConvo({ convo: $state.snapshot(convo), history: $state.snapshot(history), activeBehaviour, mode }); });
  function scrollDown() { if (scroller) requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; }); }

  // Trim an over-talkative reply to a single terse reaction — the first non-question sentence. The
  // 'what's next' guidance is a computed line now, so the agent's chatter shouldn't ask or ramble.
  function terse(s) {
    s = (s || '').trim(); if (!s) return s;
    const parts = s.match(/[^.!?]+[.!?]*/g) || [s];
    return (parts.find((p) => !p.trim().endsWith('?')) || parts[0]).trim();
  }

  // The agent's reply: its conversational prose (r.reply) first; if it also ran tools, note them.
  function replyOf(r) {
    const text = (r.reply || '').trim();
    const names = (r.artifacts || []).map((a) => a.name).filter(Boolean);
    const ok = (r.applied || []).filter((o) => o.ok && o.fn !== '·persist');
    if (text) {
      if (names.length) return `${text}\n\n✦ Added ${names.join(', ')}.`;
      if (ok.length) return `${text}\n\n✦ ${ok.length} change${ok.length > 1 ? 's' : ''} applied.`;
      return text;
    }
    if (r.warning) return r.warning;
    if (names.length) return `Added ${names.join(', ')}.`;
    if (ok.length) return `Done — ${ok.length} change${ok.length > 1 ? 's' : ''}.`;
    return 'Okay.';
  }

  async function submit(text) {
    text = (text || '').trim();
    if (!text || voice.state === 'thinking') return;
    suggestView = false;                    // speaking flips back to the transcript
    const cur = stories.current || {};
    const before = (cur.relationships || []).map((r) => ({ ...r }));
    convo.push({ role: 'user', content: text }); scrollDown();
    history.push({ role: 'user', content: text });
    voice.state = 'thinking'; voice.partial = '';
    try {
      const resp = await post('/stories/graph-ops', {
        story: storyKey, character: primaryChar, graph: graphNow(),
        messages: history, active_behavior: activeBehaviour, mode, all_tools: true,
        propose, target, commit, artifact_label: label, model,
      });
      const r = resp.data || {};                       // post() returns {ok,status,data}
      if (!resp.ok) throw new Error(r.error || `request failed (${resp.status})`);
      activeBehaviour = r.active_behavior || activeBehaviour;
      if (r.context_cut) history = [{ role: 'user', content: text }];   // persona switched → fresh context
      if (propose) {
        // suggest → approve: show the prose + proposal cards; nothing is applied until approved.
        const full = (r.reply || '').trim() || (r.proposed?.length ? 'Here’s what I’d do — approve what fits:' : 'Okay.');
        const line = terse(full);                        // enforce a one-sentence reaction (models over-talk + ask)
        history.push({ role: 'assistant', content: r.reply || full });   // keep the full reply in model context
        convo.push({ role: 'assistant', content: line, proposed: r.proposed || [], decided: {} }); scrollDown();
        suggestView = (r.proposed || []).length > 0;   // cards arrived → flip to the takeover view
        emitFocus(r.proposed);
        speak(line);
      } else {
        voice.state = 'acting';
        if (isDraft) onArtifact(r.graph || {}); else await loadStory(storyKey);
        const line = replyOf(r);
        history.push({ role: 'assistant', content: (r.reply || line) });  // keep replies in model context
        convo.push({ role: 'assistant', content: line }); scrollDown();
        speak(line);
        onApplied(r.applied || [], r.artifacts || [], r.warning || '', before);
      }
    } catch (e) {
      const line = `⚠ ${e?.message || e}`;
      convo.push({ role: 'assistant', content: line }); scrollDown();
      onApplied([], [], line, before);
    } finally {
      voice.state = 'idle'; voice.partial = '';
      if (voice.handsFree) relisten();
    }
  }

  // Approve one proposed tool call → apply exactly that call (no model round-trip). Approvals are
  // SERIALIZED: each draft apply sends the client-held doc and gets the whole doc back, so two
  // concurrent approvals would read the same base and clobber each other (last write wins). Chaining
  // makes each apply see the previous one's result.
  let applyChain = Promise.resolve();
  function approve(msg, p, i) {
    applyChain = applyChain.then(() => applyOne(msg, p, i)).catch(() => {});
    return applyChain;
  }
  async function applyOne(msg, p, i) {
    if (msg.decided?.['p' + i]) return;
    voice.state = 'thinking';
    const resp = await post('/stories/graph-ops', {
      story: storyKey, character: primaryChar, graph: graphNow(),
      apply_calls: [{ fn: p.fn, params: p.params }], all_tools: true, target, commit, artifact_label: label, model,
    });
    voice.state = 'idle';
    const r = resp.data || {};
    if (resp.ok) {
      if (isDraft) onArtifact(r.graph || {}); else await loadStory(storyKey);
      msg.decided = { ...msg.decided, ['p' + i]: 'added' }; onApplied(r.applied || [], r.artifacts || [], '', []);
    } else { msg.decided = { ...msg.decided, ['p' + i]: 'error' }; }
  }
  const dismiss = (msg, i) => { msg.decided = { ...msg.decided, ['p' + i]: 'no' }; };

  function send() { const t = typed.trim(); typed = ''; submit(t); }
  export function ask(text) { submit(text); }   // seed the agent from outside (e.g. the outfit rail)
  function toggleMic() {
    if (voice.state === 'listening') { stopListening(); return; }
    if (!startListening(submit)) voice.supported = false;
  }
  function toggleHF() {
    toggleHandsFree();
    if (voice.handsFree) { if (voice.state === 'idle') startListening(submit); }
    else stopListening();
  }
  function relisten() {
    const t = () => {
      if (!voice.handsFree) return;
      if (isSpeaking() || voice.state !== 'idle') { setTimeout(t, 250); return; }
      startListening(submit);
    };
    setTimeout(t, 400);
  }

  const busy = $derived(voice.state === 'thinking' || voice.state === 'acting');
  // Undecided proposals across the whole transcript — hoisted into the bottom strip in corner mode.
  const pending = $derived(convo.flatMap((m) => (m.proposed || [])
    .map((p, j) => ({ m, p, j })).filter(({ m: mm, j: jj }) => !mm.decided?.['p' + jj])));
  const focusOne = (p) => emitFocus([p]);   // hovering a proposal card highlights its subject
  // Card HEADLINE: a character card leads with its (specific) role; other tools fall back to the label,
  // minus the redundant "verb:" prefix ("add character: …" → the role reads as the title, not the verb).
  function cardTitle(p) {
    if (p.fn === 'add_character' && p.params?.role) return p.params.role;
    return (p.label || p.fn || '').replace(/^[^:]+:\s*/, '') || p.label || p.fn;
  }
  const cardFull = (p) => (p.params?.persona || p.detail || '');   // full text on hover
  const modeLabel = $derived(mode ? (modes.find((m) => m.id === mode)?.label || mode) : 'Auto');
  const latestMsg = $derived(convo[convo.length - 1]);
  // Suggestions TAKEOVER (propose mode): when the agent returns cards the pane flips to a cards-only
  // view — just the latest line + the cards. Acting (all cards resolved) or speaking flips it back to
  // the normal transcript, until the next batch of suggestions arrives. See [[conversational-edit-loop]].
  let suggestView = $state(false);
  $effect(() => { if (!pending.length) suggestView = false; });   // resolved them all → back to chat
</script>

<aside class="chat" class:bottom={dock === 'bottom'} class:corner={isCorner} class:collapsed={isCorner && collapsed}>
  <div class="chead">
    <span class="ctitle">{dock === 'bottom' ? '👗 Outfit agent' : '💬 Story chat'}</span>
    <span class="sp"></span>
    {#if isCorner}
      <button class="cbtn" onclick={() => (collapsed = !collapsed)} title={collapsed ? 'Expand' : 'Collapse'} aria-label={collapsed ? 'Expand chat' : 'Collapse chat'}>{collapsed ? '▲' : '▁'}</button>
    {/if}
    {#if syncMode !== null}
      <!-- Mode follows the selected TAB — a passive indicator, not a picker. -->
      <span class="modelabel" title="Agent mode follows the selected tab">{modeLabel}</span>
    {:else if modes.length}
      <select class="mode" bind:value={mode} title="Force a mode (Auto = detect from what you say)">
        <option value="">Auto</option>
        {#each modes as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
      </select>
    {/if}
    {#if voice.ttsSupported}
      <button class="cbtn" class:on={voice.tts} onclick={toggleTts} title={voice.tts ? 'Mute agent voice' : 'Speak replies'}>{voice.tts ? '🔊' : '🔇'}</button>
    {/if}
    {#if voice.tts && voice.voices.length}
      <select class="mode vpick" bind:value={voice.voiceId} title="Agent voice">
        {#each voice.voices as v (v)}<option value={v}>{voiceLabel(v)}</option>{/each}
      </select>
    {/if}
    {#if voice.supported}
      <button class="cbtn" class:on={voice.handsFree} onclick={toggleHF} title="Hands-free (auto-listen each turn)">🎙</button>
    {/if}
  </div>

  {#if nextStep && !collapsed}
    <div class="nextstep"><span class="nslabel">Next</span> {nextStep}</div>
  {/if}

  {#if !collapsed}
  {#if propose && suggestView && pending.length}
    <!-- Suggestions takeover: only the latest line, then the cards fill the pane. Resolve them (or
         speak) to flip back to the transcript. -->
    <div class="log sugview">
      {#if latestMsg}<div class="msg {latestMsg.role} recent">{latestMsg.content}</div>{/if}
      {#if pending.length > 1}<div class="popts">Pick what fits — Apply or ✕</div>{/if}
      {#each pending as { m, p, j } (m.content + j)}
        <div class="prop card" role="group" onmouseenter={() => focusOne(p)}>
          <div class="pbody">
            <span class="plabel">{cardTitle(p)}{#if p.params?.group}<span class="pgroup">{p.params.group}</span>{/if}</span>
            {#if p.detail}<span class="pdetail" title={cardFull(p)}>{p.detail}</span>{/if}
          </div>
          <div class="pacts">
            <button class="papp" onclick={() => approve(m, p, j)}>Apply</button>
            <button class="pdis" onclick={() => dismiss(m, j)} aria-label="Dismiss">✕</button>
          </div>
        </div>
      {/each}
      {#if busy}<div class="msg assistant pending"><span class="dots"><i></i><i></i><i></i></span></div>{/if}
    </div>
  {:else}
  <div class="log" bind:this={scroller}>
    {#if !convo.length}
      {#if firstMessage}
        <div class="msg assistant intro">{firstMessage}</div>
      {:else if dock === 'bottom'}
        <div class="hint">Talk or type to build outfits for {primaryChar || 'the cast'} — “give them a winter coat”, “make the casual outfit a hoodie”, “plan the wardrobe from the scenes”.</div>
      {:else}
        <div class="hint">Talk or type to shape the story — “add a rival for Eli”, “cool their bond”, “plan the wardrobe from the scenes”.</div>
      {/if}
    {/if}
    {#each convo as m, i (i)}
      <div class="msg {m.role}">{m.content}</div>
      {#if !propose}
      {#each (m.proposed || []) as p, j (j)}
        {@const d = m.decided?.['p' + j]}
        <div class="prop" class:done={d === 'added'} class:gone={d === 'no'}>
          <div class="pbody">
            <span class="plabel">{p.label || p.fn}</span>
            {#if p.detail}<span class="pdetail">{p.detail}</span>{/if}
          </div>
          {#if d === 'added'}<span class="pdone">✓ applied</span>
          {:else if d === 'no'}<span class="pgone">dismissed</span>
          {:else}
            <button class="papp" onclick={() => approve(m, p, j)}>Apply</button>
            <button class="pdis" onclick={() => dismiss(m, j)} aria-label="Dismiss">✕</button>
          {/if}
        </div>
      {/each}
      {/if}
    {/each}
    {#if voice.partial}<div class="msg user partial">{voice.partial}</div>{/if}
    {#if busy}<div class="msg assistant pending"><span class="dots"><i></i><i></i><i></i></span></div>{/if}
  </div>
  {/if}

  <div class="cfoot">
    {#if voice.supported}
      <button class="orb" class:listening={voice.state === 'listening'} class:busy onclick={toggleMic}
              aria-label="Talk to the story agent" title="Talk">
        {#if busy}<span class="spin"></span>{:else if voice.state === 'listening'}<span class="rec"></span>{:else}🎤{/if}
      </button>
    {/if}
    <input class="cin" bind:value={typed}
           placeholder={voice.state === 'listening' ? 'listening…' : 'Message the story agent…'}
           onkeydown={(e) => { if (e.key === 'Enter' && !busy) send(); }} />
    <button class="csend" disabled={busy || !typed.trim()} onclick={send} aria-label="Send">↳</button>
  </div>
  {/if}
</aside>

<style>
  /* top aligns under the chrome — 86px (topbar + subnav) or 48px when the subnav is hidden (genesis). */
  .chat { position: fixed; left: 0; top: var(--chrome-top, 86px); bottom: 0; width: 340px; z-index: 45;
          display: flex; flex-direction: column; background: var(--panel, #14161f);
          border-right: 1px solid var(--border, #2a2f44); }

  /* Bottom dock: in normal flow as a short strip under the catalogue (cast page). */
  .chat.bottom { position: relative; left: auto; top: auto; bottom: auto; width: 100%;
                 height: 240px; flex: none; z-index: 1;
                 border-right: none; border-top: 1px solid var(--border, #2a2f44); }

  /* Corner dock (structure/genesis): ONE solid FOOTER — log, suggestions and input stacked together
     instead of scattered panels. In normal flow (a flex sibling of the scrollable content above it,
     same shape as the 'bottom' dock) so it takes up its OWN room instead of floating over the page. */
  .chat.corner { position: relative; top: auto; left: auto; right: auto; bottom: auto; width: 100%;
                 height: 320px; flex: none; border: none; border-top: 1px solid var(--border, #2a2f44);
                 border-radius: 0; background: var(--panel, #14161f); }
  .chat.corner.collapsed { height: auto; }

  /* Persistent "what's next" nudge — generated from the draft state, not the transcript. */
  .nextstep { flex: none; padding: 7px 14px; font-size: 12px; color: var(--text); line-height: 1.4;
              border-bottom: 1px solid var(--border-soft); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .nslabel { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
             color: var(--accent); margin-right: 7px; }

  /* Suggestions live inside the footer, between the log and the input — one panel, not a separate bar. */
  .suggest { flex: none; padding: 6px 12px; border-top: 1px solid var(--border-soft);
             display: flex; flex-direction: column; gap: 5px; }
  .striplabel { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--faint);
                padding-left: 2px; }
  .striprow { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 2px; }
  .scard { flex: 0 0 auto; max-width: 320px; display: flex; align-items: center; gap: 10px;
           padding: 8px 10px; border: 1px solid var(--border-soft); border-left: 2px solid var(--accent);
           border-radius: 10px; background: var(--elev); transition: border-color .12s, transform .12s; }
  .scard:hover { border-color: var(--accent); transform: translateY(-1px); }
  .sbody { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .slabel { font-size: 12px; font-weight: 600; color: var(--text); white-space: nowrap; }
  .sdetail { font-size: 11px; color: var(--muted); line-height: 1.35;
             display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .sacts { display: flex; align-items: center; gap: 5px; flex: none; }
  .vpick { max-width: 128px; }

  .chead { display: flex; align-items: center; gap: 6px; padding: 10px 12px;
           border-bottom: 1px solid var(--border-soft); flex: none; }
  .ctitle { font-size: 13px; font-weight: 700; color: var(--text); }
  .sp { flex: 1; }
  .modelabel { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--accent); padding: 3px 9px; border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 14%, transparent); white-space: nowrap; }
  .mode { max-width: 110px; background: var(--elev); color: var(--text); border: 1px solid var(--border-soft);
          border-radius: 7px; padding: 4px 6px; font-size: 11px; cursor: pointer; }
  .mode:focus { outline: none; border-color: var(--accent); }
  .cbtn { width: 28px; height: 28px; flex: none; padding: 0; border-radius: 7px; font-size: 13px;
          background: var(--elev); color: var(--text); border: 1px solid var(--border-soft); cursor: pointer; }
  .cbtn.on { border-color: var(--accent); }
  .cbtn:hover { filter: brightness(1.1); }

  .log { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .hint { color: var(--faint); line-height: 1.6; font-style: italic; padding: 8px 4px; }
  .msg { max-width: 86%; font-size: 13px; line-height: 1.45; padding: 8px 11px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: var(--accent, #6d8cff); color: #0b0e14; border-bottom-right-radius: 4px; }
  .msg.assistant { align-self: flex-start; background: var(--elev, #1b1e2b); color: var(--text); border: 1px solid var(--border-soft); border-bottom-left-radius: 4px; }
  .msg.assistant.intro { max-width: 100%; background: var(--elev); color: var(--muted); line-height: 1.55; }
  .msg.user.partial { opacity: .6; }
  .msg.pending { padding: 10px 12px; }
  /* suggest→approve option/proposal cards (Structure creation step) */
  .popts { align-self: flex-start; font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px;
    color: var(--faint); margin: 2px 0 -2px 2px; }
  .prop { align-self: flex-start; max-width: 92%; display: flex; align-items: flex-start; gap: 8px;
    padding: 8px 10px; border: 1px solid var(--border-soft); border-left: 2px solid var(--accent);
    border-radius: 9px; background: var(--elev); }
  .prop.gone { opacity: .5; }
  .prop.done { border-left-color: var(--good, #6ec77f); }
  .pbody { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  .plabel { font-size: 12px; font-weight: 600; color: var(--text); }
  .pdetail { font-size: 11.5px; color: var(--muted); line-height: 1.4; }
  .papp { font-size: 11.5px; padding: 4px 11px; border-radius: 7px; border: none; cursor: pointer;
    background: var(--accent); color: #0b0e14; }
  .pdis { width: 24px; height: 24px; flex: none; padding: 0; border-radius: 7px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); font-size: 12px; }
  .pdone { font-size: 11px; color: var(--good, #6ec77f); }
  .pgone { font-size: 11px; color: var(--faint); }
  .perr { font-size: 11px; color: var(--bad); }

  /* Suggestions takeover: the latest line pinned at top, then full-width cards filling the pane. */
  .sugview .msg.recent { align-self: stretch; max-width: 100%; background: transparent; border: 0;
    color: var(--muted); font-style: italic; padding: 2px 2px 4px; }
  .sugview .prop.card { align-self: stretch; max-width: 100%; flex-direction: column; gap: 8px;
    padding: 11px 12px; border-radius: 11px; }
  .sugview .prop.card .pbody { width: 100%; }
  .sugview .prop.card .plabel { font-size: 13px; display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .sugview .prop.card .pdetail { font-size: 12px; color: var(--muted); -webkit-line-clamp: 2; display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }
  .pgroup { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; padding: 1px 7px;
    border-radius: 999px; background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent); }
  .pacts { display: flex; align-items: center; gap: 6px; align-self: flex-end; }
  .dots { display: inline-flex; gap: 4px; }
  .dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite; }
  .dots i:nth-child(2) { animation-delay: .2s; } .dots i:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,100% { opacity: .25; } 50% { opacity: 1; } }

  .cfoot { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border-soft); flex: none; }
  .orb { width: 38px; height: 38px; flex: none; border-radius: 50%; padding: 0; border: none; cursor: pointer;
         background: var(--accent, #6d8cff); display: grid; place-items: center; font-size: 16px; }
  .orb.listening { animation: pulse 1.1s ease-in-out infinite; }
  .orb.busy { background: var(--elev-2, #2a2f44); cursor: default; }
  @keyframes pulse { 0%,100% { transform: scale(1); } 50% { transform: scale(1.1); } }
  .rec { width: 12px; height: 12px; border-radius: 50%; background: #0b0e14; }
  .spin { width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(255,255,255,.25); border-top-color: #fff; animation: spin .7s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .cin { flex: 1; min-width: 0; background: var(--elev, #1b1e2b); border: 1px solid var(--border-soft); color: var(--text);
         border-radius: 8px; padding: 8px 10px; font-size: 13px; }
  .cin:focus { outline: none; border-color: var(--accent); }
  .csend { width: 34px; height: 34px; flex: none; border: none; border-radius: 8px; font-size: 16px; cursor: pointer;
           background: var(--accent, #6d8cff); color: #0b0e14; }
  .csend:disabled { opacity: .4; cursor: default; }
</style>

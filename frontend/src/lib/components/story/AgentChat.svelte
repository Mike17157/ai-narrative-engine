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
  let { storyKey, primaryChar = '', onApplied = () => {}, onSpeaker = () => {}, dock = 'left',
        propose = false, artifact = null, label = 'STORY', target = 'story', commit = true,
        onArtifact = () => {}, initialMode = '', firstMessage = '', model = '',
        initialConvo = [], initialHistory = [], initialBehaviour = '', onConvo = () => {} } = $props();
  let isDraft = $derived(target === 'draft' || !commit);
  // The doc to send: the handed artifact, else the story graph (back-compat).
  function graphNow() {
    if (artifact != null) return $state.snapshot(artifact);
    const cur = stories.current || {};
    return { relationships: cur.relationships || [], locations: cur.locations || [], places: cur.places || [] };
  }

  let modes = $state([]);
  let mode = $state(initialMode);
  onMount(async () => { try { modes = (await get('/agent/modes')).modes || []; } catch { modes = []; } });

  let convo = $state([...(initialConvo || [])]);    // display transcript: {role:'user'|'assistant', content}
  let history = $state([...(initialHistory || [])]); // model context (user turns; cut to latest on a persona switch)
  let activeBehaviour = $state(initialBehaviour || '');
  let typed = $state('');
  let scroller;

  $effect(() => { onSpeaker(mode || activeBehaviour); });
  // Emit the transcript out so the caller can cache it (genesis draft). No-op for callers that don't pass onConvo.
  $effect(() => { onConvo({ convo: $state.snapshot(convo), history: $state.snapshot(history), activeBehaviour, mode }); });
  function scrollDown() { if (scroller) requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; }); }

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
        const line = (r.reply || '').trim() || (r.proposed?.length ? 'Here’s what I’d do — approve what fits:' : 'Okay.');
        history.push({ role: 'assistant', content: r.reply || line });
        convo.push({ role: 'assistant', content: line, proposed: r.proposed || [], decided: {} }); scrollDown();
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
</script>

<aside class="chat" class:bottom={dock === 'bottom'}>
  <div class="chead">
    <span class="ctitle">{dock === 'bottom' ? '👗 Outfit agent' : '💬 Story chat'}</span>
    <span class="sp"></span>
    {#if modes.length}
      <select class="mode" bind:value={mode} title="Force a mode (Auto = detect from what you say)">
        <option value="">Auto</option>
        {#each modes as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
      </select>
    {/if}
    {#if voice.ttsSupported}
      <button class="cbtn" class:on={voice.tts} onclick={toggleTts} title={voice.tts ? 'Mute agent voice' : 'Speak replies'}>{voice.tts ? '🔊' : '🔇'}</button>
    {/if}
    {#if voice.supported}
      <button class="cbtn" class:on={voice.handsFree} onclick={toggleHF} title="Hands-free (auto-listen each turn)">🎙</button>
    {/if}
  </div>

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
      {#if (m.proposed || []).length > 1}<div class="popts">Options — pick what fits</div>{/if}
      {#each (m.proposed || []) as p, j (j)}
        {@const d = m.decided?.['p' + j]}
        <div class="prop" class:done={d === 'added'} class:gone={d === 'no'}>
          <div class="pbody">
            <span class="plabel">{p.label || p.fn}</span>
            {#if p.detail}<span class="pdetail">{p.detail}</span>{/if}
          </div>
          {#if d === 'added'}<span class="pdone">✓ applied</span>
          {:else if d === 'no'}<span class="pgone">dismissed</span>
          {:else if d === 'error'}<span class="perr">failed — retry</span>
          {:else}
            <button class="papp" onclick={() => approve(m, p, j)}>Apply</button>
            <button class="pdis" onclick={() => dismiss(m, j)} aria-label="Dismiss">✕</button>
          {/if}
        </div>
      {/each}
    {/each}
    {#if voice.partial}<div class="msg user partial">{voice.partial}</div>{/if}
    {#if busy}<div class="msg assistant pending"><span class="dots"><i></i><i></i><i></i></span></div>{/if}
  </div>

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

  .chead { display: flex; align-items: center; gap: 6px; padding: 10px 12px;
           border-bottom: 1px solid var(--border-soft); flex: none; }
  .ctitle { font-size: 13px; font-weight: 700; color: var(--text); }
  .sp { flex: 1; }
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

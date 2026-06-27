<script>
  // The story agent as a docked left-SIDEBAR chat (promoted from the floating bubble) — an ongoing
  // conversation that applies edits via graph-ops and drives the canvas. Voice in (Web Speech),
  // voice out (Kokoro/Web Speech), hands-free VAD loop, mode override. See [[voice-agent-ui]].
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import { voice, startListening, stopListening, speak, isSpeaking, toggleTts, toggleHandsFree } from '$lib/voice.svelte.js';

  let { storyKey, primaryChar = '', onApplied = () => {}, onSpeaker = () => {} } = $props();

  let modes = $state([]);
  let mode = $state('');
  onMount(async () => { try { modes = (await get('/agent/modes')).modes || []; } catch { modes = []; } });

  let convo = $state([]);          // display transcript: {role:'user'|'assistant', content}
  let history = $state([]);        // model context (user turns; cut to latest on a persona switch)
  let activeBehaviour = $state('');
  let typed = $state('');
  let scroller;

  $effect(() => { onSpeaker(mode || activeBehaviour); });
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
        story: storyKey, character: primaryChar,
        graph: { relationships: cur.relationships || [], locations: cur.locations || [], places: cur.places || [] },
        messages: history, active_behavior: activeBehaviour, mode, all_tools: true, artifact_label: 'STORY',
      });
      const r = resp.data || {};                       // post() returns {ok,status,data}
      if (!resp.ok) throw new Error(r.error || `request failed (${resp.status})`);
      activeBehaviour = r.active_behavior || activeBehaviour;
      if (r.context_cut) history = [{ role: 'user', content: text }];   // persona switched → fresh context
      voice.state = 'acting';
      await loadStory(storyKey);
      const line = replyOf(r);
      history.push({ role: 'assistant', content: (r.reply || line) });  // keep replies in model context
      convo.push({ role: 'assistant', content: line }); scrollDown();
      speak(line);
      onApplied(r.applied || [], r.artifacts || [], r.warning || '', before);
    } catch (e) {
      const line = `⚠ ${e?.message || e}`;
      convo.push({ role: 'assistant', content: line }); scrollDown();
      onApplied([], [], line, before);
    } finally {
      voice.state = 'idle'; voice.partial = '';
      if (voice.handsFree) relisten();
    }
  }

  function send() { const t = typed.trim(); typed = ''; submit(t); }
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

<aside class="chat">
  <div class="chead">
    <span class="ctitle">💬 Story chat</span>
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
      <div class="hint">Talk or type to shape the story — “add a rival for Eli”, “cool their bond”, “plan the wardrobe from the scenes”.</div>
    {/if}
    {#each convo as m, i (i)}
      <div class="msg {m.role}">{m.content}</div>
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
  .chat { position: fixed; left: 0; top: 86px; bottom: 0; width: 340px; z-index: 45;
          display: flex; flex-direction: column; background: var(--panel, #14161f);
          border-right: 1px solid var(--border, #2a2f44); }

  .chead { display: flex; align-items: center; gap: 6px; padding: 10px 12px;
           border-bottom: 1px solid var(--border-soft); flex: none; }
  .ctitle { font-size: 13px; font-weight: 700; color: var(--text); }
  .sp { flex: 1; }
  .mode { max-width: 110px; background: var(--elev); color: var(--text); border: 1px solid var(--border-soft);
          border-radius: 7px; padding: 4px 6px; font-size: 11px; cursor: pointer; }
  .mode:focus { outline: none; border-color: var(--accent); }
  .cbtn { width: 28px; height: 28px; flex: none; padding: 0; border-radius: 7px; font-size: 13px;
          background: var(--elev); color: var(--text); border: 1px solid var(--border-soft); cursor: pointer; box-shadow: none; }
  .cbtn.on { border-color: var(--accent); }
  .cbtn:hover { filter: brightness(1.1); }

  .log { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .hint { font-size: 12.5px; color: var(--faint); line-height: 1.6; font-style: italic; padding: 8px 4px; }
  .msg { max-width: 86%; font-size: 13px; line-height: 1.45; padding: 8px 11px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: var(--accent, #6d8cff); color: #0b0e14; border-bottom-right-radius: 4px; }
  .msg.assistant { align-self: flex-start; background: var(--elev, #1b1e2b); color: var(--text); border: 1px solid var(--border-soft); border-bottom-left-radius: 4px; }
  .msg.user.partial { opacity: .6; }
  .msg.pending { padding: 10px 12px; }
  .dots { display: inline-flex; gap: 4px; }
  .dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite; }
  .dots i:nth-child(2) { animation-delay: .2s; } .dots i:nth-child(3) { animation-delay: .4s; }
  @keyframes blink { 0%,100% { opacity: .25; } 50% { opacity: 1; } }

  .cfoot { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border-soft); flex: none; }
  .orb { width: 38px; height: 38px; flex: none; border-radius: 50%; padding: 0; border: none; cursor: pointer;
         background: var(--accent, #6d8cff); display: grid; place-items: center; font-size: 16px; box-shadow: none; }
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
           background: var(--accent, #6d8cff); color: #0b0e14; box-shadow: none; }
  .csend:disabled { opacity: .4; cursor: default; }
</style>

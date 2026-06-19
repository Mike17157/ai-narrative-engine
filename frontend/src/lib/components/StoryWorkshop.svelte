<script>
  // Three-phase conversational story planning:
  // Phase 0 — Premise: chat about world, character state, ideas
  // Phase 1 — Ending: lock in where the story lands
  // Phase 2 — Arc Structure: structured arc suggestion (not a chat)
  import { onMount } from 'svelte';

  let {
    character = '',       // character key
    charName  = '',
    onGenerate   = null,  // callback(premise: string) — advance to storyboard gen
    onSkip       = null,  // callback() — bypass workshop
    onConfirmArcs = null, // callback({ intended_ending, arcs }) — user locked in arc structure
  } = $props();

  // ── Phase state ──────────────────────────────────────────────────────────────
  let phase = $state(0);   // 0 | 1 | 2

  // Each phase has its own message list so context is kept but displayed separately.
  let phase0Messages = $state([]);  // [{role, content}]
  let phase1Messages = $state([]);
  let userInput      = $state('');
  let premise        = $state('');   // optional seed for phase 0
  let busy           = $state(false);
  let err            = $state(null);
  let msgBox;

  // Phase 2 — arc suggestion state
  let intendedEnding = $state('');
  let arcs           = $state([]);   // [{id, name, dramatic_function, mini_ending, cast, rationale}]
  let arcBusy        = $state(false);
  let arcErr         = $state(null);

  // Derived convenience
  let messages    = $derived(phase === 0 ? phase0Messages : phase1Messages);
  let hasExchange = $derived(messages.some((m) => m.role === 'user'));

  function setMessages(msgs) {
    if (phase === 0) phase0Messages = msgs;
    else phase1Messages = msgs;
  }

  function scrollBottom() {
    queueMicrotask(() => { if (msgBox) msgBox.scrollTop = msgBox.scrollHeight; });
  }

  // ── Workshop chat call (streaming SSE) ───────────────────────────────────────
  async function callWorkshop(msgs) {
    busy = true;
    err  = null;

    // Add a placeholder assistant message we'll fill in as tokens arrive.
    const withPlaceholder = [...msgs, { role: 'assistant', content: '' }];
    const assistantIdx    = withPlaceholder.length - 1;
    setMessages(withPlaceholder);

    try {
      const res = await fetch('/api/stories/workshop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character,
          messages: msgs,
          premise: premise.trim(),
          phase,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        err = data?.error || `HTTP ${res.status}`;
        setMessages(withPlaceholder.slice(0, assistantIdx));
        return;
      }

      const reader = res.body.getReader();
      const dec    = new TextDecoder();
      let buf      = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i);
          buf = buf.slice(i + 2);
          const dataLine = chunk.split('\n').find((l) => l.startsWith('data:'));
          if (!dataLine) continue;
          let ev;
          try { ev = JSON.parse(dataLine.slice(5).trim()); } catch { continue; }

          if (ev.type === 'delta') {
            const updated = [...(phase === 0 ? phase0Messages : phase1Messages)];
            updated[assistantIdx] = { ...updated[assistantIdx], content: updated[assistantIdx].content + ev.text };
            setMessages(updated);
            scrollBottom();
          } else if (ev.type === 'error') {
            err = ev.error;
          }
        }
      }
    } catch (e) {
      err = String(e);
      setMessages((phase === 0 ? phase0Messages : phase1Messages).slice(0, assistantIdx));
    } finally {
      busy = false;
    }
  }

  // Send user message in the current phase chat.
  async function send() {
    const text = userInput.trim();
    if (!text || busy) return;
    userInput = '';
    const next = [...(phase === 0 ? phase0Messages : phase1Messages), { role: 'user', content: text }];
    await callWorkshop(next);
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  // Auto-send on mount (phase 0 opening).
  onMount(() => { callWorkshop([]); });

  // ── Phase transitions ─────────────────────────────────────────────────────────
  async function advanceToPhase1() {
    phase = 1;
    // Auto-send the opening user nudge — model responds as if starting that thread.
    const openingMsg = `Let's design the ending now. What happens in the final scene? What does ${charName || 'the protagonist'} discover, lose, or choose? How should the reader feel?`;
    await callWorkshop([{ role: 'user', content: openingMsg }]);
  }

  async function advanceToPhase2() {
    // Capture the intended ending from the field (already editable).
    // If empty, seed it from the last phase1 assistant message.
    if (!intendedEnding.trim()) {
      const lastA = [...phase1Messages].reverse().find((m) => m.role === 'assistant');
      intendedEnding = lastA?.content?.trim() || '';
    }
    phase = 2;
    await suggestArcs();
  }

  // ── Phase 2: arc suggestion ───────────────────────────────────────────────────
  async function suggestArcs() {
    arcBusy = true;
    arcErr  = null;
    arcs    = [];

    try {
      const res = await fetch('/api/stories/workshop/arcs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character,
          messages: [...phase0Messages, ...phase1Messages],
          intended_ending: intendedEnding,
          story_cast: [],
        }),
      });

      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.arcs) {
        arcs = data.arcs;
      } else {
        arcErr = data?.error || 'arc suggestion failed';
      }
    } catch (e) {
      arcErr = String(e);
    } finally {
      arcBusy = false;
    }
  }

  // ── Phase 0: "Generate Full Story" path (bypass arcs) ────────────────────────
  function generate() {
    const lastAssistant = [...phase0Messages].reverse().find((m) => m.role === 'assistant');
    const seed = lastAssistant?.content?.trim() || premise.trim();
    onGenerate?.(seed);
  }

  // ── Phase 2 confirm ──────────────────────────────────────────────────────────
  function confirmArcs() {
    onConfirmArcs?.({ intended_ending: intendedEnding, arcs });
  }

  // Step label helper
  const STEP_LABELS = ['Premise', 'Ending', 'Arc Structure'];
</script>

<div class="ws">
  <!-- Step indicator -->
  <div class="steps">
    {#each STEP_LABELS as label, i}
      <div class="step" class:active={phase === i} class:done={phase > i}>
        <span class="snum">{i + 1}</span>
        <span class="slabel">{label}</span>
      </div>
      {#if i < STEP_LABELS.length - 1}
        <div class="sep" class:active={phase > i}></div>
      {/if}
    {/each}
  </div>

  <!-- Header -->
  <div class="wshead">
    <span class="wstitle">Story Workshop</span>
    <span class="wsub">
      {phase === 0 ? 'Premise & world' : phase === 1 ? 'Designing the ending' : 'Arc structure'}
      — {charName || character}
    </span>
    {#if onSkip && phase === 0}
      <button class="skip" onclick={onSkip}>Skip to generate →</button>
    {/if}
  </div>

  <!-- ─── Phase 0 & 1: chat ─────────────────────────────────────────────────── -->
  {#if phase < 2}
    <!-- Premise seed (only in phase 0) -->
    {#if phase === 0}
      <div class="premise-row">
        <input class="fld" bind:value={premise} placeholder="What's the story about? (optional seed)" />
      </div>
    {/if}

    <!-- Intended ending text field (phase 1 only) -->
    {#if phase === 1}
      <div class="ending-row">
        <label class="el">Agreed ending</label>
        <textarea
          class="fld ta"
          rows="2"
          bind:value={intendedEnding}
          placeholder="Type the ending directly, or it will auto-populate from the last assistant message…"
        ></textarea>
      </div>
    {/if}

    <!-- Message list -->
    <div class="msgs" bind:this={msgBox}>
      {#each messages as msg, i (i)}
        <div class="msg" class:user={msg.role === 'user'} class:assistant={msg.role === 'assistant'}>
          {#if msg.role === 'assistant'}
            <span class="mname">Workshop</span>
          {/if}
          <div class="mbubble">
            {#if !msg.content && busy && i === messages.length - 1}
              <span class="typing">
                <span></span><span></span><span></span>
              </span>
            {:else}
              {msg.content}
            {/if}
          </div>
        </div>
      {/each}
      {#if err}
        <div class="merr">⚠ {err}</div>
      {/if}
    </div>

    <!-- Input row -->
    <div class="inputrow">
      <textarea
        class="fld ta"
        rows="2"
        bind:value={userInput}
        onkeydown={onKey}
        disabled={busy}
        placeholder={phase === 0
          ? 'Your thoughts, directions, changes… (Enter to send)'
          : 'Refine the ending… (Enter to send)'}
      ></textarea>
      <button class="send" onclick={send} disabled={busy || !userInput.trim()}>
        {busy ? '…' : '↑'}
      </button>
    </div>

    <!-- Phase action bar -->
    <div class="acts">
      {#if phase === 0}
        {#if onSkip}
          <button class="ghost sm" onclick={onSkip}>Skip workshop</button>
        {/if}
        <span class="sp"></span>
        <button class="ghost sm" onclick={generate} disabled={busy || !hasExchange}>
          Generate (no arcs) →
        </button>
        <button class="go" onclick={advanceToPhase1} disabled={busy || !hasExchange}>
          Design the Ending →
        </button>
      {:else}
        <span class="sp"></span>
        <button class="go" onclick={advanceToPhase2} disabled={busy || !hasExchange}>
          Suggest Arc Structure →
        </button>
      {/if}
    </div>

  <!-- ─── Phase 2: arc cards ────────────────────────────────────────────────── -->
  {:else}
    <!-- Intended ending display/edit -->
    <div class="ending-callout">
      <div class="ec-label">🏁 Intended ending</div>
      <textarea
        class="fld ta ec-ta"
        rows="2"
        bind:value={intendedEnding}
        placeholder="How the story ends…"
      ></textarea>
    </div>

    {#if arcBusy}
      <div class="arc-loading">
        <span class="spin"></span>
        <span class="arc-loading-txt">Suggesting arc structure…</span>
      </div>
    {:else if arcErr}
      <div class="merr">⚠ {arcErr}</div>
      <button class="ghost sm" onclick={suggestArcs}>↻ Retry</button>
    {:else if arcs.length}
      <div class="arc-list">
        {#each arcs as arc, i}
          <div class="arc-card">
            <div class="arc-head">
              <span class="arc-num">{i + 1}</span>
              <input class="fld arc-name" bind:value={arc.name} placeholder="Arc name" />
              {#if arc.dramatic_function}
                <span class="chip df">{arc.dramatic_function}</span>
              {/if}
            </div>
            {#if arc.cast?.length}
              <div class="arc-cast">
                {#each arc.cast as ckey}
                  <span class="chip cast-chip">{ckey}</span>
                {/each}
              </div>
            {/if}
            <textarea
              class="fld ta arc-mini"
              rows="2"
              bind:value={arc.mini_ending}
              placeholder="Where this arc ends…"
            ></textarea>
            {#if arc.rationale}
              <p class="arc-rat">{arc.rationale}</p>
            {/if}
          </div>
        {/each}
      </div>

      <div class="acts">
        <button class="ghost sm" onclick={suggestArcs}>↻ Re-suggest</button>
        <span class="sp"></span>
        <button class="go" onclick={confirmArcs}>
          Confirm arc structure →
        </button>
      </div>
    {:else}
      <div class="arc-empty">No arcs returned — try re-suggesting.</div>
      <button class="ghost sm" onclick={suggestArcs}>↻ Re-suggest</button>
    {/if}
  {/if}
</div>

<style>
  /* ── Wrapper ──────────────────────────────────────────────────────────────── */
  .ws {
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px 16px;
  }

  /* ── Step indicator ──────────────────────────────────────────────────────── */
  .steps {
    display: flex;
    align-items: center;
    gap: 0;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-soft);
  }
  .step {
    display: flex;
    align-items: center;
    gap: 6px;
    opacity: .4;
    transition: opacity .15s;
  }
  .step.active { opacity: 1; }
  .step.done   { opacity: .7; }
  .snum {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    font-size: 9.5px;
    font-weight: 700;
    background: var(--elev);
    border: 1px solid var(--border-soft);
    color: var(--muted);
    flex: none;
  }
  .step.active .snum {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }
  .step.done .snum {
    background: rgba(109,140,255,.2);
    border-color: rgba(109,140,255,.35);
    color: var(--accent);
  }
  .slabel {
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    white-space: nowrap;
  }
  .step.active .slabel { color: var(--text); }
  .sep {
    flex: 1;
    height: 1px;
    background: var(--border-soft);
    margin: 0 8px;
    min-width: 16px;
    transition: background .15s;
  }
  .sep.active { background: rgba(109,140,255,.35); }

  /* ── Header ──────────────────────────────────────────────────────────────── */
  .wshead {
    display: flex;
    align-items: baseline;
    gap: 10px;
    flex-wrap: wrap;
  }
  .wstitle {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
  }
  .wsub {
    font-size: 12px;
    color: var(--muted);
    flex: 1;
  }
  .skip {
    font-size: 11px;
    color: var(--accent);
    background: none;
    border: none;
    box-shadow: none;
    padding: 0;
    cursor: pointer;
    text-decoration: underline;
    white-space: nowrap;
  }
  .skip:hover { filter: brightness(1.15); }

  /* ── Fields ──────────────────────────────────────────────────────────────── */
  .premise-row { display: flex; }
  .ending-row { display: flex; flex-direction: column; gap: 4px; }
  .el {
    font-size: 10.5px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: .3px;
    font-weight: 600;
  }

  .fld {
    width: 100%;
    padding: 8px 10px;
    font-size: 13px;
    border-radius: 8px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: inherit;
    box-sizing: border-box;
  }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.2)); }
  .ta { line-height: 1.5; resize: none; }
  .fld:disabled { opacity: .6; cursor: not-allowed; }

  /* ── Messages ────────────────────────────────────────────────────────────── */
  .msgs {
    min-height: 180px;
    max-height: 340px;
    overflow: auto;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 4px 2px;
  }
  .msg {
    display: flex;
    flex-direction: column;
    gap: 3px;
    max-width: 88%;
  }
  .msg.user      { align-self: flex-end;  align-items: flex-end; }
  .msg.assistant { align-self: flex-start; align-items: flex-start; }

  .mname {
    font-size: 10px;
    font-weight: 700;
    color: var(--accent);
    text-transform: uppercase;
    letter-spacing: .3px;
    padding: 0 4px;
  }
  .mbubble {
    font-size: 13px;
    line-height: 1.6;
    color: var(--text);
    padding: 9px 12px;
    border-radius: 11px;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .msg.user .mbubble {
    background: rgba(109,140,255,.18);
    border: 1px solid rgba(109,140,255,.28);
  }
  .msg.assistant .mbubble {
    background: var(--elev);
    border: 1px solid var(--border-soft);
  }

  /* Typing animation */
  .typing { display: inline-flex; align-items: center; gap: 4px; height: 18px; }
  .typing span {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--muted);
    animation: blink 1.1s ease-in-out infinite;
  }
  .typing span:nth-child(2) { animation-delay: .18s; }
  .typing span:nth-child(3) { animation-delay: .36s; }
  @keyframes blink { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }

  .merr {
    font-size: 12.5px;
    color: var(--bad, #ff7a7a);
    padding: 6px 8px;
    background: rgba(255,122,122,.08);
    border-radius: 7px;
    border: 1px solid rgba(255,122,122,.18);
  }

  /* ── Input row ───────────────────────────────────────────────────────────── */
  .inputrow { display: flex; gap: 8px; align-items: flex-end; }
  .inputrow .fld { flex: 1; }
  .send {
    width: 36px; height: 36px; flex: none;
    padding: 0; border-radius: 9px;
    font-size: 16px; font-weight: 700;
    background: var(--accent); border: 0; color: #fff;
    cursor: pointer;
    display: grid; place-items: center;
    align-self: flex-end;
  }
  .send:hover:not(:disabled) { filter: brightness(1.1); }
  .send:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Actions bar ─────────────────────────────────────────────────────────── */
  .acts { display: flex; align-items: center; gap: 8px; }
  .sp { flex: 1; }
  .go {
    font-size: 13px; font-weight: 700;
    padding: 8px 18px; border-radius: 9px;
    background: var(--accent); border: 0; color: #fff; cursor: pointer;
  }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Phase 2: ending callout ─────────────────────────────────────────────── */
  .ending-callout {
    display: flex;
    flex-direction: column;
    gap: 6px;
    background: rgba(255,200,80,.07);
    border: 1px solid rgba(255,200,80,.2);
    border-radius: 9px;
    padding: 10px 12px;
  }
  .ec-label {
    font-size: 11px;
    font-weight: 700;
    color: rgba(255,200,80,.9);
    text-transform: uppercase;
    letter-spacing: .4px;
  }
  .ec-ta { background: rgba(255,255,255,.04); }

  /* ── Phase 2: arc loading ────────────────────────────────────────────────── */
  .arc-loading {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 20px 0;
    justify-content: center;
    color: var(--muted);
    font-size: 13px;
  }
  .arc-loading-txt { color: var(--muted); }

  .arc-empty {
    font-size: 13px;
    color: var(--muted);
    padding: 14px 0;
    text-align: center;
  }

  /* ── Arc cards ───────────────────────────────────────────────────────────── */
  .arc-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .arc-card {
    border: 1px solid var(--border-soft);
    border-radius: 10px;
    padding: 12px 14px;
    background: var(--elev);
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .arc-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .arc-num {
    width: 20px; height: 20px; flex: none;
    border-radius: 50%;
    display: grid; place-items: center;
    font-size: 10px; font-weight: 700;
    background: var(--accent);
    color: #0b0e14;
  }
  .arc-name {
    flex: 1;
    padding: 5px 8px;
    font-size: 13px;
    font-weight: 700;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: var(--text);
    min-width: 0;
  }
  .arc-name:hover { border-color: var(--border-soft); }
  .arc-name:focus { border-color: var(--accent); outline: none; background: var(--bg); }
  .arc-mini { font-size: 12.5px; }

  .arc-cast {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .chip {
    font-size: 10.5px;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .df {
    background: rgba(109,140,255,.14);
    border: 1px solid rgba(109,140,255,.28);
    color: var(--accent);
    font-weight: 600;
  }
  .cast-chip {
    background: var(--elev);
    border: 1px solid var(--border-soft);
    color: var(--muted);
  }

  .arc-rat {
    margin: 0;
    font-size: 12px;
    color: var(--faint);
    font-style: italic;
    line-height: 1.45;
  }

  /* ── Spinner ─────────────────────────────────────────────────────────────── */
  .spin {
    width: 14px; height: 14px; flex: none;
    border-radius: 50%;
    border: 2px solid rgba(109,140,255,.3);
    border-top-color: var(--accent);
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

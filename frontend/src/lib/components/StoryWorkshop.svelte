<script>
  // Conversational story planning step that appears BEFORE full storyboard generation.
  // An opening message is auto-sent to the /api/stories/workshop endpoint when mounted.
  // Each exchange appends to `messages` and calls the endpoint with full history.
  // "Generate Full Story" takes the last assistant message as the premise seed.
  import { onMount } from 'svelte';

  let {
    character = '',       // character key
    charName = '',
    onGenerate = null,    // callback(premise: string) — user is ready to generate
    onSkip = null,        // callback() — bypass workshop
  } = $props();

  // Chat state
  let messages   = $state([]);  // [{role:'user'|'assistant', content:string}]
  let userInput  = $state('');
  let premise    = $state('');  // optional seed at the top
  let busy       = $state(false);
  let err        = $state(null);
  let msgBox;

  function scrollBottom() {
    queueMicrotask(() => { if (msgBox) msgBox.scrollTop = msgBox.scrollHeight; });
  }

  // Start streaming a response from the workshop endpoint.
  async function callWorkshop(msgs) {
    busy = true;
    err  = null;

    // Add a placeholder assistant message we'll fill in as tokens arrive.
    messages = [...msgs, { role: 'assistant', content: '' }];
    const assistantIdx = messages.length - 1;

    try {
      const res = await fetch('/api/stories/workshop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character,
          messages: msgs,          // send history without the placeholder
          premise: premise.trim(),
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        err = data?.error || `HTTP ${res.status}`;
        // Remove the empty placeholder on error.
        messages = messages.slice(0, assistantIdx);
        return;
      }

      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';

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
            messages[assistantIdx] = { ...messages[assistantIdx], content: messages[assistantIdx].content + ev.text };
            messages = [...messages];
            scrollBottom();
          } else if (ev.type === 'error') {
            err = ev.error;
          }
        }
      }
    } catch (e) {
      err = String(e);
      messages = messages.slice(0, assistantIdx);
    } finally {
      busy = false;
    }
  }

  // Send the user's typed message.
  async function send() {
    const text = userInput.trim();
    if (!text || busy) return;
    userInput = '';
    const nextMsgs = [...messages, { role: 'user', content: text }];
    await callWorkshop(nextMsgs);
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

  // Auto-call on mount to get the opening concept proposal.
  onMount(() => { callWorkshop([]); });

  // "Generate" uses the last assistant message as the premise seed (if any).
  function generate() {
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant');
    const seed = lastAssistant?.content?.trim() || premise.trim();
    onGenerate?.(seed);
  }

  let hasExchange = $derived(messages.some((m) => m.role === 'user'));
</script>

<div class="ws">
  <div class="wshead">
    <span class="wstitle">Story Workshop</span>
    <span class="wsub">Collaborative planning for {charName || character}</span>
    {#if onSkip}
      <button class="skip" onclick={onSkip}>Skip to generate →</button>
    {/if}
  </div>

  <!-- Optional premise seed -->
  <div class="premise-row">
    <input class="fld" bind:value={premise} placeholder="What's the story about? (optional seed — guides the workshop)" />
  </div>

  <!-- Chat message list -->
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
      placeholder="Your thoughts, directions, changes… (Enter to send)"
    ></textarea>
    <button class="send" onclick={send} disabled={busy || !userInput.trim()}>
      {busy ? '…' : '↑'}
    </button>
  </div>

  <!-- Actions -->
  <div class="acts">
    {#if onSkip}
      <button class="ghost sm" onclick={onSkip}>Skip workshop</button>
    {/if}
    <span class="sp"></span>
    <button class="go" onclick={generate} disabled={busy || !hasExchange}>
      Generate Full Story →
    </button>
  </div>
</div>

<style>
  .ws {
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px 16px;
  }

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

  .premise-row { display: flex; }
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

  /* Message list */
  .msgs {
    min-height: 180px;
    max-height: 360px;
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
  .msg.user {
    align-self: flex-end;
    align-items: flex-end;
  }
  .msg.assistant {
    align-self: flex-start;
    align-items: flex-start;
  }

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
    background: rgba(109, 140, 255, .18);
    border: 1px solid rgba(109, 140, 255, .28);
  }
  .msg.assistant .mbubble {
    background: var(--elev);
    border: 1px solid var(--border-soft);
  }

  /* Typing animation (3 dots) */
  .typing {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 18px;
  }
  .typing span {
    width: 6px;
    height: 6px;
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

  /* Input row */
  .inputrow {
    display: flex;
    gap: 8px;
    align-items: flex-end;
  }
  .inputrow .fld { flex: 1; }

  .send {
    width: 36px;
    height: 36px;
    flex: none;
    padding: 0;
    border-radius: 9px;
    font-size: 16px;
    font-weight: 700;
    background: var(--accent);
    border: 0;
    color: #fff;
    cursor: pointer;
    display: grid;
    place-items: center;
    align-self: flex-end;
  }
  .send:hover:not(:disabled) { filter: brightness(1.1); }
  .send:disabled { opacity: .4; cursor: not-allowed; }

  /* Actions bar */
  .acts {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sp { flex: 1; }
  .go {
    font-size: 13px;
    font-weight: 700;
    padding: 8px 18px;
    border-radius: 9px;
    background: var(--accent);
    border: 0;
    color: #fff;
    cursor: pointer;
  }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: .4; cursor: not-allowed; }
</style>

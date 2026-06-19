<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';

  let busy = $state(false);
  let illustrating = $state(null); // index of the message currently being illustrated
  let chars = $state([]);
  let logEl = $state();
  const chat = app.chat; // { messages, input, demoTurn, seededFor } — persists across routes

  let connected = $derived(!!(app.health?.active_chat_model || app.conns.active?.text));
  let activeChat = $derived(app.health?.active_chat_model || app.conns.active?.text || '(none — connect)');

  let activeCharObj = $derived(chars.find((c) => c.key === app.activeChar) || null);
  let charName = $derived(activeCharObj?.name || (app.activeChar || 'Assistant'));
  let persona = $derived(app.personas.find((p) => p.id === app.activePersona) || app.personas[0] || { name: 'You' });

  onMount(async () => { chars = await get('/characters'); });

  // Seed the conversation with the character's first message (greeting). Reseed
  // when the selected character changes, and once its data arrives from the API.
  $effect(() => {
    const key = app.activeChar;
    const c = chars.find((x) => x.key === key) || null;
    const stamp = `${key}|${c ? 'loaded' : 'none'}`;
    if (stamp !== chat.seededFor) {
      chat.seededFor = stamp;
      chat.messages = c?.greeting ? [{ role: 'bot', text: c.greeting, images: [] }] : [];
    }
  });

  async function scrollDown() {
    await Promise.resolve();
    if (logEl) logEl.scrollTop = logEl.scrollHeight;
  }

  // Prototype reply when no chat model is connected — so the back-and-forth is
  // visible without provider setup. Clearly a stand-in, not a real model.
  const DEMO = [
    (t) => `*${charName} considers your words.* "${t}" — go on, I'm listening.`,
    (t) => `"${t}?" *${charName} tilts their head, intrigued.* Tell me more, ${persona.name}.`,
    (t) => `*A thoughtful pause.* "I hear you, ${persona.name}." ${charName} leans in.`,
    () => `*${charName} nods slowly, weighing what you've said.*`
  ];
  function demoReply(text) {
    const line = DEMO[chat.demoTurn % DEMO.length](text);
    chat.demoTurn++;
    return `${line}\n\n(prototype reply — connect a chat model in TextGen for real responses)`;
  }

  async function send() {
    const text = chat.input.trim();
    if (!text || busy) return;
    chat.input = '';
    chat.messages = [...chat.messages, { role: 'user', text }];
    const idx = chat.messages.length;
    chat.messages = [...chat.messages, { role: 'bot', text: '…', images: [] }];
    busy = true;
    scrollDown();

    let reply;
    if (connected) {
      const r = await post('/run', {
        pipeline: app.activePipeline,
        character: app.activeChar || null,
        chat_model: app.conns.active?.text || null,
        image_model: app.activeImage || null,
        message: text
      });
      reply = r.data?.error
        ? { role: 'bot', text: r.data.error, images: [], error: true }
        : { role: 'bot', text: r.data?.reply || '(no reply)', images: r.data?.images || [] };
    } else {
      await new Promise((res) => setTimeout(res, 450)); // let the "…" breathe
      reply = { role: 'bot', text: demoReply(text), images: [], demo: true };
    }

    busy = false;
    chat.messages = chat.messages.map((m, i) => (i === idx ? reply : m));
    scrollDown();
  }

  // Generate an image from an existing turn (the "illustrate" pipeline: prompt-gen
  // describes the scene text, the image model renders it) and append it to that
  // message. Needs a text connection (for prompt-gen) and an image model.
  async function illustrate(idx) {
    if (illustrating !== null) return;
    const m = chat.messages[idx];
    if (!m?.text) return;
    if (!connected) {
      chat.messages = chat.messages.map((x, i) => (i === idx ? { ...x, imgError: 'Connect a chat model first (Prompt Gen needs it).' } : x));
      return;
    }
    illustrating = idx;
    chat.messages = chat.messages.map((x, i) => (i === idx ? { ...x, imgError: null } : x));
    const r = await post('/run', {
      pipeline: 'illustrate',
      character: app.activeChar || null,
      chat_model: app.conns.active?.text || null,
      image_model: app.activeImage || null,
      message: m.text,
      use_reference: true   // img2img from the character's reference when the workflow supports it
    });
    illustrating = null;
    chat.messages = chat.messages.map((x, i) => {
      if (i !== idx) return x;
      if (r.data?.error) return { ...x, imgError: r.data.error };
      const imgs = r.data?.images || [];
      return imgs.length ? { ...x, images: [...(x.images || []), ...imgs], imgError: null }
                         : { ...x, imgError: 'No image returned.' };
    });
    scrollDown();
  }

  function resetChat() {
    chat.messages = activeCharObj?.greeting ? [{ role: 'bot', text: activeCharObj.greeting, images: [] }] : [];
  }

  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }
</script>

<div class="controls">
  <button class="bar-btn" onclick={resetChat} title="Clear this conversation and start fresh">↺ New chat</button>
  <a class="bar-btn" href="/settings/models/chat" title="Language model, connections & system prompt — in Settings">⚙ Generation</a>
</div>

<div class="log" bind:this={logEl}>
  {#if chat.messages.length === 0}
    <div class="empty">
      {#if activeCharObj?.avatar}<img class="emimg" src={activeCharObj.avatar} alt="" />{:else}<div class="emark"></div>{/if}
      <p>Chat with {charName}</p>
      <span class="hint">
        {#if connected}Running on <b>{activeChat}</b>.{:else}No model connected — replies are a local prototype. Set one up in <a href="/settings/models">Settings ▸ Generation</a>.{/if}
      </span>
    </div>
  {/if}
  {#each chat.messages as m, i}
    <div class="msg {m.role}">
      <div class="avatar {m.role}">
        {#if m.role === 'bot' && activeCharObj?.avatar}
          <img src={activeCharObj.avatar} alt="" />
        {:else}
          {m.role === 'user' ? (persona.name?.[0] || 'Y').toUpperCase() : (charName?.[0] || 'L').toUpperCase()}
        {/if}
      </div>
      <div class="bubble" class:error={m.error} class:demo={m.demo}>
        {m.text}
        {#each m.images || [] as src}
          <div class="cimg"><ZoomImage {src} caption={m.text} inline /></div>
        {/each}
        {#if m.imgError}<div class="imgerr">{m.imgError}</div>{/if}
        {#if m.role === 'bot' && !m.demo && m.text && m.text !== '…'}
          <button class="illus" title="Generate an image from this turn"
            onclick={() => illustrate(i)} disabled={illustrating !== null}>
            {illustrating === i ? '⏳ illustrating…' : '🖼 Illustrate'}
          </button>
        {/if}
      </div>
    </div>
  {/each}
</div>

<div class="composer">
  <textarea bind:value={chat.input} onkeydown={onKey}
    placeholder={`Message ${charName}…  (Enter to send, Shift+Enter for newline)`}></textarea>
  <button onclick={send} disabled={busy}>Send</button>
</div>

<style>
  .controls {
    padding: 10px 18px; border-bottom: 1px solid var(--border-soft);
    display: flex; gap: 8px; align-items: center; justify-content: flex-end; background: var(--panel);
  }
  .bar-btn {
    font-size: 12.5px; font-weight: 600; padding: 5px 12px;
    border-radius: 999px; box-shadow: none; border: 1px solid var(--border);
    background: var(--elev); color: var(--muted);
    text-decoration: none; display: inline-flex; align-items: center; cursor: pointer;
  }
  .bar-btn:hover { color: var(--text); filter: none; background: var(--elev-2); }
  .bar-btn.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1px var(--accent); }

  .log { flex: 1; overflow: auto; padding: 22px; display: flex; flex-direction: column; gap: 16px; }
  .msg { display: flex; gap: 10px; max-width: 760px; }
  .msg.user { align-self: flex-end; flex-direction: row-reverse; }
  .avatar {
    width: 32px; height: 32px; border-radius: 9px; flex: none; display: grid; place-items: center;
    font-size: 13px; font-weight: 700; color: #fff; overflow: hidden;
  }
  .avatar img { width: 100%; height: 100%; object-fit: cover; }
  .avatar.bot { background: linear-gradient(135deg, #6d8cff, #9a6dff); }
  .avatar.user { background: var(--elev-2); color: var(--muted); border: 1px solid var(--border); }
  .bubble {
    padding: 11px 15px; border-radius: 13px; white-space: pre-wrap;
    background: var(--elev); border: 1px solid var(--border-soft);
  }
  .msg.user .bubble { background: linear-gradient(180deg, var(--accent), var(--accent-600)); color: #fff; border: 0; }
  .bubble.error { color: var(--bad); border-color: rgba(255,122,122,.3); }
  .bubble.demo { border-style: dashed; }
  .cimg { max-width: 380px; border-radius: 11px; overflow: hidden; margin-top: 9px; }
  .imgerr { font-size: 12px; color: var(--bad); margin-top: 8px; }
  .illus {
    margin-top: 10px; font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: 999px;
    box-shadow: none; border: 1px solid var(--border); background: var(--elev); color: var(--muted);
  }
  .illus:hover:not(:disabled) { color: var(--text); filter: none; background: var(--elev-2); }
  .illus:disabled { opacity: .6; cursor: default; }
  .msg.user .illus { display: none; }

  .empty { margin: auto; text-align: center; color: var(--muted); }
  .empty p { margin: 14px 0 6px; color: var(--text); font-size: 16px; }
  .emimg { width: 72px; height: 72px; border-radius: 16px; object-fit: cover; margin: 0 auto; display: block; border: 1px solid var(--border); }
  .emark {
    width: 38px; height: 38px; margin: 0 auto; border-radius: 11px; transform: rotate(45deg);
    background: linear-gradient(135deg, var(--accent), #9a6dff); opacity: .85;
    box-shadow: 0 0 26px var(--accent-glow);
  }

  .composer { display: flex; gap: 10px; padding: 16px 18px; border-top: 1px solid var(--border-soft); }
  textarea { flex: 1; resize: none; height: 54px; }
</style>

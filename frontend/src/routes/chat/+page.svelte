<script>
  // The main chat — the standalone roleplay surface and control center. It runs with
  // the active character + persona and the active chat config, streaming each turn from
  // /api/chat. The ⚙ opens the shared ConfigModal (models, connections, configs,
  // lorebooks) so every knob lives at the point of use, not on a settings page.
  import { onMount, tick } from 'svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { chars, loadChars } from '$lib/characters.svelte.js';
  import { openConfigModal } from '$lib/configModal.svelte.js';

  let active = $derived(chars.list.find((c) => c.key === app.activeChar) || null);
  let persona = $derived(app.personas.find((p) => p.id === app.activePersona) || null);

  // Conversation lives in the global store so it survives navigation. Reference
  // app.chat.messages directly (a $derived value would be read-only — can't push).
  let input = $state('');
  let busy = $state(false);
  let err = $state(null);
  let abortCtl = null;
  let msgBox;

  // Active chat config (label in the header) + this thread's attached lorebooks.
  let cfgLib = $state({ active: 'default', configs: [] });
  let activeCfg = $derived(cfgLib.configs.find((c) => c.id === cfgLib.active) || null);
  let lorebooks = $state([]);   // override for this thread; seeded from the config's defaults

  async function loadCfg() {
    try {
      cfgLib = await get('/chat-configs');
      // Seed thread lorebooks from the active config unless the user already set some.
      if (!lorebooks.length) lorebooks = [...(activeCfg?.lorebooks || [])];
    } catch { /* no configs */ }
  }

  onMount(async () => { if (!chars.list.length) await loadChars(); await loadCfg(); scrollBottom(); });

  function scrollBottom() { tick().then(() => { if (msgBox) msgBox.scrollTop = msgBox.scrollHeight; }); }

  function openCfg(tab = 'configs') {
    openConfigModal({
      tab,
      lorebooks,
      onLorebooks: (v) => { lorebooks = v; },
    });
  }

  async function stream(history) {
    busy = true; err = null;
    const msgs = app.chat.messages;
    // Push then mutate by index: Svelte proxies array elements on insert, so the
    // stored proxy (not a local literal) is what the DOM binds to.
    msgs.push({ role: 'assistant', content: '' });
    const ai = msgs.length - 1;
    scrollBottom();
    abortCtl = new AbortController();
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          history,
          character: app.activeChar || undefined,
          persona: persona ? { name: persona.name, description: persona.description || '' } : undefined,
          lorebooks,
        }),
        signal: abortCtl.signal,
      });
      if (!res.ok || !res.body) {
        let msg = 'chat error';
        try { msg = (await res.json())?.error || msg; } catch { /* stream */ }
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, idx); buf = buf.slice(idx + 2);
          if (!line.startsWith('data: ')) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === 'delta') { msgs[ai].content += ev.text; scrollBottom(); }
          else if (ev.type === 'error') { err = ev.error; }
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') err = e.message || 'chat failed';
    }
    busy = false;
    abortCtl = null;
    if (!msgs[ai]?.content && !err) msgs.splice(ai, 1);   // empty turn — drop the placeholder
  }

  async function send() {
    const t = input.trim();
    if (!t || busy) return;
    input = '';
    app.chat.messages.push({ role: 'user', content: t });
    await stream(app.chat.messages.filter((m) => m.role !== 'assistant' || m.content));
  }
  function stop() { abortCtl?.abort(); }
  function reset() { app.chat.messages = []; err = null; }
  function onKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }
</script>

<div class="chat">
  <div class="bar">
    <div class="who">
      {#if active?.avatar}<img class="av" src={active.avatar} alt={active.name} />{:else}<div class="av ph">{(active?.name?.[0] || '∅')}</div>{/if}
      <div class="whometa">
        <span class="cname">{active?.name || 'No character'}</span>
        <span class="csub">{persona ? `as ${persona.name}` : 'neutral persona'}{activeCfg ? ` · ${activeCfg.name}` : ''}</span>
      </div>
    </div>
    <div class="tools">
      <button class="ghost sm" onclick={() => openCfg('lorebooks')} title="Lorebooks for this chat">📚 {lorebooks.length || ''}</button>
      <button class="ghost sm" onclick={reset} title="Clear conversation">↺</button>
      <button class="gear" onclick={() => openCfg('configs')} title="Models, configs & connections">⚙</button>
    </div>
  </div>

  <div class="msgs" bind:this={msgBox}>
    {#if !app.chat.messages.length}
      <div class="empty">
        <p>Start chatting{active ? ` with ${active.name}` : ''}.</p>
        <span>Pick a character in <a href="/characters/search">Characters</a>, tune the model &amp; prompt in <button class="link" onclick={() => openCfg('configs')}>⚙ config</button>.</span>
      </div>
    {/if}
    {#each app.chat.messages as m, i (i)}
      <div class="msg" class:user={m.role === 'user'} class:assistant={m.role === 'assistant'}>
        <div class="bubble">
          {#if !m.content && busy && i === app.chat.messages.length - 1}
            <span class="typing"><span></span><span></span><span></span></span>
          {:else}{m.content}{/if}
        </div>
      </div>
    {/each}
    {#if err}<div class="merr">⚠ {err}</div>{/if}
  </div>

  <div class="inputrow">
    <textarea class="say" rows="1" placeholder="Message…  (Enter to send, Shift+Enter for newline)"
      bind:value={input} onkeydown={onKey} disabled={busy}></textarea>
    {#if busy}
      <button class="send stop" onclick={stop} title="Stop">■</button>
    {:else}
      <button class="send" onclick={send} disabled={!input.trim()}>↑</button>
    {/if}
  </div>
</div>

<style>
  .chat { flex: 1; min-height: 0; display: flex; flex-direction: column; max-width: 900px; width: 100%; margin: 0 auto; padding: 14px 16px; gap: 10px; }
  .bar { display: flex; align-items: center; gap: 12px; padding-bottom: 10px; border-bottom: 1px solid var(--border-soft); }
  .who { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
  .av { width: 38px; height: 38px; border-radius: 10px; object-fit: cover; border: 1px solid var(--border); flex: none; }
  .av.ph { display: grid; place-items: center; font-size: 18px; font-weight: 700; color: #fff; background: linear-gradient(135deg, var(--accent), #9a6dff); }
  .whometa { display: flex; flex-direction: column; min-width: 0; }
  .cname { font-size: 14px; font-weight: 700; color: var(--text); }
  .csub { font-size: 11.5px; color: var(--muted); }
  .tools { display: flex; align-items: center; gap: 6px; }
  .gear { width: 32px; height: 32px; display: grid; place-items: center; padding: 0; font-size: 15px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); color: var(--muted); cursor: pointer; }
  .gear:hover { color: var(--accent); border-color: var(--accent); }

  .msgs { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding: 6px 2px; }
  .empty { margin: auto; text-align: center; color: var(--muted); }
  .empty p { margin: 0 0 4px; color: var(--text); font-size: 15px; }
  .empty a, .link { color: var(--accent); }
  .link { background: none; border: 0; box-shadow: none; cursor: pointer; padding: 0; font: inherit; }

  .msg { display: flex; max-width: 86%; }
  .msg.user { align-self: flex-end; }
  .msg.assistant { align-self: flex-start; }
  .bubble { font-size: 14px; line-height: 1.6; color: var(--text); padding: 10px 13px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user .bubble { background: rgba(109,140,255,.18); border: 1px solid rgba(109,140,255,.28); }
  .msg.assistant .bubble { background: var(--elev); border: 1px solid var(--border-soft); }
  .merr { font-size: 12.5px; color: var(--bad); padding: 6px 10px; background: rgba(255,122,122,.08); border: 1px solid rgba(255,122,122,.2); border-radius: 8px; }

  .typing { display: inline-flex; gap: 4px; height: 16px; align-items: center; }
  .typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.1s ease-in-out infinite; }
  .typing span:nth-child(2) { animation-delay: .18s; } .typing span:nth-child(3) { animation-delay: .36s; }
  @keyframes blink { 0%,100% { opacity: .25; } 50% { opacity: 1; } }

  .inputrow { display: flex; gap: 8px; align-items: flex-end; }
  .say { flex: 1; padding: 10px 13px; font-size: 14px; border-radius: 10px; background: var(--bg); border: 1px solid var(--border); color: var(--text); resize: none; line-height: 1.5; font-family: inherit; max-height: 40vh; }
  .say:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow); }
  .send { width: 40px; height: 40px; flex: none; padding: 0; border-radius: 10px; font-size: 17px; font-weight: 700; background: var(--accent); border: 0; color: #fff; cursor: pointer; display: grid; place-items: center; }
  .send:hover:not(:disabled) { filter: brightness(1.1); }
  .send:disabled { opacity: .4; cursor: not-allowed; }
  .send.stop { background: var(--bad); }
</style>

<script>
  // A reusable two-pane LLM workspace: chat on the left, an artifact canvas on the
  // right, with inline config (model, lorebooks, images) and a live context-budget
  // dial. The host owns the conversation + transport; this shell owns the UI.
  //
  // This is the standardized surface for *every* LLM interaction (the story
  // workshop is its first tenant) — model/prompt/lorebook config lives here, in
  // the same window as the chat, rather than scattered across the settings pages.
  import { openConfigModal } from '$lib/configModal.svelte.js';
  import { formatChat } from '$lib/chat-format.js';

  let {
    title = 'Console',
    subtitle = '',
    messages = [],            // [{ role, content }]
    busy = false,
    err = null,
    placeholder = 'Message…  (Enter to send)',
    assistantLabel = 'Assistant',
    retrievedLore = [],       // [{ id, title, facet }]
    usage = null,             // { tokens, window, parts:{ base, craft, world, history } }
    models = [],              // [{ value, label }]
    model = $bindable(''),    // selected model id ('' = use the stage default)
    lorebooks = $bindable([]),// assigned world-lorebook scopes (strings)
    onSend,                   // (text) => void
    onImage = null,           // optional () => void — image invocation hook
    right = null,             // snippet — the right-pane canvas
    actions = null,           // snippet — action buttons under the input
    aboveInput = null,        // snippet — optional field above the input
  } = $props();

  let userInput = $state('');
  let msgBox;

  // Context-budget dial maths.
  let pct = $derived(usage?.window ? Math.min(100, Math.round((usage.tokens / usage.window) * 100)) : 0);
  let dialClass = $derived(pct >= 90 ? 'crit' : pct >= 70 ? 'warn' : 'ok');
  const k = (n) => (n >= 1000 ? (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(n));
  function segPct(v) { return usage?.tokens ? (v / usage.tokens) * 100 : 0; }

  function scrollBottom() {
    queueMicrotask(() => { if (msgBox) msgBox.scrollTop = msgBox.scrollHeight; });
  }
  $effect(() => { messages.length; scrollBottom(); });

  function send() {
    const text = userInput.trim();
    if (!text || busy) return;
    userInput = '';
    onSend?.(text);
  }
  function onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  }

</script>

<div class="con">
  <!-- ── Header ─────────────────────────────────────────────────────────────── -->
  <div class="con-head">
    <div class="con-title">
      <span class="ct">{title}</span>
      {#if subtitle}<span class="cs">{subtitle}</span>{/if}
    </div>

    <div class="con-tools">
      {#if usage}
        <div class="dial {dialClass}" title={`Context: ${usage.tokens} / ${usage.window} tokens`}>
          <div class="dbar">
            <span class="seg base"  style="width:{segPct(usage.parts?.base || 0)}%"></span>
            <span class="seg craft" style="width:{segPct(usage.parts?.craft || 0)}%"></span>
            <span class="seg world" style="width:{segPct(usage.parts?.world || 0)}%"></span>
            <span class="seg hist"  style="width:{segPct(usage.parts?.history || 0)}%"></span>
          </div>
          <span class="dlabel">{k(usage.tokens)} / {k(usage.window)}</span>
        </div>
      {/if}
      {#if onImage}<button class="tool" onclick={onImage} title="Invoke image">🖼</button>{/if}
      <button class="gear" title="Model, configs, connections & lorebooks"
        onclick={() => openConfigModal({ tab: 'configs', lorebooks, onLorebooks: (v) => (lorebooks = v) })}>⚙</button>
    </div>
  </div>

  <!-- ── Two-pane body ──────────────────────────────────────────────────────── -->
  <div class="con-body">
    <!-- Chat column -->
    <div class="chat">
      <div class="msgs" bind:this={msgBox}>
        {#each messages as msg, i (i)}
          <div class="msg" class:user={msg.role === 'user'} class:assistant={msg.role === 'assistant'}>
            {#if msg.role === 'assistant'}<span class="mname">{assistantLabel}</span>{/if}
            <div class="mbubble">
              {#if !msg.content && busy && i === messages.length - 1}
                <span class="typing"><span></span><span></span><span></span></span>
              {:else}
                {@html formatChat(msg.content)}
              {/if}
            </div>
          </div>
        {/each}
        {#if err}<div class="merr">⚠ {err}</div>{/if}
      </div>

      {#if retrievedLore.length}
        <div class="lore-bar">
          <span class="lore-label">Lore</span>
          {#each retrievedLore as e}
            <span class="lore-chip" class:craft={e.facet} title={e.facet ? `craft · ${e.facet}` : e.id}>{e.title || e.id}</span>
          {/each}
        </div>
      {/if}

      {#if aboveInput}{@render aboveInput()}{/if}

      <div class="inputrow">
        <textarea class="fld ta" rows="2" bind:value={userInput} onkeydown={onKey}
          disabled={busy} {placeholder}></textarea>
        <button class="send" onclick={send} disabled={busy || !userInput.trim()}>{busy ? '…' : '↑'}</button>
      </div>

      {#if actions}<div class="acts">{@render actions()}</div>{/if}
    </div>

    <!-- Right canvas -->
    <div class="canvas">
      {#if right}{@render right()}{:else}<div class="canvas-empty">No canvas.</div>{/if}
    </div>
  </div>
</div>

<style>
  .con {
    display: flex;
    flex-direction: column;
    gap: 10px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 14px 14px;
  }

  /* Header */
  .con-head { display: flex; align-items: center; gap: 12px; }
  .con-title { display: flex; align-items: baseline; gap: 9px; flex: 1; min-width: 0; }
  .ct { font-size: 15px; font-weight: 700; color: var(--text); }
  .cs { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .con-tools { display: flex; align-items: center; gap: 10px; flex: none; }
  .gear {
    width: 30px; height: 30px; flex: none; padding: 0; border-radius: 8px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted);
    font-size: 14px; cursor: pointer; display: grid; place-items: center;
  }
  .gear:hover { color: var(--text); }
  .gear.on { color: var(--accent); border-color: var(--accent); background: rgba(109,140,255,.12); }

  /* Context dial */
  .dial { display: flex; align-items: center; gap: 7px; }
  .dbar {
    width: 88px; height: 7px; border-radius: 999px; overflow: hidden;
    background: var(--elev); border: 1px solid var(--border-soft); display: flex;
  }
  .seg { height: 100%; }
  .seg.base  { background: rgba(150,160,190,.55); }
  .seg.craft { background: rgba(100,210,130,.7); }
  .seg.world { background: rgba(255,200,90,.7); }
  .seg.hist  { background: rgba(109,140,255,.75); }
  .dlabel { font-size: 10.5px; font-variant-numeric: tabular-nums; color: var(--muted); white-space: nowrap; }
  .dial.warn .dlabel { color: rgba(255,200,90,.95); }
  .dial.crit .dlabel { color: var(--bad, #ff7a7a); }

  /* Config drawer */
  .cfg {
    display: flex; flex-direction: column; gap: 8px;
    padding: 10px 12px; border-radius: 9px;
    background: var(--bg); border: 1px solid var(--border-soft);
  }
  .cfg-row { display: flex; align-items: center; gap: 10px; }
  .cfg-lbl { width: 78px; flex: none; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .cfg-ctl { flex: 1; min-width: 0; }
  .cfg-ctl.chips { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .cfg-ctl.tools { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .scope {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 11px; padding: 2px 4px 2px 9px; border-radius: 999px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--text);
  }
  .scope.locked { padding: 2px 9px; color: rgba(100,210,130,.9); border-color: rgba(100,210,130,.25); background: rgba(100,210,130,.08); }
  .rm { background: none; border: 0; color: var(--faint); cursor: pointer; font-size: 10px; padding: 0 2px; }
  .rm:hover { color: var(--bad, #ff7a7a); }
  .scope-add {
    width: 80px; font-size: 11px; padding: 3px 8px; border-radius: 999px;
    background: var(--bg); border: 1px dashed var(--border); color: var(--text);
  }
  .scope-add:focus { outline: none; border-color: var(--accent); border-style: solid; }
  .tool { font-size: 12px; padding: 5px 12px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; }
  .tool:hover { border-color: var(--accent); color: var(--accent); }

  /* Two-pane body */
  .con-body { display: flex; gap: 12px; align-items: stretch; }
  .chat { flex: 1 1 50%; min-width: 0; display: flex; flex-direction: column; gap: 9px; }
  .canvas {
    flex: 1 1 50%; min-width: 0;
    border: 1px solid var(--border-soft); border-radius: 10px;
    background: var(--bg); overflow: auto;
    max-height: 62vh;
  }
  .canvas-empty { color: var(--faint); font-size: 12px; padding: 20px; text-align: center; }

  /* Messages */
  .msgs { flex: 1; min-height: 240px; max-height: 56vh; overflow: auto; display: flex; flex-direction: column; gap: 10px; padding: 4px 2px; }
  .msg { display: flex; flex-direction: column; gap: 3px; max-width: 90%; }
  .msg.user { align-self: flex-end; align-items: flex-end; }
  .msg.assistant { align-self: flex-start; align-items: flex-start; }
  .mname { font-size: 10px; font-weight: 700; color: var(--accent); text-transform: uppercase; letter-spacing: .3px; padding: 0 4px; }
  .mbubble { font-size: 13px; line-height: 1.6; color: var(--text); padding: 9px 12px; border-radius: 11px; white-space: pre-wrap; word-break: break-word; }
  .msg.user .mbubble { background: rgba(109,140,255,.18); border: 1px solid rgba(109,140,255,.28); }
  .msg.assistant .mbubble { background: var(--elev); border: 1px solid var(--border-soft); }

  .typing { display: inline-flex; align-items: center; gap: 4px; height: 18px; }
  .typing span { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.1s ease-in-out infinite; }
  .typing span:nth-child(2) { animation-delay: .18s; }
  .typing span:nth-child(3) { animation-delay: .36s; }
  @keyframes blink { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }

  .merr { font-size: 12.5px; color: var(--bad, #ff7a7a); padding: 6px 8px; background: rgba(255,122,122,.08); border-radius: 7px; border: 1px solid rgba(255,122,122,.18); }

  /* Lore bar */
  .lore-bar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 2px; }
  .lore-label { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint, #555b78); flex: none; }
  .lore-chip { font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); white-space: nowrap; }
  .lore-chip.craft { background: rgba(100,210,130,.1); border-color: rgba(100,210,130,.22); color: rgba(100,210,130,.85); }

  /* Input */
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: inherit; box-sizing: border-box; }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.2)); }
  .ta { line-height: 1.5; resize: none; }
  .fld:disabled { opacity: .6; cursor: not-allowed; }
  .inputrow { display: flex; gap: 8px; align-items: flex-end; }
  .inputrow .fld { flex: 1; }
  .send { width: 36px; height: 36px; flex: none; padding: 0; border-radius: 9px; font-size: 16px; font-weight: 700; background: var(--accent); border: 0; color: #fff; cursor: pointer; display: grid; place-items: center; }
  .send:hover:not(:disabled) { filter: brightness(1.1); }
  .send:disabled { opacity: .4; cursor: not-allowed; }

  .acts { display: flex; align-items: center; gap: 8px; }

  @media (max-width: 860px) {
    .con-body { flex-direction: column; }
    .canvas { max-height: 40vh; }
  }
</style>

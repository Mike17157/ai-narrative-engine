<script>
  // The SECTION EDITOR — a left-docked agent scoped to ONE section (the active tab's layer). You
  // talk; it proposes TARGETED ops (set a field, merge one key, upsert/remove one item) that each
  // touch a single part of the section's JSON — never a whole-doc rewrite. Ops show as approve
  // cards; approving applies via /card/{layer}/ops and reloads the story so the tab reflects it.
  import { post } from '$lib/api.js';
  import { loadStory } from '$lib/stories.svelte.js';

  let { storyKey, layer, layerLabel = '' } = $props();

  let convo = $state([]);      // [{role, content, ops?}]
  let typed = $state('');
  let busy = $state(false);
  let err = $state('');
  let scroller;
  const scroll = () => requestAnimationFrame(() => { if (scroller) scroller.scrollTop = scroller.scrollHeight; });

  // Switching sections starts a fresh conversation (the editor is scoped to one layer).
  let seen = layer;
  $effect(() => { if (layer !== seen) { seen = layer; convo = []; err = ''; } });

  async function send() {
    const t = typed.trim(); if (!t || busy) return;
    typed = ''; busy = true; err = '';
    convo = [...convo, { role: 'user', content: t }]; scroll();
    const messages = convo.filter((m) => m.role).map((m) => ({ role: m.role, text: m.content }));
    const r = await post(`/stories/${storyKey}/card/${layer}/chat`, { messages });
    busy = false;
    if (r.ok) {
      convo = [...convo, { role: 'assistant', content: r.data.reply || 'Okay.',
                           ops: (r.data.ops || []).map((o) => ({ ...o })) }];
      scroll();
    } else err = r.data?.error || 'editor failed';
  }
  async function apply(op) {
    if (op.done || busy) return;
    busy = true;
    const r = await post(`/stories/${storyKey}/card/${layer}/ops`, { ops: [op] });
    busy = false;
    if (r.ok && r.data.applied?.length) {
      op.done = true; convo = [...convo];
      await loadStory(storyKey);
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } else err = r.data?.error || 'could not apply';
  }
  const dismiss = (op) => { op.gone = true; convo = [...convo]; };
  const opText = (o) => o.summary
    || `${o.op} ${o.field}${o.key ? '.' + o.key : ''}${o.id ? ' · ' + o.id : ''}`;
</script>

<aside class="schat">
  <div class="sh"><span class="dot"></span>Editing <b>{layerLabel || layer}</b></div>
  <div class="log" bind:this={scroller}>
    {#if !convo.length}
      <div class="hint">Tell the editor what to change in this section — “make the tone wryer”,
        “rename the arc to Paper Lanterns”, “give Mara a rival named Toll”. It proposes small,
        targeted edits you approve one by one.</div>
    {/if}
    {#each convo as m, i (i)}
      <div class="msg {m.role}">{m.content}</div>
      {#each (m.ops || []) as op, j (j)}
        {#if !op.gone}
          <div class="op" class:done={op.done}>
            <span class="opl">{opText(op)}</span>
            {#if op.done}<span class="ok">✓ applied</span>
            {:else}
              <button class="app" onclick={() => apply(op)}>Apply</button>
              <button class="dis" onclick={() => dismiss(op)} aria-label="Dismiss">✕</button>
            {/if}
          </div>
        {/if}
      {/each}
    {/each}
    {#if busy}<div class="msg assistant pending"><span class="dots"><i></i><i></i><i></i></span></div>{/if}
    {#if err}<div class="err">⚠ {err}</div>{/if}
  </div>
  <div class="foot">
    <input bind:value={typed} placeholder="Message the section editor…"
           onkeydown={(e) => e.key === 'Enter' && send()} disabled={busy} />
    <button class="send" onclick={send} disabled={busy || !typed.trim()} aria-label="Send">↳</button>
  </div>
</aside>

<style>
  .schat { position: fixed; left: 0; top: var(--chrome-top, 86px); bottom: 0; width: 320px; z-index: 40;
           display: flex; flex-direction: column; background: var(--panel, #14161f);
           border-right: 1px solid var(--border, #2a2f44); }
  .sh { display: flex; align-items: center; gap: 7px; padding: 11px 13px; flex: none;
        border-bottom: 1px solid var(--border-soft); font-size: 12.5px; color: var(--muted); }
  .sh b { color: var(--text); text-transform: capitalize; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); flex: none; }
  .log { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .hint { color: var(--faint); line-height: 1.6; font-style: italic; font-size: 12.5px; }
  .msg { max-width: 90%; font-size: 13px; line-height: 1.45; padding: 8px 11px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: var(--accent, #6d8cff); color: #0b0e14; border-bottom-right-radius: 4px; }
  .msg.assistant { align-self: flex-start; background: var(--elev, #1b1e2b); color: var(--text); border: 1px solid var(--border-soft); border-bottom-left-radius: 4px; }
  .op { align-self: flex-start; max-width: 92%; display: flex; align-items: center; gap: 7px;
        padding: 6px 9px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border-soft);
        border-left: 2px solid var(--accent); }
  .op.done { border-left-color: var(--good, #6ec77f); opacity: .8; }
  .opl { flex: 1; min-width: 0; font-size: 11.5px; color: var(--text); }
  .app { font-size: 11px; padding: 3px 10px; border-radius: 7px; border: none; background: var(--accent); color: #0b0e14; cursor: pointer; }
  .dis { width: 22px; height: 22px; padding: 0; border-radius: 6px; background: var(--elev-2, var(--bg)); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .ok { font-size: 11px; color: var(--good, #6ec77f); }
  .err { font-size: 12px; color: var(--bad, #d0655a); }
  .dots { display: inline-flex; gap: 4px; } .dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: blink 1.2s infinite; }
  .dots i:nth-child(2){animation-delay:.2s;} .dots i:nth-child(3){animation-delay:.4s;}
  @keyframes blink { 0%,100%{opacity:.25;} 50%{opacity:1;} }
  .foot { display: flex; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--border-soft); flex: none; }
  .foot input { flex: 1; min-width: 0; background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); border-radius: 8px; padding: 8px 10px; font: inherit; font-size: 13px; }
  .foot input:focus { outline: none; border-color: var(--accent); }
  .send { width: 34px; height: 34px; flex: none; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; background: var(--accent); color: #0b0e14; }
  .send:disabled { opacity: .4; cursor: default; }
</style>

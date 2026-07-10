<script>
  // The SECTION EDITOR — a story-level agent (mounted in the story shell) scoped to the CURRENT view's
  // layer. You talk; it emits HASH-ANCHORED ops (set a field, merge one key, remove one item) that each
  // point at a node by path + the #hash it saw. Edits APPLY AUTOMATICALLY and each shows an Undo — no
  // approve step. A stale anchor (the node changed elsewhere since) is REJECTED, never silently over-
  // written; the writer is told which paths drifted and can ask again. Undo restores the exact field
  // values snapshotted before the edit. Apply failures surface as a clear error, never a silent break.
  import { put } from '$lib/api.js';
  import { consumeSse } from '$lib/sse.js';
  import { loadStory } from '$lib/stories.svelte.js';

  // presentation: 'docked' (fixed left window) | 'modal' (centered overlay). 'hidden' is handled by
  // the parent (it stops rendering us and shows a launcher chip). onCenter toggles docked↔modal.
  // interview=true (a blank story on the world/premise layer) → the editor OPENS the conversation
  // itself: it greets and puts a few core-question springboards on the table (see openInterview).
  let { storyKey, layer, layerLabel = '', onCollapse = null, presentation = 'docked', onCenter = null,
        interview = false } = $props();

  let convo = $state([]);      // [{role, content, applied?, rejected?, before?, undone?, failed?}]
  let typed = $state('');
  let busy = $state(false);
  let err = $state('');
  let scroller;
  const scroll = () => requestAnimationFrame(() => { if (scroller) scroller.scrollTop = scroller.scrollHeight; });

  // Switching sections starts a fresh conversation (the editor is scoped to one layer).
  let seen = layer;
  $effect(() => { if (layer !== seen) { seen = layer; convo = []; err = ''; opened = false; } });

  // Blank story on the world/premise layer → OPEN the interview ourselves: the editor greets and
  // offers a few core-question springboards, so the writer arrives to a conversation, not a blank box.
  let opened = $state(false);
  $effect(() => {
    if (interview && !opened && !convo.length && !busy) { opened = true; openInterview(); }
  });
  async function openInterview() {
    await stream([{ role: 'user',
      text: "I'm starting a brand-new story from nothing. Put a few different directions we could take it — a world, a character, a place, a tension — for me to pick from." }]);
  }

  // ONE streamed call: the agent's `reply` TEXT streams into a live bubble; a final `result` event
  // carries the anchored ops the backend already verified + applied (with pre-edit values for undo)
  // plus `suggestions` — the list of things to potentially address, rendered as pickable chips.
  async function send() {
    const t = typed.trim(); if (!t || busy) return;
    typed = ''; err = '';
    convo = [...convo, { role: 'user', content: t }]; scroll();
    const messages = convo.filter((m) => m.role === 'user' || m.role === 'assistant').map((m) => ({ role: m.role, text: m.content }));
    await stream(messages);
  }

  // Picking a suggestion = pursuing it: send it as the next message.
  function pick(s) { if (busy) return; typed = s; send(); }

  // Open the SSE stream: fill a placeholder assistant bubble as reply tokens arrive, then reconcile
  // the final result (apply/undo/rejections). The reply text is authoritative from the `result` event.
  async function stream(messages) {
    busy = true; err = '';
    convo = [...convo, { role: 'assistant', content: '', streaming: true }];
    const idx = convo.length - 1;
    let res;
    try {
      res = await fetch(`/api/stories/${storyKey}/card/${layer}/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages }) });
    } catch { convo = convo.slice(0, idx); busy = false; err = 'editor failed'; return; }
    if (!res.ok) {
      convo = convo.slice(0, idx); busy = false;
      try { err = (await res.json())?.error || 'editor failed'; } catch { err = 'editor failed'; }
      return;
    }
    let final = null;
    await consumeSse(res, (ev) => {
      if (ev.type === 'reply') { convo[idx].content = ev.text; scroll(); }
      else if (ev.type === 'result') { final = ev.data; }
      else if (ev.type === 'error') { err = ev.error; }
    });
    busy = false; convo[idx].streaming = false;
    const d = final || {};
    convo[idx].content = d.reply || convo[idx].content || 'Okay.';
    convo[idx].suggestions = d.suggestions || [];
    if (d.error) { convo[idx].failed = d.error; scroll(); return; }
    if (d.applied?.length) {
      await loadStory(storyKey);
      window.dispatchEvent(new CustomEvent('queue:refresh'));
      // Let the visible form show itself being edited — flash the fields the agent just wrote.
      window.dispatchEvent(new CustomEvent('story:edited', { detail: { paths: d.applied.map((o) => o.path) } }));
      convo[idx].applied = d.applied; convo[idx].rejected = d.rejected || []; convo[idx].before = d.before;
    } else if (d.rejected?.length) {
      convo[idx].rejected = d.rejected; convo[idx].before = d.before;   // all stale/invalid — say why
    }
    scroll();
  }

  async function undo(m) {
    if (!m.before || m.undone || busy) return;
    busy = true;
    const r = await put(`/stories/${storyKey}`, m.before);   // restore the exact pre-edit field values
    busy = false;
    if (r.ok) { m.undone = true; convo = [...convo]; await loadStory(storyKey); window.dispatchEvent(new CustomEvent('queue:refresh')); }
    else err = r.data?.error || 'could not undo';
  }
</script>

<!-- Rendered only when not hidden. When hidden the component stays MOUNTED (template empty) so the
     conversation state (`convo`) survives hide/show instead of resetting. -->
{#if presentation !== 'hidden'}
{#if presentation === 'modal' && onCenter}
  <button class="backdrop" onclick={onCenter} aria-label="Dock editor to the side"></button>
{/if}
<aside class="schat" class:modal={presentation === 'modal'}>
  <div class="sh"><span class="dot"></span>Editing <b>{layerLabel || layer}</b>
    <span class="winctl">
      {#if onCenter}<button class="collapse" onclick={onCenter}
        title={presentation === 'modal' ? 'Dock to side' : 'Center'} aria-label="Toggle centered">{presentation === 'modal' ? '▣' : '⤢'}</button>{/if}
      {#if onCollapse}<button class="collapse" onclick={onCollapse} title="Hide editor" aria-label="Hide editor">⟨</button>{/if}
    </span></div>
  <div class="log" bind:this={scroller}>
    {#if !convo.length}
      <div class="hint">Tell the editor what to change in this section — “make the tone wryer”,
        “rename the arc to Paper Lanterns”, “give Mara a rival named Toll”. Edits apply right away;
        each one has an <b>Undo</b>. If a node changed since the editor last saw it, that edit is
        flagged <b>stale</b> — ask again and it’ll re-read.</div>
    {/if}
    {#each convo as m, i (i)}
      <div class="msg {m.role}">{#if m.streaming && !m.content}<span class="dots"><i></i><i></i><i></i></span>{:else}{m.content}{/if}</div>
      {#if m.applied?.length}
        <div class="applied">
          <div class="al">
            <span class="tick">✓</span>
            <span class="ax">{m.applied.map((o) => o.path).join(', ')}</span>
          </div>
          {#if m.undone}<span class="undone">↩ undone</span>
          {:else}<button class="undo" onclick={() => undo(m)} disabled={busy}>Undo</button>{/if}
        </div>
      {/if}
      {#if m.rejected?.length}
        <div class="stale">
          ⚠ {m.rejected.length} edit(s) not applied —
          {m.rejected.map((r) => r.path).join(', ')} {m.rejected[0]?.reason || 'stale'}
        </div>
      {/if}
      {#if m.failed}<div class="failed">⚠ couldn’t apply — {m.failed}</div>{/if}
      {#if m.suggestions?.length && !m.streaming && i === convo.length - 1}
        <div class="sugs">
          <div class="sugl">Things you could address next</div>
          {#each m.suggestions as s}
            <button class="sug" onclick={() => pick(s)} disabled={busy}>{s}</button>
          {/each}
        </div>
      {/if}
    {/each}
    {#if err}<div class="err">⚠ {err}</div>{/if}
  </div>
  <div class="foot">
    <input bind:value={typed} placeholder="Message the section editor…"
           onkeydown={(e) => e.key === 'Enter' && send()} disabled={busy} />
    <button class="send" onclick={send} disabled={busy || !typed.trim()} aria-label="Send">↳</button>
  </div>
</aside>
{/if}

<style>
  .schat { position: fixed; left: var(--storynav-w, 0); top: var(--chrome-top, 86px); bottom: 0; width: 320px; z-index: 40;
           display: flex; flex-direction: column; background: var(--panel, #14161f);
           border-right: 1px solid var(--border, #2a2f44); }
  /* Centered overlay: a floating window instead of the side dock. */
  .schat.modal { left: 50%; top: 50%; right: auto; bottom: auto; transform: translate(-50%, -50%);
                 width: min(600px, 94vw); height: min(78vh, 760px); z-index: 60;
                 border: 1px solid var(--border, #2a2f44); border-radius: 14px; overflow: hidden;
                 box-shadow: 0 24px 70px rgba(0, 0, 0, .55); }
  .backdrop { position: fixed; inset: 0; z-index: 55; padding: 0; border: none;
              background: rgba(4, 6, 12, .5); cursor: default; }
  .sh { display: flex; align-items: center; gap: 7px; padding: 11px 13px; flex: none;
        border-bottom: 1px solid var(--border-soft); font-size: 12.5px; color: var(--muted); }
  .sh b { color: var(--text); text-transform: capitalize; }
  .winctl { margin-left: auto; display: flex; gap: 2px; }
  .collapse { width: 22px; height: 22px; display: grid; place-items: center; padding: 0;
              background: none; border: 1px solid transparent; border-radius: 6px; color: var(--faint); font-size: 12px; cursor: pointer; }
  .collapse:hover { color: var(--text); background: var(--elev); }
  .log { flex: 1; min-height: 0; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
  .hint { color: var(--faint); line-height: 1.6; font-style: italic; font-size: 12.5px; }
  .hint b { color: var(--muted); font-style: normal; }
  .msg { max-width: 90%; font-size: 13px; line-height: 1.45; padding: 8px 11px; border-radius: 12px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { align-self: flex-end; background: var(--accent, #6d8cff); color: #0b0e14; border-bottom-right-radius: 4px; }
  .msg.assistant { align-self: flex-start; background: var(--elev, #1b1e2b); color: var(--text); border: 1px solid var(--border-soft); border-bottom-left-radius: 4px; }
  .applied { align-self: stretch; display: flex; align-items: center; gap: 8px; padding: 6px 9px; border-radius: 9px;
             background: color-mix(in srgb, var(--good, #6ec77f) 10%, transparent); border: 1px solid color-mix(in srgb, var(--good, #6ec77f) 30%, transparent); }
  .al { flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; }
  .tick { color: var(--good, #6ec77f); font-size: 12px; flex: none; }
  .ax { font-size: 11.5px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .undo { flex: none; font-size: 11px; padding: 3px 11px; border-radius: 7px; border: 1px solid var(--border-soft);
          background: var(--elev); color: var(--muted); cursor: pointer; }
  .undo:hover:not(:disabled) { color: var(--text); border-color: var(--muted); }
  .undone { flex: none; font-size: 11px; color: var(--faint); }
  .stale { align-self: stretch; font-size: 11.5px; color: var(--warn, #d8b35a); padding: 6px 9px; border-radius: 9px;
           background: color-mix(in srgb, var(--warn, #d8b35a) 9%, transparent); border: 1px solid color-mix(in srgb, var(--warn, #d8b35a) 28%, transparent); }
  .failed { align-self: stretch; font-size: 11.5px; color: var(--bad, #d0655a); padding: 6px 9px; border-radius: 9px;
            background: color-mix(in srgb, var(--bad, #d0655a) 8%, transparent); border: 1px solid color-mix(in srgb, var(--bad, #d0655a) 25%, transparent); }
  /* Things to potentially address — the pickable list under the message. */
  .sugs { align-self: stretch; display: flex; flex-direction: column; gap: 5px; margin-top: 2px; }
  .sugl { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin: 2px 0 1px; }
  .sug { text-align: left; font-size: 12.5px; line-height: 1.35; color: var(--text); padding: 7px 10px; border-radius: 9px;
         background: var(--elev); border: 1px solid var(--border-soft); cursor: pointer; transition: border-color .12s, background .12s; }
  .sug:hover:not(:disabled) { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--elev)); }
  .sug:disabled { opacity: .5; cursor: default; }
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

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
  import { cubicOut } from 'svelte/easing';

  // Slide the panel off the bottom edge on hide, back up on show. A Svelte transition (JS-driven,
  // compiled to a keyframe) instead of a CSS transition — a var/percent-driven CSS transition on
  // transform/padding gets stuck mid-animation in Chromium. intro t:0→1 = translateY(100%→0).
  const slideY = (node, { duration = 260 } = {}) => ({
    duration, easing: cubicOut, css: (t) => `transform: translateY(${(1 - t) * 100}%)`,
  });

  // The editor is a panel in the BOTTOM half of the screen. `presentation` is 'bottom' (shown) or
  // 'hidden' (parent stops offsetting the body and shows a launcher chip; we render nothing).
  // interview=true (a blank story on the world/premise layer) → the editor OPENS the conversation
  // itself: it greets and puts a few core-question springboards on the table (see openInterview).
  let { storyKey, layer, layerLabel = '', onCollapse = null, presentation = 'bottom',
        interview = false } = $props();

  // Per-section conversation persistence — keep the last ~12 turns so switching sections or reloading
  // doesn't lose the thread. Keyed by story + layer; stores role/content/suggestions only (the undo/
  // apply metadata is live-session-only, and undoing across a reload would apply a stale snapshot).
  const CONVO_MAX = 24;                        // ~12 user+assistant turns
  const convoStoreKey = (l) => `loom.sectionchat.${storyKey}.${l}`;
  function loadConvo(l) {
    try { return JSON.parse(localStorage.getItem(convoStoreKey(l)) || '[]'); } catch { return []; }
  }
  function saveConvo() {
    try {
      localStorage.setItem(convoStoreKey(layer), JSON.stringify(convo.slice(-CONVO_MAX).map((m) => ({
        role: m.role, content: m.content, ...(m.suggestions?.length ? { suggestions: m.suggestions } : {}) }))));
    } catch { /* storage unavailable */ }
  }

  let convo = $state(loadConvo(layer));   // restored per section; [{role, content, applied?, ...}]
  let typed = $state('');
  let busy = $state(false);
  let err = $state('');
  let scroller;
  const scroll = () => requestAnimationFrame(() => { if (scroller) scroller.scrollTop = scroller.scrollHeight; });

  // Switching sections swaps in that section's saved conversation (the editor is scoped to one layer).
  let seen = layer;
  $effect(() => { if (layer !== seen) { seen = layer; convo = loadConvo(layer); err = ''; opened = false; } });

  // A BLANK section opens itself so the writer arrives to a conversation, not an empty box:
  //   • a thin story on the world/premise layer → the fork INTERVIEW (springboards);
  //   • any other blank section → PROPOSE IMPROVEMENTS to what's there.
  // Fires once per section (opened resets on layer change); a restored saved convo skips it.
  let opened = $state(false);
  $effect(() => {
    if (!opened && !convo.length && !busy) {
      opened = true;
      if (interview) openInterview(); else openImprovements();
    }
  });
  async function openInterview() {
    await stream([{ role: 'user',
      text: "I'm starting a brand-new story from nothing. Put a few different directions we could take it — a world, a character, a place, a tension — for me to pick from." }]);
  }
  async function openImprovements() {
    await stream([{ role: 'user',
      text: "Review this section and propose 3-4 concrete improvements to it — what's underdeveloped, unclear, or could be sharper. Keep your reply to ONE short line naming the biggest gap, and put each improvement as a separate pickable option in `suggestions` for me to choose from." }]);
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
    saveConvo();   // persist the completed turn (user message + this reply) for this section
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

<!-- The aside is `{#if}`-toggled (so it slides in/out via slideY), but the COMPONENT stays mounted via
     the parent's `{#if editor}`, so `convo` (script state) survives hide/show. -->
{#if presentation !== 'hidden'}
<aside class="schat" transition:slideY>
  <div class="sh"><span class="dot"></span><b>{layerLabel || layer}</b><span class="role">co-writer</span>
    <span class="winctl">
      {#if onCollapse}<button class="collapse" onclick={onCollapse} title="Hide editor" aria-label="Hide editor">✕</button>{/if}
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
  /* The editor is a full-width panel in the BOTTOM half of the content: the form is the top half,
     the chat the bottom. It spans right of the explorer. The slide in/out is the `slideY` Svelte
     transition (see script), not a CSS transition. */
  .schat { position: fixed; left: var(--storynav-w, 0); right: 0; bottom: 0; top: auto;
           height: 50vh; z-index: 40;
           display: flex; flex-direction: column; background: var(--panel, #14161f);
           border-top: 1px solid var(--border, #2a2f44); }
  .sh { display: flex; align-items: center; gap: 7px; padding: 11px 13px; flex: none;
        border-bottom: 1px solid var(--border-soft); font-size: 12.5px; color: var(--muted); }
  .sh b { color: var(--text); text-transform: capitalize; font-weight: 600; }
  .sh .role { color: var(--faint); font-size: 11px; }
  .winctl { margin-left: auto; display: flex; gap: 2px; }
  .collapse { width: 32px; height: 32px; display: grid; place-items: center; padding: 0;
              background: none; border: 1px solid transparent; border-radius: 8px; color: var(--muted); font-size: 18px; cursor: pointer; }
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

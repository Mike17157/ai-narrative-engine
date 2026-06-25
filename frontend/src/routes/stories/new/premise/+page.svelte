<script>
  // Step 1 — Premise FIRST. The seed for the whole story. A premise is generated from an
  // IMPORTED reference card (SillyTavern/Chub) — those are external reference material, a
  // different kind of thing from OUR characters. Our characters are the cast you'll develop;
  // imported cards are never cast, only premise seeds.
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { get } from '$lib/api.js';
  import { consumeSse } from '$lib/sse.js';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';

  let wz = $derived(stories.wizard);
  let roster = $derived(chars.list || []);
  let imported = $derived(roster.filter((c) => c.imported));     // reference cards (premise seeds)
  let ours = $derived(roster.filter((c) => !c.imported));        // our characters (the cast)

  let refKeys = $state([]);   // selected reference card(s) to generate the premise from

  // Seed from the card the wizard was launched on — to references if imported, else the cast.
  $effect(() => {
    if (!wz.character) return;
    const c = roster.find((x) => x.key === wz.character);
    if (!c) return;
    if (c.imported) { if (!refKeys.includes(c.key)) refKeys = [c.key, ...refKeys]; }
    else if (!wz.castKeys.includes(c.key)) wz.castKeys = [c.key, ...wz.castKeys];
  });

  const toggleRef = (k) => refKeys = refKeys.includes(k) ? refKeys.filter((x) => x !== k) : [...refKeys, k];
  const toggleCast = (k) => wz.castKeys = wz.castKeys.includes(k) ? wz.castKeys.filter((x) => x !== k) : [...wz.castKeys, k];

  // Interview: a deterministic, lorebook-scripted Q&A that builds the premise from scratch
  // (or seeded by a reference card). The conversation drives a structured premise DOC on the
  // right — each interview question is a SECTION the model fills + revises as answers arrive.
  const MARKER = '⟦DOC⟧';
  let chat = $state([]);            // [{ role:'assistant'|'user', content }]
  let chatInput = $state('');
  let chatBusy = $state(false);
  let chatErr = $state(null);
  let progress = $state(null);      // { filled, total, done } from the backend
  let streaming = $state('');       // live partial reply during a turn
  let inputEl;                      // chat input (focused when addressing a section)
  let fields = $state({ ...(wz.premiseFields || {}) });   // section id → value (persisted in wz)
  let sections = $state([]);        // [{ id, label }] — the canvas skeleton (from the question book)
  let activeId = $state('');        // section the model is asking about (highlight)

  // The canvas skeleton: load the section labels/order from the question lorebook so the doc
  // shows its parts even before the first turn (and reflects user edits to the questions).
  onMount(async () => {
    if (sections.length) return;
    const r = await get('/lorebooks/_premise_interview');
    if (r?.entries?.length) {
      sections = r.entries.slice().sort((a, b) => (b.priority || 0) - (a.priority || 0))
        .map((e) => ({ id: e.id, label: e.title || e.id }));
    }
  });

  async function askAi(text, focus = '') {
    if (chatBusy) return;
    if (text) chat = [...chat, { role: 'user', content: text }];
    chatBusy = true; chatErr = null; streaming = '';
    let acc = '';
    let res;
    try {
      res = await fetch('/api/stories/premise-chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ characters: refKeys, premise: wz.premise, messages: chat, fields, focus }),
      });
    } catch { chatBusy = false; chatErr = 'network error'; return; }
    if (!res.ok) {
      chatBusy = false;
      let d = null; try { d = await res.json(); } catch { /* no body */ }
      chatErr = d?.error || 'generation failed';
      return;
    }
    await consumeSse(res, (ev) => {
      if (ev.type === 'delta') { acc += ev.text; streaming = acc.split(MARKER)[0]; }   // hide the doc tail
      else if (ev.type === 'result') {
        if (ev.premise) wz.premise = ev.premise;
        if (ev.sections?.length) sections = ev.sections;
        if (ev.fields) { fields = ev.fields; wz.premiseFields = fields; }   // backend merged the model's ops
        activeId = ev.active || '';
        chat = [...chat, { role: 'assistant', content: ev.reply || acc.split(MARKER)[0].trim() }];
        progress = { filled: ev.filled ?? 0, total: ev.total ?? 0, done: !!ev.done };
      } else if (ev.type === 'error') chatErr = ev.error;
    });
    streaming = ''; chatBusy = false;
  }
  const startInterview = () => askAi('');

  // Click a doc section to BRING THE INTERVIEW BACK to it — the model re-asks that section
  // (and edits it from your next answer). Dynamic: you steer, the model picks up where you point.
  const address = (s) => askAi('', s.id);

  // Conversation is the DEFAULT: auto-open the interview on entry, unless a premise
  // already exists (returning to the step) or it's already running.
  let started = $state(false);
  $effect(() => {
    if (started || chat.length || chatBusy || wz.premise.trim()) return;
    started = true;
    startInterview();
  });
  function sendChat() {
    const t = chatInput.trim();
    if (!t) return;
    chatInput = '';
    askAi(t);
  }
  function chatKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); } }

  function next() { wz.step = 1; goto('/stories/new/characters'); }
</script>

<div class="page"><div class="col wide">
  <h2 class="title">New story</h2>
  <p class="sub">Build the premise through a short interview — the conversation on the left shapes the premise doc on the right. Then pick the cast of your characters.</p>

  <label class="field">
    <span>Story name</span>
    <input bind:value={wz.name} placeholder="Untitled story" />
  </label>

  <div class="field">
    <div class="lblrow">
      <span>Premise</span>
      <button class="genbtn" onclick={startInterview} disabled={chatBusy}
        title="Interview: the AI asks scripted questions and builds the premise — from scratch or from a reference card">
        {chatBusy ? '…' : (chat.length ? '✨ Restart interview' : (refKeys.length ? '✨ Interview from reference' : '✨ Build a premise (interview)'))}
      </button>
    </div>

    <div class="builder">
      <!-- LEFT: the conversation -->
      <div class="chat">
        {#if progress?.total}
          <div class="prog">
            {progress.done ? '✓ Premise ready'
              : `${progress.filled}/${progress.total} sections${activeId ? ' · on “' + (sections.find((x) => x.id === activeId)?.label || '') + '”' : ''}`}
          </div>
        {/if}
        <div class="msgs">
          {#each chat as m}
            <div class="msg {m.role}">{m.content}</div>
          {/each}
          {#if chatBusy}
            <div class="msg assistant pending">{streaming}<span class="caret"></span></div>
          {:else if !chat.length}
            <div class="empty">Answer the questions to build your premise — or just start typing one yourself.</div>
          {/if}
        </div>
        <div class="chatbar">
          <input bind:this={inputEl} bind:value={chatInput} onkeydown={chatKey} disabled={chatBusy}
            placeholder="Answer, or steer the premise…  (Enter to send)" />
          <button onclick={sendChat} disabled={chatBusy || !chatInput.trim()}>Send</button>
        </div>
      </div>

      <!-- RIGHT: the premise doc the conversation builds -->
      <div class="canvas">
        <div class="cv-prem">
          <label for="prem">Premise</label>
          <textarea id="prem" bind:value={wz.premise} rows="3"
            placeholder="Builds as you answer — or write your own."></textarea>
        </div>
        {#if sections.length}
          <div class="cv-secs">
            {#each sections as s (s.id)}
              <button class="sec" class:active={activeId === s.id && !progress?.done}
                class:filled={fields[s.id]} onclick={() => address(s)} title="Click to bring the interview back to this part">
                <span class="sec-h">{s.label}</span>
                <span class="sec-v">{fields[s.id] || '—'}</span>
              </button>
            {/each}
          </div>
        {/if}
      </div>
    </div>
    {#if chatErr}<span class="generr">{chatErr}</span>{/if}
  </div>

  <div class="field">
    <span>Reference card — generate the premise from an imported card ({refKeys.length})</span>
    {#if imported.length}
      <div class="roster">
        {#each imported as c (c.key)}
          <button class="chip ref" class:on={refKeys.includes(c.key)} onclick={() => toggleRef(c.key)}>
            {#if c.reference || c.avatar}<img class="cav" src={c.reference || c.avatar} alt="" />{/if}
            {c.name || c.key}
          </button>
        {/each}
      </div>
    {:else}
      <p class="hint">No imported cards yet. <a href="/characters/import">Import a character card</a> to seed a premise from it.</p>
    {/if}
  </div>

  <div class="field">
    <span>Cast — your characters in this story ({wz.castKeys.length})</span>
    {#if ours.length}
      <div class="roster">
        {#each ours as c (c.key)}
          <button class="chip" class:on={wz.castKeys.includes(c.key)} onclick={() => toggleCast(c.key)}>
            {c.name || c.key}
          </button>
        {/each}
      </div>
    {:else}
      <p class="hint">No characters yet. <a href="/characters/personas/new">Build one</a> to add to the cast.</p>
    {/if}
  </div>

  <div class="acts">
    <button class="ghost" onclick={() => goto('/stories')}>← Library</button>
    <button onclick={next} disabled={!wz.castKeys.length}>Develop characters →</button>
  </div>
</div></div>

<style>
  .title { margin: 0 0 4px; font-size: 20px; font-weight: 680; }
  .sub { margin: 0 0 18px; font-size: 13px; color: var(--muted); max-width: 560px; }
  .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
  .field > span { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .lblrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .genbtn { font-size: 11.5px; font-weight: 600; padding: 4px 11px; border-radius: 7px; cursor: pointer;
    background: color-mix(in srgb, var(--accent) 12%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    color: var(--accent); }
  .genbtn:disabled { opacity: .4; cursor: default; }
  .genbtn:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .generr { font-size: 12px; color: var(--bad); }
  .builder { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 10px; align-items: stretch; }
  .chat { flex: 1 1 360px; min-width: 0; display: flex; flex-direction: column; gap: 8px; padding: 10px;
    border: 1px solid var(--border-soft); border-radius: 10px; background: var(--elev); max-height: 460px; }
  .msgs { display: flex; flex-direction: column; gap: 8px; overflow: auto; flex: 1; min-height: 180px; }
  .prog { align-self: center; font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .4px; color: var(--muted); }
  /* ── premise canvas (the doc the conversation builds) ── */
  .canvas { flex: 1 1 320px; min-width: 0; display: flex; flex-direction: column; gap: 12px; padding: 12px;
    border: 1px solid var(--border-soft); border-radius: 10px; background: var(--elev); }
  .cv-prem { display: flex; flex-direction: column; gap: 5px; }
  .cv-prem label { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .cv-secs { display: flex; flex-direction: column; gap: 6px; }
  .sec { text-align: left; display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; cursor: pointer;
    border: 1px solid var(--border-soft); border-radius: 9px; background: var(--elev-2); transition: border-color .12s; }
  .sec:hover { border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  .sec.active { border-color: var(--accent); box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent) inset; }
  .sec.filled { background: color-mix(in srgb, var(--accent) 7%, var(--elev-2)); }
  .sec-h { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .sec-v { font-size: 12.5px; line-height: 1.4; color: var(--faint); white-space: pre-wrap; }
  .sec.filled .sec-v { color: var(--text); }
  .msg { font-size: 13px; line-height: 1.45; padding: 8px 11px; border-radius: 10px; max-width: 85%; white-space: pre-wrap; }
  .msg.assistant { align-self: flex-start; background: var(--elev-2); color: var(--text); }
  .msg.user { align-self: flex-end; background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--text); }
  .msg.pending { color: var(--text); }
  .caret { display: inline-block; width: 7px; height: 14px; margin-left: 2px; vertical-align: text-bottom;
    background: var(--accent); border-radius: 1px; animation: blink 1s steps(2) infinite; }
  @keyframes blink { 0%, 50% { opacity: 1; } 50.01%, 100% { opacity: 0; } }
  .empty { font-size: 12.5px; color: var(--faint); padding: 2px 2px 4px; }
  .chatbar { display: flex; gap: 6px; margin-top: 2px; }
  .chatbar input { flex: 1; padding: 8px 11px; border-radius: 9px; background: var(--elev-2);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; }
  .chatbar input:focus { outline: none; border-color: var(--accent); }
  .chatbar button { padding: 8px 14px; font-size: 12.5px; border-radius: 9px; background: var(--accent);
    color: #fff; border: 0; cursor: pointer; }
  .chatbar button:disabled { opacity: .45; cursor: default; }
  .field input, .field textarea { padding: 9px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13.5px; resize: vertical; }
  .field input:focus, .field textarea:focus { outline: none; border-color: var(--accent); }
  .hint { margin: 0; font-size: 12.5px; color: var(--faint); }
  .hint a { color: var(--accent); }
  .roster { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .chip.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .chip.ref { padding-left: 5px; }
  .cav { width: 18px; height: 18px; border-radius: 50%; object-fit: cover; }
  .acts { display: flex; justify-content: space-between; gap: 10px; margin-top: 22px; }
  .acts button { padding: 9px 18px; font-size: 13.5px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .acts button:disabled { opacity: .45; cursor: default; }
  .acts .ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
</style>

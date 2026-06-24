<script>
  // Per-character interview: develop ONE character through back-and-forth. Left = chat with
  // the development agent; right = the exemplar cards (life / saying / reaction) it commits to
  // the character's lorebook as they're agreed. Backed by /api/stories/character/{key}/*.
  import { get, post, del } from '$lib/api.js';

  let { charKey = '', charName = '', reloadToken = 0 } = $props();

  let messages = $state([]);       // {role, content}
  let facets = $state([]);         // {id, type, title, keywords, content}
  let input = $state('');
  let busy = $state(false);
  let err = $state(null);
  let started = '';                // charKey we've auto-opened for

  const TYPES = [
    { key: 'life', label: 'Life', hint: 'moments from their past' },
    { key: 'saying', label: 'Sayings', hint: 'lines in their voice' },
    { key: 'reaction', label: 'Reactions', hint: 'how they respond' },
  ];
  let groups = $derived(Object.fromEntries(
    TYPES.map((t) => [t.key, facets.filter((f) => f.type === t.key)])
  ));

  async function loadFacets() {
    const r = await get(`/stories/character/${charKey}/facets`);
    facets = r?.facets || [];
  }

  // Re-init when the selected character changes; auto-open the interview once.
  $effect(() => {
    const key = charKey;
    if (!key || started === key) return;
    started = key;
    messages = []; facets = []; err = null;
    loadFacets();
    turn();   // let the agent open with a question
  });

  // Refresh cards (without resetting the conversation) when an improv harvest commits facets.
  $effect(() => { reloadToken; if (charKey && started === charKey) loadFacets(); });

  async function turn() {
    busy = true; err = null;
    const r = await post(`/stories/character/${charKey}/interview`, { messages });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'interview failed'; return; }
    messages.push({ role: 'assistant', content: r.data.reply });
    if (r.data.facets) facets = r.data.facets;
  }

  function send() {
    const text = input.trim();
    if (!text || busy) return;
    messages.push({ role: 'user', content: text });
    input = '';
    turn();
  }

  async function refine(f) {
    const instruction = window.prompt(`Refine "${f.title}" — how should it change?`, '');
    if (instruction === null) return;
    busy = true;
    const r = await post(`/stories/character/${charKey}/facets/${f.id}/refine`, { instruction });
    busy = false;
    if (r.data?.facets) facets = r.data.facets;
  }

  async function discard(f) {
    const r = await del(`/stories/character/${charKey}/facets/${f.id}`);
    if (r?.facets) facets = r.facets;
  }

  function onKey(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }
</script>

<div class="interview">
  <!-- left: the conversation -->
  <div class="chat">
    <div class="log">
      {#each messages as m, i (i)}
        <div class="msg {m.role}">{m.content}</div>
      {/each}
      {#if busy}<div class="msg assistant thinking">…</div>{/if}
      {#if err}<div class="msg err">{err}</div>{/if}
      {#if !messages.length && !busy}
        <div class="placeholder">Developing <b>{charName || charKey}</b> — the agent will open with a question.</div>
      {/if}
    </div>
    <div class="composer">
      <textarea bind:value={input} onkeydown={onKey} rows="2"
        placeholder="Talk it through — propose, react, steer…" disabled={busy}></textarea>
      <button onclick={send} disabled={busy || !input.trim()}>Send</button>
    </div>
  </div>

  <!-- right: the exemplars committed so far -->
  <div class="cards">
    <div class="cards-head">{charName || charKey} — established</div>
    {#each TYPES as t (t.key)}
      <div class="group">
        <div class="glabel">{t.label} <span class="ghint">· {t.hint}</span>
          <span class="gcount">{groups[t.key].length}</span></div>
        {#each groups[t.key] as f (f.id)}
          <div class="facet">
            <div class="ftitle">{f.title}</div>
            <div class="fcontent">{f.content}</div>
            <div class="factions">
              <button class="link" onclick={() => refine(f)} disabled={busy}>refine</button>
              <button class="link del" onclick={() => discard(f)}>discard</button>
            </div>
          </div>
        {/each}
        {#if !groups[t.key].length}<div class="empty">—</div>{/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .interview { display: grid; grid-template-columns: 1fr 360px; gap: 16px; height: calc(100vh - 170px); }
  /* chat */
  .chat { display: flex; flex-direction: column; min-height: 0; border: 1px solid var(--border-soft);
    border-radius: 14px; background: var(--panel); overflow: hidden; }
  .log { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 10px; }
  .msg { max-width: 82%; padding: 9px 13px; border-radius: 12px; font-size: 13.5px; line-height: 1.5;
    white-space: pre-wrap; }
  .msg.user { align-self: flex-end; background: var(--accent); color: #fff; border-bottom-right-radius: 4px; }
  .msg.assistant { align-self: flex-start; background: var(--elev-2); color: var(--text); border-bottom-left-radius: 4px; }
  .msg.thinking { opacity: .6; }
  .msg.err { align-self: center; background: transparent; color: var(--bad); font-size: 12.5px; }
  .placeholder { margin: auto; color: var(--muted); font-size: 13px; text-align: center; }
  .composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid var(--border-soft); }
  .composer textarea { flex: 1; resize: none; padding: 9px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13.5px; }
  .composer textarea:focus { outline: none; border-color: var(--accent); }
  .composer button { padding: 0 16px; border-radius: 9px; background: var(--accent); color: #fff; border: 0;
    cursor: pointer; font-size: 13.5px; }
  .composer button:disabled { opacity: .45; cursor: default; }
  /* cards */
  .cards { overflow-y: auto; padding-right: 4px; display: flex; flex-direction: column; gap: 14px; }
  .cards-head { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--muted); position: sticky; top: 0; background: var(--bg, var(--panel)); padding-bottom: 4px; }
  .group { display: flex; flex-direction: column; gap: 7px; }
  .glabel { font-size: 12px; font-weight: 650; color: var(--text); display: flex; align-items: center; gap: 6px; }
  .ghint { font-weight: 400; color: var(--faint); font-size: 11px; }
  .gcount { margin-left: auto; font-size: 11px; color: var(--faint); }
  .facet { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 11px; background: var(--panel); }
  .ftitle { font-size: 12.5px; font-weight: 650; margin-bottom: 3px; }
  .fcontent { font-size: 12.5px; color: var(--muted); line-height: 1.45; }
  .factions { display: flex; gap: 10px; margin-top: 6px; }
  .link { background: none; border: 0; padding: 0; color: var(--accent); font-size: 11.5px; cursor: pointer; }
  .link.del { color: var(--faint); }
  .link:hover { text-decoration: underline; }
  .link:disabled { opacity: .4; cursor: default; }
  .empty { font-size: 12px; color: var(--faint); }
</style>

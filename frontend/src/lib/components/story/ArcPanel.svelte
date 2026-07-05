<script>
  // The ARC panel — the planned staged progression play steers through (storymaster.generate_arc).
  // Shows the active arc on the story's play thread (stages · purpose · milestone · planned moments,
  // current stage highlighted) and plans a new one from a one-line request (the request IS the template).
  import { get, post } from '$lib/api.js';
  import Section from './Section.svelte';

  let { storyKey, conditions = [] } = $props();
  const sid = `play-${storyKey}`;
  let arc = $state(null);
  let req = $state('');
  let busy = $state(false);
  let err = $state('');

  // ── Collaborative DESIGN — the writer and the model shape the arc together. The draft
  // persists in the story's pending store (work-queue visible); Keep installs it as THE arc.
  let designing = $state(false);
  let chat = $state([]);        // [{role, text}] — this design conversation
  let draft = $state(null);     // the living arc draft the model maintains
  let dmsg = $state('');
  let dbusy = $state(false);
  (async () => {                // resume an unfinished design from the pending store
    const r = await get(`/stories/${storyKey}/pending`);
    const items = r?.pending?.arc?.items;
    if (items?.length) { draft = items[0]; designing = true; }
  })();
  async function designSay() {
    const t = dmsg.trim(); if (!t || dbusy) return;
    dmsg = ''; dbusy = true; err = '';
    chat = [...chat, { role: 'user', text: t }];
    const r = await post(`/stories/${storyKey}/arc/design`, { messages: chat });
    dbusy = false;
    if (r.ok) {
      chat = [...chat, { role: 'assistant', text: r.data.reply }];
      if (r.data.arc) draft = r.data.arc;
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } else err = r.data?.error || 'design turn failed';
  }
  async function keepDraft() {
    if (!draft?.stages?.length || dbusy) return;
    dbusy = true;
    const r = await post(`/stories/${storyKey}/arc`, { sid, arc: draft });
    dbusy = false;
    if (r.ok) {
      arc = r.data.arc; draft = null; chat = []; designing = false;
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } else err = r.data?.error || 'could not install the arc';
  }

  // The arc's SETTING STAGES — which of the story's conditions hold while this arc runs
  // (crossing the arc boundary flips them; situational cast content keys to them).
  async function toggleCond(id) {
    const cur = new Set(arc?.conditions || []);
    cur.has(id) ? cur.delete(id) : cur.add(id);
    const r = await post(`/stories/${storyKey}/arc`, { sid, conditions: [...cur] });
    if (r.ok) arc = r.data.arc;
    else err = r.data?.error || 'could not update stages';
  }

  async function load() {
    const r = await get(`/stories/${storyKey}/arc?sid=${sid}`);   // get() returns the JSON itself
    arc = r?.arc || null;
  }
  load();

  async function plan() {
    if (busy || !req.trim()) return;
    busy = true; err = '';
    const r = await post(`/stories/${storyKey}/arc`, { sid, request: req.trim() });
    busy = false;
    if (r.ok) { arc = r.data.arc; req = ''; }
    else err = r.data?.error || 'arc planning failed';
  }
  let cur = $derived(arc ? Math.min(arc.stage ?? 0, arc.stages.length) : 0);
</script>

<Section icon="🎯" title="Arc" count={arc ? `stage ${Math.min(cur + 1, arc.stages.length)}/${arc.stages.length}` : ''} open>
  <p class="hint">The planned progression play steers toward — each stage has a purpose, ONE concrete
    milestone (the player's to perform; the story sets it up, never performs it), and small planned
    moments scenes weave in. Stages advance automatically when a milestone lands on the page.</p>
  {#if arc}
    <div class="aq"><b>{arc.name}</b>{#if arc.question} <span class="q">— {arc.question}</span>{/if}</div>
    {#if conditions.length}
      <div class="condrow">
        <span class="cl">Setting stages live under this arc:</span>
        {#each conditions as c (c.id)}
          <button class="cch" class:on={(arc.conditions || []).includes(c.id)}
            title={c.effect || c.description || ''} onclick={() => toggleCond(c.id)}>
            {(arc.conditions || []).includes(c.id) ? '●' : '○'} {c.name || c.id}
          </button>
        {/each}
      </div>
    {/if}
    {#each arc.stages as s, i (i)}
      <div class="stg" class:cur={i === cur} class:done={i < cur}>
        <span class="si">{i < cur ? '✓' : i + 1}</span>
        <div class="sb">
          <div class="stt">{s.title}</div>
          <div class="spp">{s.purpose}</div>
          <div class="smm">completes when: {s.milestone}</div>
          {#if s.events?.length}
            <div class="sev">{#each s.events as e, j (j)}<span class="ev">{e}</span>{/each}</div>
          {/if}
        </div>
      </div>
    {/each}
  {/if}
  <div class="planrow">
    <input bind:value={req} onkeydown={(e) => e.key === 'Enter' && plan()}
      placeholder={arc ? 'plan a NEW arc (replaces the current one)…'
        : 'one line — e.g. a village romance: he draws up his courage, fights with himself, and asks her out'} />
    <button class="primary sm" onclick={plan} disabled={busy || !req.trim()}>{busy ? 'Planning…' : (arc ? 'Replace arc' : 'Plan arc')}</button>
    <button class="dsgn" class:on={designing} onclick={() => (designing = !designing)}
      title="Design the arc in conversation — the draft persists until you keep it">✏ Design together</button>
  </div>

  {#if designing}
    <div class="dbox">
      {#if chat.length}
        <div class="dchat">
          {#each chat as m, i (i)}
            <div class="dmsg" class:me={m.role === 'user'}><b>{m.role === 'user' ? 'You' : 'Designer'}</b> {m.text}</div>
          {/each}
        </div>
      {/if}
      {#if draft}
        <div class="ddraft">
          <div class="dhead"><b>Draft — {draft.name}</b>{#if draft.question}<span class="q"> — {draft.question}</span>{/if}
            <span class="sp"></span>
            <button class="keep" onclick={keepDraft} disabled={dbusy || !draft.stages?.length}>✓ Keep this arc</button></div>
          {#each draft.stages || [] as s, i (i)}
            <div class="dstg"><span class="si">{i + 1}</span>
              <div class="sb"><div class="stt">{s.title}</div><div class="spp">{s.purpose}</div>
                <div class="smm">completes when: {s.milestone}</div></div></div>
          {/each}
          {#if draft.conditions?.length}<div class="dcond">stages live: {draft.conditions.join(', ')}</div>{/if}
        </div>
      {/if}
      <div class="planrow">
        <input bind:value={dmsg} onkeydown={(e) => e.key === 'Enter' && designSay()}
          placeholder={draft ? 'steer the draft — "slower stage 2", "put it under the flood"…'
                             : 'what should this arc be about?'} />
        <button class="primary sm" onclick={designSay} disabled={dbusy || !dmsg.trim()}>{dbusy ? 'Thinking…' : 'Send'}</button>
      </div>
    </div>
  {/if}
  {#if err}<p class="err">{err}</p>{/if}
</Section>

<style>
  .aq { font-size: 13px; color: var(--text); }
  .aq .q { color: var(--muted); font-style: italic; }
  .condrow { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }
  .cl { font-size: 11px; color: var(--faint); }
  .cch { font-size: 11px; padding: 3px 9px; border-radius: 999px; background: var(--elev);
    border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .cch.on { border-color: var(--accent); color: var(--text);
    background: color-mix(in srgb, var(--accent) 10%, var(--elev)); }
  .stg { display: flex; gap: 10px; padding: 9px 11px; border-radius: 10px;
    border: 1px solid var(--border-soft); background: var(--panel); opacity: .85; }
  .stg.cur { border-color: var(--accent); opacity: 1; background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .stg.done { opacity: .55; }
  .si { width: 20px; height: 20px; flex: none; border-radius: 50%; display: grid; place-items: center;
    font-size: 10.5px; font-weight: 700; background: var(--elev); color: var(--muted); }
  .stg.cur .si { background: var(--accent); color: #0b0e14; }
  .stg.done .si { color: var(--accent); }
  .sb { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .stt { font-size: 12.5px; font-weight: 700; color: var(--text); }
  .spp { font-size: 12px; color: var(--muted); line-height: 1.45; }
  .smm { font-size: 11px; color: var(--faint); font-style: italic; }
  .sev { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; }
  .ev { font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev);
    border: 1px solid var(--border-soft); color: var(--muted); }
  .planrow { display: flex; gap: 8px; align-items: center; }
  .dsgn { flex: none; font-size: 12px; font-weight: 600; padding: 6px 13px; border-radius: 999px;
          background: none; border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .dsgn.on, .dsgn:hover { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .dbox { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border-radius: 12px;
          border: 1px dashed var(--accent); background: color-mix(in srgb, var(--accent) 4%, var(--panel)); }
  .dchat { display: flex; flex-direction: column; gap: 5px; max-height: 220px; overflow-y: auto; }
  .dmsg { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .dmsg b { color: var(--faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; margin-right: 5px; }
  .dmsg.me { color: var(--text); }
  .ddraft { display: flex; flex-direction: column; gap: 6px; padding: 8px 10px; border-radius: 10px;
            background: var(--elev); border: 1px solid var(--border-soft); }
  .dhead { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
  .dhead .q { color: var(--muted); font-style: italic; font-weight: 400; }
  .keep { font-size: 11.5px; padding: 4px 12px; border-radius: 999px; background: var(--accent);
          border: none; color: #0b0e14; font-weight: 700; cursor: pointer; }
  .dstg { display: flex; gap: 9px; }
  .dcond { font-size: 10.5px; color: var(--faint); }
  .sp { flex: 1; }
  .planrow input { flex: 1; padding: 7px 10px; font: inherit; font-size: 12.5px; border-radius: 8px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); }
  .planrow input:focus { outline: none; border-color: var(--accent); }
  .err { margin: 0; font-size: 12px; color: var(--bad); }
</style>

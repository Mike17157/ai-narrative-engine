<script>
  // The WORKFLOW modal — ONE conversational surface that walks a story's unfinished sections.
  // Left: the ordered checklist (from /queue — the same list the QueueRail shows). Right: the
  // focused section's AI assist, in the established suggest→approve shape (or the arc-design
  // chat for plot). Applying a section re-fetches the queue and auto-advances to the next
  // unfinished one. Every apply routes through /card/{layer} (or /arc), so the modal owns no
  // persistence of its own. Opened from anywhere via openWorkflow(storyKey, layer?).
  import Modal from '$lib/components/shared/Modal.svelte';
  import { get, post, put } from '$lib/api.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import { charName } from '$lib/characters.svelte.js';

  let { storyKey, startLayer = '', onClose = () => {} } = $props();

  const LABEL = { overview: 'Story overview', map: 'World', relationships: 'Cast & bonds', plot: 'Storylines & scenes', cast: 'Production' };
  const ICON = { overview: '🎯', map: '🗺', relationships: '🕸', plot: '🎬', cast: '🎭' };
  // The three tiers, in order — sections group under these (loom/stories/card.py TIERS).
  const TIERS = [['bible', 'Bible — the constant truth'], ['progression', 'Progression — the staged plan'], ['production', 'Production — rendered assets']];
  const nm = (k) => charName(k) || k;

  let items = $state([]);          // the raw queue items
  let focus = $state('');          // the layer we're working
  let busy = $state(false);
  let err = $state('');

  // Sections = the layers with unfinished work, in queue order (each with its item labels + tier).
  let sections = $derived.by(() => {
    const by = new Map();
    for (const it of items) {
      if (!by.has(it.layer)) by.set(it.layer, { layer: it.layer, tier: it.tier || '', order: it.order, labels: [] });
      by.get(it.layer).labels.push(it.label);
    }
    return [...by.values()].sort((a, b) => a.order - b.order);
  });
  // Tiers that actually have unfinished sections, in canonical order — for grouped headers.
  let tierGroups = $derived(TIERS
    .map(([id, label]) => ({ id, label, secs: sections.filter((s) => s.tier === id) }))
    .filter((g) => g.secs.length));
  let focusLabels = $derived(sections.find((s) => s.layer === focus)?.labels || []);

  async function loadQueue(keepFocus = true) {
    items = (await get(`/stories/${storyKey}/queue`))?.items || [];
    const layers = sections.map((s) => s.layer);
    if (!keepFocus || !layers.includes(focus)) focus = layers[0] || '';
  }
  (async () => {
    await loadStory(storyKey);   // merges read cur() — make sure it's THIS story, fresh
    await loadQueue(false);
    if (startLayer && sections.some((s) => s.layer === startLayer)) selectSection(startLayer);
  })();

  // After an apply: reload the story (so merges read fresh), re-fetch the queue, and if the
  // focused section is now clear, advance to the next unfinished one.
  async function afterApply() {
    await loadStory(storyKey);
    const done = focus;
    await loadQueue(true);
    if (!sections.some((s) => s.layer === done)) proposals = [];   // section cleared → drop its cards
    window.dispatchEvent(new CustomEvent('queue:refresh'));        // update the To-do button badge
  }
  const cur = () => stories.current || {};

  // ── Suggest→approve sections (overview · relationships · map): generate proposals, approve
  // each into the story via /card/{layer}. Proposals persist in the story's pending store for
  // relationships/map, so the queue tracks them even if you close mid-review.
  let proposals = $state([]);      // [{id, title, body, raw}]
  let intro = $state('');

  function selectSection(layer) {
    focus = layer; proposals = []; err = '';
    intro = INTRO[layer] || '';
    if (layer === 'relationships') loadPending('bonds');
    else if (layer === 'map') loadPending('conditions');
  }
  async function loadPending(kind) {
    const p = (await get(`/stories/${storyKey}/pending`))?.pending || {};
    const its = p[kind]?.items || [];
    if (kind === 'bonds') proposals = its.map((b) => ({ id: b.id, title: `${nm(b.source)} ⇄ ${nm(b.target)}`, body: b.nature || b.potential || '', raw: b }));
    else if (kind === 'conditions') proposals = its.map((c) => ({ id: c.id, title: c.name, body: c.effect || c.description || '', raw: c }));
  }

  const INTRO = {
    overview: 'Let’s fill in the premise components. Draft them, tweak the wording, keep what fits.',
    relationships: 'Let’s give the cast their bonds. Suggest a web — an innocent surface and a hidden undercurrent per pair — and keep the ones that ring true.',
    map: 'Let’s set the world’s recurring stages (seasons, events, place-states) — the modes that bring out different sides of the cast.',
    cast: 'Sprites and wardrobe are generated on the Cast tab — this one’s hands-on rather than conversational.',
  };

  async function suggest() {
    if (busy) return;
    busy = true; err = '';
    try {
      if (focus === 'overview') {
        const r = await post(`/stories/${storyKey}/premise-parts/draft`, {});
        proposals = Object.entries(r.data?.parts || {}).map(([id, text]) => ({ id, title: id, body: text }));
      } else if (focus === 'relationships') {
        const r = await post(`/stories/${storyKey}/weave-bonds`, {});
        proposals = (r.data?.bonds || []).map((b) => ({ id: b.id, title: `${nm(b.source)} ⇄ ${nm(b.target)}`, body: b.nature || b.potential || '', raw: b }));
        if (!proposals.length) err = r.data?.error || 'nothing new to propose';
      } else if (focus === 'map') {
        const r = await post(`/stories/${storyKey}/conditions/generate`, {});
        proposals = (r.data?.conditions || []).map((c) => ({ id: c.id, title: c.name, body: c.effect || c.description || '', raw: c }));
        if (!proposals.length) err = r.data?.error || 'nothing new to propose';
      }
    } catch (e) { err = String(e?.message || e); }
    busy = false;
  }

  async function approve(p) {
    busy = true; err = '';
    try {
      if (focus === 'overview') {
        const parts = { ...(cur().premise_parts || {}), [p.id]: p.body };
        await post(`/stories/${storyKey}/card/overview`, { premise_parts: parts });
      } else if (focus === 'relationships') {
        const rels = [...(cur().relationships || []).filter((r) => (r.id || '') !== p.id), p.raw];
        await post(`/stories/${storyKey}/card/relationships`, { relationships: rels });
        await syncPending('bonds', proposals.filter((x) => x.id !== p.id).map((x) => x.raw));
      } else if (focus === 'map') {
        const conds = [...(cur().conditions || []).filter((c) => (c.id || '') !== p.id), p.raw];
        await post(`/stories/${storyKey}/card/map`, { conditions: conds });
        await syncPending('conditions', proposals.filter((x) => x.id !== p.id).map((x) => x.raw));
      }
      proposals = proposals.filter((x) => x.id !== p.id);
      await afterApply();
    } catch (e) { err = String(e?.message || e); }
    busy = false;
  }
  async function dismiss(p) {
    proposals = proposals.filter((x) => x.id !== p.id);
    if (focus === 'relationships') await syncPending('bonds', proposals.map((x) => x.raw));
    else if (focus === 'map') await syncPending('conditions', proposals.map((x) => x.raw));
  }
  async function syncPending(kind, its) {
    try { await put(`/stories/${storyKey}/pending/${kind}`, { items: its }); } catch { /* draft */ }
  }

  // ── Plot section: the collaborative arc-design chat (its own conversational endpoint). ──
  let chat = $state([]);
  let draft = $state(null);
  let cmsg = $state('');
  (async () => {
    const p = (await get(`/stories/${storyKey}/pending`))?.pending || {};
    if (p.arc?.items?.length) draft = p.arc.items[0];
  })();
  async function designSay() {
    const t = cmsg.trim(); if (!t || busy) return;
    cmsg = ''; busy = true; err = '';
    chat = [...chat, { role: 'user', text: t }];
    const r = await post(`/stories/${storyKey}/arc/design`, { messages: chat });
    busy = false;
    if (r.ok) { chat = [...chat, { role: 'assistant', text: r.data.reply }]; if (r.data.arc) draft = r.data.arc; }
    else err = r.data?.error || 'design turn failed';
  }
  async function keepArc() {
    if (!draft?.stages?.length || busy) return;
    busy = true;
    const r = await post(`/stories/${storyKey}/arc`, { sid: `play-${storyKey}`, arc: draft });
    busy = false;
    if (r.ok) { draft = null; chat = []; await afterApply(); } else err = r.data?.error || 'could not install the arc';
  }

  function close() { onClose(); }
</script>

<Modal onClose={close} flush width="880px" height="620px" zIndex={210}>
  <div class="whead">
    <b>Finish the story</b>
    <span class="wsub">{sections.length ? `${sections.length} section${sections.length > 1 ? 's' : ''} to go` : 'all sections complete 🎉'}</span>
    <span class="sp"></span>
    <button class="wx" onclick={close} aria-label="Close">✕</button>
  </div>
  <div class="wbody">
    <!-- LEFT: unfinished sections, grouped by tier (bible → progression → production) -->
    <div class="wnav">
      {#if !sections.length}
        <div class="wdone">✓ Nothing left — the pipeline is satisfied.</div>
      {/if}
      {#each tierGroups as g (g.id)}
        <div class="wtier">{g.label}</div>
        {#each g.secs as s (s.layer)}
          <button class="wsec" class:on={focus === s.layer} onclick={() => selectSection(s.layer)}>
            <span class="wic">{ICON[s.layer]}</span>
            <span class="wsl"><b>{LABEL[s.layer]}</b><span class="wct">{s.labels.length} to do</span></span>
          </button>
        {/each}
      {/each}
    </div>

    <!-- RIGHT: the focused section's conversational assist -->
    <div class="wpane">
      {#if !focus}
        <div class="wempty">Pick a section to work on — or you’re done.</div>
      {:else}
        <div class="wtodo">
          {#each focusLabels as l (l)}<div class="wtl">☐ {l}</div>{/each}
        </div>
        {#if intro}<p class="wintro">{intro}</p>{/if}

        {#if focus === 'plot'}
          <!-- Collaborative arc design -->
          <div class="wchat">
            {#each chat as m, i (i)}
              <div class="wmsg" class:me={m.role === 'user'}><b>{m.role === 'user' ? 'You' : 'Designer'}</b> {m.text}</div>
            {/each}
            {#if draft}
              <div class="wdraft">
                <div class="wdh"><b>Draft — {draft.name}</b><span class="sp"></span>
                  <button class="wkeep" onclick={keepArc} disabled={busy || !draft.stages?.length}>✓ Keep arc</button></div>
                {#each draft.stages || [] as st, i (i)}<div class="wds"><span class="wn">{i + 1}</span> {st.title} — <span class="wmut">{st.purpose}</span></div>{/each}
              </div>
            {/if}
          </div>
          <div class="wrow">
            <input bind:value={cmsg} placeholder={draft ? 'steer the draft…' : 'what should this arc be about?'}
              onkeydown={(e) => e.key === 'Enter' && designSay()} />
            <button class="wgo" onclick={designSay} disabled={busy || !cmsg.trim()}>{busy ? '…' : 'Send'}</button>
          </div>

        {:else if focus === 'cast'}
          <div class="wmanual">Cast sprites and wardrobe are generated on the <b>Cast</b> tab.</div>

        {:else}
          <!-- suggest → approve (overview · relationships · map) -->
          <div class="wgenrow">
            <button class="wgo" onclick={suggest} disabled={busy}>
              {busy ? '✨ Working…' : (focus === 'overview' ? '✨ Draft components' : focus === 'relationships' ? '✨ Suggest bonds' : '✨ Suggest stages')}</button>
          </div>
          {#if proposals.length}
            <div class="wcards">
              {#each proposals as p (p.id)}
                <div class="wcard">
                  <div class="wcb"><b>{p.title}</b>{#if p.body}<span class="wcbody">{p.body}</span>{/if}</div>
                  <div class="wca">
                    <button class="wapp" onclick={() => approve(p)} disabled={busy}>✓ Keep</button>
                    <button class="wdis" onclick={() => dismiss(p)} aria-label="Dismiss">✕</button>
                  </div>
                </div>
              {/each}
            </div>
          {/if}
        {/if}
        {#if err}<div class="werr">{err}</div>{/if}
      {/if}
    </div>
  </div>
</Modal>

<style>
  .whead { display: flex; align-items: baseline; gap: 12px; padding: 13px 16px; border-bottom: 1px solid var(--border-soft); }
  .whead b { font-size: 14px; }
  .wsub { font-size: 11.5px; color: var(--faint); }
  .sp { flex: 1; }
  .wx { background: none; border: none; color: var(--muted); font-size: 15px; cursor: pointer; }
  .wbody { flex: 1; min-height: 0; display: grid; grid-template-columns: 220px 1fr; }
  .wnav { border-right: 1px solid var(--border-soft); padding: 10px; display: flex; flex-direction: column; gap: 5px; overflow-y: auto; }
  .wtier { font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .5px;
           color: var(--faint); padding: 8px 6px 2px; }
  .wtier:first-child { padding-top: 2px; }
  .wdone { font-size: 12px; color: var(--good, #5ec27a); padding: 10px 6px; }
  .wsec { display: flex; align-items: center; gap: 9px; padding: 9px 10px; border-radius: 9px; text-align: left;
          background: none; border: 1px solid transparent; color: var(--text); cursor: pointer; }
  .wsec:hover { background: var(--elev); }
  .wsec.on { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .wic { font-size: 16px; flex: none; }
  .wsl { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
  .wsl b { font-size: 12.5px; }
  .wct { font-size: 10.5px; color: var(--faint); }
  .wpane { padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
  .wempty, .wmanual { color: var(--muted); font-size: 13px; padding: 20px 4px; }
  .wtodo { display: flex; flex-direction: column; gap: 3px; }
  .wtl { font-size: 12px; color: var(--muted); }
  .wintro { margin: 0; font-size: 12.5px; color: var(--text); line-height: 1.5; }
  .wgenrow, .wrow { display: flex; gap: 8px; align-items: center; }
  .wgo { font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 999px; cursor: pointer;
         background: var(--accent); border: none; color: #0b0e14; }
  .wgo:disabled { opacity: .5; cursor: default; }
  .wrow input { flex: 1; padding: 8px 11px; font: inherit; font-size: 12.5px; border-radius: 8px;
                background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); }
  .wrow input:focus { outline: none; border-color: var(--accent); }
  .wcards { display: flex; flex-direction: column; gap: 7px; }
  .wcard { display: flex; align-items: flex-start; gap: 10px; padding: 9px 11px; border-radius: 10px;
           background: var(--elev); border: 1px solid var(--border-soft); }
  .wcb { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
  .wcb b { font-size: 12.5px; }
  .wcbody { font-size: 11.5px; color: var(--muted); line-height: 1.5; }
  .wca { display: flex; gap: 5px; flex: none; }
  .wapp { font-size: 11.5px; padding: 5px 11px; border-radius: 8px; border: none; cursor: pointer; background: var(--accent); color: #0b0e14; font-weight: 700; }
  .wdis { width: 26px; height: 26px; padding: 0; border-radius: 7px; background: var(--elev-2, var(--bg)); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .wchat { display: flex; flex-direction: column; gap: 6px; max-height: 300px; overflow-y: auto; }
  .wmsg { font-size: 12.5px; color: var(--muted); line-height: 1.5; }
  .wmsg.me { color: var(--text); }
  .wmsg b { font-size: 10px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin-right: 6px; }
  .wdraft { padding: 9px 11px; border-radius: 10px; background: var(--elev); border: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 5px; }
  .wdh { display: flex; align-items: center; font-size: 12.5px; }
  .wkeep { font-size: 11.5px; padding: 4px 12px; border-radius: 999px; background: var(--accent); border: none; color: #0b0e14; font-weight: 700; cursor: pointer; }
  .wds { font-size: 12px; color: var(--text); }
  .wn { display: inline-grid; place-items: center; width: 17px; height: 17px; border-radius: 50%; background: var(--elev-2, var(--bg)); font-size: 10px; margin-right: 4px; }
  .wmut { color: var(--muted); }
  .werr { font-size: 12px; color: var(--bad, #d0655a); }
</style>

<script>
  // The structure workspace — ONE surface, ONE mode. It branches only on whether a story exists yet
  // (no key = a new story being generated; key = a committed story being edited). The generate tools
  // (premise → cast queue → web) are available in BOTH: for a new story they lead to "Create story";
  // for an existing story they ADD to the cast. Plus the web/arcs/json lenses + the Build agent once
  // the story exists. See loom/stories/GENESIS.md.
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { post } from '$lib/api.js';
  import { stories, loadStory, saveDraft, clearDraft } from '$lib/stories.svelte.js';
  import { chars } from '$lib/characters.svelte.js';
  import RelationshipGraph from '$lib/components/story/RelationshipGraph.svelte';
  import AgentChat from '$lib/components/story/AgentChat.svelte';
  import Icon from '$lib/components/shared/Icon.svelte';

  let key = $derived($page.params.key);          // undefined on /stories/genesis (a new story)
  let st = $derived(stories.current);
  let isNew = $derived(!key);

  // The new-story and structure pages are the SAME surface. A new story has no cast yet → the left
  // chat greets with a first message and you DESCRIBE the cast you want; it builds the draft queue
  // (suggest→approve), then you derive + build. An existing story shows the live web/arcs lenses.
  const FIRST_MSG = "Let's build your story. Tell me the cast or the vibe you want — e.g. “a shy boy "
    + "and the popstar who keeps catching him watching her”, or “a fun-loving friend group”. I'll "
    + "propose characters as cards; confirm the keepers, re-weave their bonds, then we derive and build.";

  // ── Draft cast → web → derived stories ──────────────────────────────────────
  // The cast is built CONVERSATIONALLY by the left chat (draft mode); these run the rest of the flow.
  let busy = $state('');                 // '' | cast | weave | derive | commit
  let err = $state(null);
  let seed = $state('');                 // the one-line idea
  let castN = $state(4);
  // The WORLD frame (the stage) — authored first so the cast's backgrounds belong to one place, not an
  // improvised vacuum. Premise stays emergent. Threaded into BOTH harness design and voice formalize.
  let world = $state({ genre: '', tone: '', setting: '', situation: '' });
  let worldBusy = $state(false);
  let fieldBusy = $state({});            // world field -> regenerating
  // The generation model for THIS flow — a per-request override (builder_ctx reads body.model), so the
  // toggle steers world/cast/voice/regen without touching the stage_premise preset. GPT + Anthropic only.
  let genModel = $state('openai/gpt-5-mini');
  const GEN_MODELS = [
    { group: 'OpenAI', items: [['openai/gpt-5', 'GPT-5'], ['openai/gpt-5-mini', 'GPT-5 mini'],
        ['openai/gpt-4.1-mini', 'GPT-4.1 mini'], ['openai/gpt-5-nano', 'GPT-5 nano']] },
    { group: 'Anthropic', items: [['anthropic/claude-sonnet-4.5', 'Claude Sonnet 4.5'],
        ['anthropic/claude-haiku-4.5', 'Claude Haiku 4.5']] },
    { group: 'DeepSeek', items: [['deepseek/deepseek-v3.2', 'DeepSeek 3.2'],
        ['deepseek/deepseek-v4-pro', 'DeepSeek V4 Pro']] },
  ];
  let canGen = $derived(!!(seed.trim() || world.setting.trim() || world.situation.trim()));
  async function suggestWorld() {
    const s = seed.trim();
    if (worldBusy || !s) return;
    worldBusy = true; err = null;
    const r = await post('/stories/genesis/world', { seed: s, model: genModel });
    worldBusy = false;
    if (r.ok) world = { genre: r.data.genre || '', tone: r.data.tone || '', setting: r.data.setting || '', situation: r.data.situation || '' };
    else err = r.data?.error || 'world failed';
  }
  async function regenField(field) {
    if (fieldBusy[field]) return;
    fieldBusy = { ...fieldBusy, [field]: true }; err = null;
    const r = await post('/stories/genesis/world/field', { field, world: $state.snapshot(world), seed: seed.trim(), model: genModel });
    fieldBusy = { ...fieldBusy, [field]: false };
    if (r.ok && r.data?.value) world = { ...world, [field]: r.data.value };
    else if (!r.ok) err = r.data?.error || 'regen failed';
  }
  // Auto-grow a textarea to fit its content (no inner scroll) — re-fits on input AND when the bound value
  // changes programmatically (suggest / regen). Used by the story-gen text boxes.
  function autogrow(node) {
    const fit = () => {
      node.style.height = 'auto';
      const cs = getComputedStyle(node);   // border-box: scrollHeight excludes the border, add it back
      const border = parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
      node.style.height = (node.scrollHeight + border) + 'px';
    };
    node.addEventListener('input', fit);
    queueMicrotask(fit);
    return { update: fit, destroy: () => node.removeEventListener('input', fit) };
  }
  let harnesses = $state([]);
  let draftRels = $state([]);
  let candidates = $state([]);
  let newName = $state('');
  let newType = $state('novel');
  const roleOf = (id) => harnesses.find((h) => h.id === id)?.role || id;

  // Ratification queue: generated characters are SUGGESTIONS — confirm the keepers, remove the rest.
  let kept = $state({});
  let confirmedCount = $derived(harnesses.filter((h) => kept[h.id]).length);
  let activeHarnesses = $derived(confirmedCount ? harnesses.filter((h) => kept[h.id]) : harnesses);
  // Sort by group so members sit adjacent on the ring; carry a group colour for the node halo.
  let draftCast = $derived([...activeHarnesses]
    .sort((a, b) => String(a.group || '~').localeCompare(String(b.group || '~')))
    .map((h) => ({ key: h.id, name: h.name || h.role || h.id, primary: false, face: h.face || '',
      facing: !!faceBusy[h.id],
      group: h.group || '', groupColor: h.group ? `hsl(${hueOf(h.group)} 55% 58%)` : '' })));
  const confirmH = (id) => (kept = { ...kept, [id]: !kept[id] });
  function dropH(id) {
    harnesses = harnesses.filter((h) => h.id !== id);
    const { [id]: _gone, ...rest } = kept; kept = rest;
    draftRels = draftRels.filter((r) => r.source !== id && r.target !== id);
  }

  async function reweave() {
    if (busy || activeHarnesses.length < 2) return;
    busy = 'weave'; err = null;
    const w = await post('/stories/genesis/weave', { harnesses: $state.snapshot(activeHarnesses) });
    busy = ''; if (w.ok) draftRels = w.data.relationships || []; else err = w.data?.error || 'weave failed';
  }
  async function derive(steer = '') {
    if (busy || !activeHarnesses.length) return;
    busy = 'derive'; err = null;
    const r = await post('/stories/genesis/derive', { harnesses: $state.snapshot(activeHarnesses), relationships: draftRels, steer });
    busy = ''; if (r.ok) candidates = r.data.candidates || []; else err = r.data?.error || 'derive failed';
  }
  async function build(cand) {                    // NEW story → commit, land on the same surface (now live)
    if (busy) return;
    busy = 'commit'; err = null;
    const r = await post('/stories/genesis/commit',
      { candidate: cand, harnesses: $state.snapshot(activeHarnesses), relationships: $state.snapshot(webRels), name: newName || cand.title, type: newType });
    busy = '';
    if (r.ok && r.data?.key) { clearDraft(); goto(`/stories/${r.data.key}/structure`); return; }
    err = r.data?.error || 'could not build story';
  }
  function resetGen() { harnesses = []; draftRels = []; candidates = []; kept = {}; seed = ''; }

  // Cast generation: ONE pass designs N Big-Five-grounded harnesses (want/lie/wound/secret + a concrete
  // trauma & good memory — a reliable count), then the auto-flesh effect builds each person's VOICE from
  // that harness — 5 quotes ANCHORED to the psychology (wound, good memory, want, lie, defense) so the
  // lines cohere AND stay specific — ALL CONCURRENTLY, faces too. The chat stays for refinement.
  async function genCast() {
    const s = seed.trim();
    if (busy || !canGen) return;
    busy = 'cast'; err = null;
    const r = await post('/stories/genesis/harnesses', { seed: s, n: castN, world: $state.snapshot(world), model: genModel });
    busy = '';
    if (r.ok) { harnesses = (r.data?.harnesses || []).map((h) => ({ ...h })); kept = {}; draftRels = []; }
    else err = r.data?.error || 'generation failed';
  }

  // ── Draft cache ── The genesis state is the single cached draft: hydrate it on entry, persist edits
  // (debounced) so it survives navigation + appears in the library, and clear it on commit (above).
  // The cast queue AND the chat transcript are both part of the in-progress story → both cached.
  let hydrated = $state(false);
  let chatState = $state(stories.draft?.chat || null);   // {convo, history, activeBehaviour, mode}
  const onChat = (snap) => { chatState = snap; };
  onMount(() => {
    if (!isNew) { hydrated = true; return; }
    const d = stories.draft;
    if (d) {
      harnesses = d.harnesses || []; draftRels = d.draftRels || []; candidates = d.candidates || [];
      kept = d.kept || {}; leadId = d.leadId || ''; newName = d.newName || ''; newType = d.newType || 'novel';
      if (d.world) world = { genre: '', tone: '', setting: '', situation: '', ...d.world };
      if (d.genModel) genModel = d.genModel;
    }
    hydrated = true;
  });
  let saveT;
  $effect(() => {
    if (!isNew) return;
    const snap = { harnesses: $state.snapshot(harnesses), draftRels: $state.snapshot(draftRels),
      candidates: $state.snapshot(candidates), kept: $state.snapshot(kept), leadId, newName, newType,
      world: $state.snapshot(world), genModel, chat: chatState };
    if (!hydrated) return;                                    // don't clobber the cache before hydrating
    clearTimeout(saveT);
    saveT = setTimeout(() => {
      // Keep the draft if there's a cast, a name, a started chat, OR an authored world — all in-progress work.
      const empty = !snap.harnesses.length && !snap.newName && !(snap.chat?.convo?.length)
        && !(snap.world?.setting || snap.world?.situation);
      if (empty) clearDraft(); else saveDraft(snap);
    }, 500);
  });

  // The draft queue IS an editable artifact — the agent reshapes it conversationally (add/cut/edit a
  // harness, re-weave a bond) via the universal suggest→approve loop, target='draft' (nothing persists
  // until commit). Approved ops come back here and replace the queue. See AgentChat.
  // The world rides in the artifact so the chat sees the established stage (proposals fit it) and can
  // reshape it via set_world_field — echoes come back in g.world.
  let draftArtifact = $derived({ cast: harnesses, relationships: draftRels, world });
  let formalizing = $state({});   // harness id -> true while its prose is being structured
  function applyDraft(g) {
    if (Array.isArray(g?.cast)) harnesses = g.cast;
    if (Array.isArray(g?.relationships)) draftRels = g.relationships;
    if (g?.world && typeof g.world === 'object') world = { genre: '', tone: '', setting: '', situation: '', ...g.world };
  }
  // Auto-FLESH every harness that has a sketch or scaffold but isn't a full person yet — fired in
  // PARALLEL (formalize self-guards re-entry). Covers BOTH chat-proposed prose personas AND the
  // Generate-cast scaffolds → name/background/hobbies/sayings + psychology + woven edges. See
  // [[conversational-edit-loop]]. The face effect then renders portraits, also in parallel.
  $effect(() => { if (isNew) for (const h of harnesses) if ((h.persona || scaffolded(h)) && !fleshed(h)) formalize(h); });
  async function formalize(h) {
    if (formalizing[h.id]) return;
    formalizing = { ...formalizing, [h.id]: true };
    const others = harnesses.filter((x) => x.id !== h.id).map((x) => x.role || x.name).filter(Boolean);
    const r = await post('/stories/genesis/formalize', { persona: blurbOf(h), role: h.role, others, world: $state.snapshot(world), model: genModel });
    formalizing = { ...formalizing, [h.id]: false };
    if (!r.ok) return;
    const d = r.data || {};
    const keepList = (a, b) => (a?.length ? a : (b || []));
    harnesses = harnesses.map((x) => x.id === h.id   // formalize FILLS gaps, never clobbers existing
      ? { ...x, name: x.name || d.name, temperament: x.temperament || d.temperament,
          want: x.want || d.want, lie: x.lie || d.lie, wound: x.wound || d.wound, secret: x.secret || d.secret,
          good_memory: x.good_memory || d.good_memory,
          background: x.background || d.background, hobbies: keepList(x.hobbies, d.hobbies),
          sayings: keepList(x.sayings, d.sayings) }
      : x);
    // resolve the character's relationships (named by role/name) to harness ids → draft web edges
    const find = (nm) => harnesses.find((x) =>
      (x.role || '').toLowerCase() === String(nm).toLowerCase() ||
      (x.name || '').toLowerCase() === String(nm).toLowerCase());
    const add = [];
    for (const rel of (d.relationships || [])) {
      const tgt = find(rel.target);
      if (!tgt || tgt.id === h.id || draftRels.some((e) => e.source === h.id && e.target === tgt.id)) continue;
      add.push({ id: `r-${h.id}-${tgt.id}`, source: h.id, target: tgt.id,
                 nature: rel.nature, dynamic: rel.dynamic, stance: rel.stance, note: rel.note });
    }
    if (add.length) draftRels = [...draftRels, ...add];
  }

  // ── Face portraits ── Best-effort: once a draft character is fleshed, render a square portrait
  // (keyless — drafts have no record) and stash the data URI on the harness so it clips into the graph
  // node. `faceBusy` drives an on-node "generating" pulse so a slow 1024² render reads as in-progress,
  // not broken. `faceFail` caps AUTO retries at 3 (renders await ~a minute, so the tries are naturally
  // spaced) — a transient backend hiccup recovers without a permanent block; the inspector's 👤 forces
  // a fresh attempt (resets the counter). Degrades silently → node shows the initial.
  let faceBusy = $state({});
  let faceFail = $state({});
  async function genFace(h, force = false) {
    if (!h || faceBusy[h.id]) return;
    if (!force && (h.face || (faceFail[h.id] || 0) >= 3)) return;
    if (force) faceFail = { ...faceFail, [h.id]: 0 };
    const txt = (h.background || h.persona || '').trim();
    if (txt.length < 20) return;
    faceBusy = { ...faceBusy, [h.id]: true };
    let img = '', appr = '';
    try {
      const r = await post('/stories/genesis/face', { name: h.name || h.role, persona: txt, appearance: h.appearance || '', model: genModel });
      if (r.ok && r.data?.image) { img = r.data.image; appr = r.data.appearance || ''; }
    } catch { /* network drop → treated as a fail below */ }
    faceBusy = { ...faceBusy, [h.id]: false };
    if (img) harnesses = harnesses.map((x) => x.id === h.id ? { ...x, face: img, appearance: x.appearance || appr } : x);
    else faceFail = { ...faceFail, [h.id]: (faceFail[h.id] || 0) + 1 };
  }
  // Auto-render a portrait for every fleshed character. genFace's guards (has-face / busy / fail-cap)
  // keep this from spinning; failed renders retry up to the cap as the effect re-runs.
  $effect(() => { if (isNew) for (const h of harnesses) if (fleshed(h)) genFace(h); });
  let facingCount = $derived(Object.values(faceBusy).filter(Boolean).length);

  // Two lenses over the SAME draft character: 'character' (name/background/hobbies/sayings — to
  // understand them) and 'harness' (the psychology scaffold — to build). Toggled for the whole queue.
  let view = $state('character');
  const sayLine = (s) => String(s).replace(/^[\s"'“”‘’]+|[\s"'“”‘’]+$/g, '');   // the model already quotes sayings
  const fleshed = (h) => !!(h.background || h.hobbies?.length || h.sayings?.length);
  const scaffolded = (h) => !!(h.temperament || h.want);
  // A blurb to formalize from: the proposed prose, else composed from whatever scaffold exists (so a
  // Generate-cast harness with no prose can still be fleshed into the Character view on demand).
  function blurbOf(h) {
    if (h.persona) return h.persona;
    const b = [h.role];
    if (h.temperament) b.push(h.temperament);
    if (h.want) b.push(`Wants ${h.want}.`);
    if (h.lie) b.push(`Believes, falsely, that ${h.lie}.`);
    if (h.wound) b.push(`Old wound (a real past event): ${h.wound}.`);
    if (h.good_memory) b.push(`A cherished memory: ${h.good_memory}.`);
    if (h.secret) b.push(`Secret: ${h.secret}.`);
    return b.filter(Boolean).join(' ');
  }

  // ── Social groups ── PURELY VISUAL now: members sharing a `group` get the same coloured halo on the
  // ring (a cluster you can see), but a group draws NO edges. Relationships ARE potential edges — every
  // bond is an explicit potential you pick, so a solid edge always means "you defined this".
  let groups = $derived([...new Set(harnesses.map((h) => h.group).filter(Boolean))]);
  let webRels = $derived(draftRels);
  function setGroup(id, g) {
    g = (g || '').trim();
    harnesses = harnesses.map((h) => h.id === id ? { ...h, group: g } : h);
  }
  const hueOf = (g) => { let n = 0; for (const c of String(g || '')) n = (n * 31 + c.charCodeAt(0)) % 360; return n; };

  // ── Relationship potentials (the story SEED) ── Constrain the state space: pick ONE lead (the POV /
  // protagonist) and define each OTHER character's potential RELATIVE to them — N-1 bonds, not N². For
  // each pair the agent suggests 2-3 potentials (hidden common core + trajectory + tone); you pick one.
  // VNs keep one POV; manga/novels benefit too. See loom/stories/genesis.py suggest_potentials.
  let leadId = $state('');
  let activeId = $state('');      // the node being inspected in the graph (any character, incl. the lead)
  let suggesting = $state({});    // other id -> bool
  let suggestions = $state({});   // other id -> [{common, trajectory, nature, stance}]
  let leadH = $derived(harnesses.find((h) => h.id === leadId));
  let others = $derived(leadId ? harnesses.filter((h) => h.id !== leadId) : []);
  let activeChar = $derived(harnesses.find((h) => h.id === activeId) || null);
  // The potential being shaped: lead ↔ the inspected node (only when it isn't the lead itself).
  let activeRel = $derived((activeId && activeId !== leadId) ? relWith(activeId) : null);
  $effect(() => { if (isNew && !leadId && harnesses.length) leadId = harnesses[0].id; });
  // The graph driver: real bonds + a faint latent spoke from the lead to every undefined other, so the
  // ring SHOWS what's left to define. Clicking any node inspects that character (see the detail panel).
  let potentialRels = $derived.by(() => {
    const out = [...webRels];
    if (!leadId) return out;
    const have = new Set(webRels.map((r) => [r.source, r.target].sort().join('|')));
    for (const o of others) {
      if (!have.has([leadId, o.id].sort().join('|'))) out.push({ id: `lat-${o.id}`, source: leadId, target: o.id, pending: true, stance: 'neutral' });
    }
    return out;
  });
  const selectNode = (k) => { if (k) activeId = k; };
  const relWith = (oid) => draftRels.find((r) =>
    (r.source === leadId && r.target === oid) || (r.source === oid && r.target === leadId));
  // A bond reads BOTH ways: resolve `rel` into the inspected character's side and the other's side
  // (target_* mirrors the source side when unset). Used by the inspector to show each read.
  function sidesOf(rel, meId) {
    if (!rel) return null;
    const meSrc = rel.source === meId;
    const src = { stance: rel.stance || 'neutral', read: rel.dynamic || '' };
    const tgt = { stance: rel.target_stance || rel.stance || 'neutral', read: rel.target_dynamic || rel.dynamic || '' };
    return meSrc ? { mine: src, theirs: tgt, otherId: rel.target } : { mine: tgt, theirs: src, otherId: rel.source };
  }
  const charBrief = (h) => ({ name: h?.name || h?.role || '', role: h?.role || '',
    persona: h?.background || h?.persona || '', want: h?.want || '', lie: h?.lie || '', wound: h?.wound || '' });
  async function suggestFor(o) {
    if (!leadH || suggesting[o.id]) return;
    suggesting = { ...suggesting, [o.id]: true };
    const r = await post('/stories/genesis/potentials', { a: charBrief(leadH), b: charBrief(o), n: 3, model: genModel });
    suggesting = { ...suggesting, [o.id]: false };
    if (r.ok) suggestions = { ...suggestions, [o.id]: r.data?.potentials || [] };
  }
  function pickPotential(o, pot) {
    // The bond POINTS AT THE MC (lead = the player's role): source = the NPC, target = the MC. We author
    // ONLY the NPC's side — the common core (ground for closeness) + the NPC's starting disposition. No
    // outcome, no trajectory, and never the MC's feelings (those are the player's). See GENESIS.md.
    const edge = { id: `r-${o.id}-${leadId}`, source: o.id, target: leadId, nature: '',
      stance: pot.stance || 'neutral', dynamic: pot.read || '', note: '', potential: pot.common || '', trajectory: '' };
    const i = draftRels.findIndex((r) => (r.source === leadId && r.target === o.id) || (r.source === o.id && r.target === leadId));
    draftRels = i >= 0 ? draftRels.map((r, j) => j === i ? edge : r) : [...draftRels, edge];
    suggestions = { ...suggestions, [o.id]: [] };
  }

  async function addToCast() {                    // EXISTING story → append the confirmed characters
    if (busy) return;
    busy = 'commit'; err = null;
    const r = await post(`/stories/${key}/genesis/add`, { harnesses: $state.snapshot(activeHarnesses), relationships: $state.snapshot(webRels) });
    busy = '';
    if (r.ok) { await loadStory(key); resetGen(); } else err = r.data?.error || 'add failed';
  }

</script>

{#snippet charBody(h)}
  {#if formalizing[h.id]}
    <p class="hwork">fleshing out…</p>
    {#if h.persona}<p class="hpersona">{h.persona}</p>{/if}
  {:else if view === 'character'}
    {#if h.background}<p class="hpersona">{h.background}</p>
    {:else if h.persona}<p class="hpersona">{h.persona}</p>
    {:else if h.temperament}<p class="htemp">{h.temperament}</p>{/if}
    {#if h.hobbies?.length}<div class="chips">{#each h.hobbies as x}<span class="chip">{x}</span>{/each}</div>{/if}
    {#if h.sayings?.length}<div class="says">{#each h.sayings as s}<p class="say">“{sayLine(s)}”</p>{/each}</div>{/if}
  {:else}
    {#if h.persona && !scaffolded(h)}<p class="hpersona">{h.persona}</p>{/if}
    {#if h.temperament}<p class="htemp">{h.temperament}</p>{/if}
    {#if h.want}<p class="hline"><span class="hk">wants</span> {h.want}</p>{/if}
    {#if h.lie}<p class="hline"><span class="hk">lie</span> {h.lie}</p>{/if}
    {#if h.wound}<p class="hline"><span class="hk">wound</span> {h.wound}</p>{/if}
    {#if h.good_memory}<p class="hline"><span class="hk">good memory</span> {h.good_memory}</p>{/if}
    {#if h.secret}<p class="hline secret"><Icon name="eyeOff" size={12} /> {h.secret}</p>{/if}
  {/if}
{/snippet}

<div class="page withchat"><div class="col wide">
  <div class="head">
    <h2 class="title">New story</h2>
    <label class="modelpick" title="Generation model for this flow">
      <span>Model</span>
      <select bind:value={genModel}>
        {#each GEN_MODELS as g (g.group)}
          <optgroup label={g.group}>
            {#each g.items as [val, lbl] (val)}<option value={val}>{lbl}</option>{/each}
          </optgroup>
        {/each}
      </select>
    </label>
  </div>

  <div class="body">
    <div class="main">

    {#if isNew}
      {#if err}<p class="err">{err}</p>{/if}
      {#if !harnesses.length}
        <div class="newhint">
          <Icon name="sparkles" size={20} />
          <p>Start with the world — the stage your cast lives on. Then generate characters who belong to it.</p>
          <div class="genform">
            <div class="idearow">
              <textarea class="seed" use:autogrow={seed} bind:value={seed} rows="2"
                        placeholder="Your idea — a line or two. e.g. a shy boy and the popstar who keeps catching him watching her"></textarea>
              <button class="soft ib world-sg" onclick={suggestWorld} disabled={worldBusy || !seed.trim()}>
                <Icon name="sparkles" size={12} />{worldBusy ? 'Building…' : 'Suggest a world'}
              </button>
            </div>

            <div class="worldpanel">
              <div class="wtitle">World <span class="whint">the stage — edit freely or ↻ re-roll a field; the plot still emerges from the cast</span></div>
              {#snippet wfield(key, label, multiline)}
                <div class="wf">
                  <span class="wfhead">{label}
                    <button class="regen" class:spin={fieldBusy[key]} onclick={() => regenField(key)}
                            disabled={!!fieldBusy[key]} title="Regenerate {label.toLowerCase()}" aria-label="Regenerate {label}">
                      <Icon name="refresh" size={11} />
                    </button>
                  </span>
                  {#if multiline}
                    <textarea rows="2" use:autogrow={world[key]} bind:value={world[key]}></textarea>
                  {:else}
                    <input bind:value={world[key]} />
                  {/if}
                </div>
              {/snippet}
              <div class="wgrid">
                {@render wfield('genre', 'Genre', false)}
                {@render wfield('tone', 'Tone', false)}
              </div>
              {@render wfield('setting', 'Setting', true)}
              {@render wfield('situation', 'Situation', true)}
            </div>

            <div class="genrow">
              <label class="cnt">characters
                <input type="number" min="2" max="8" bind:value={castN} />
              </label>
              <button class="ib gen" onclick={genCast} disabled={!!busy || !canGen}>
                <Icon name="sparkles" />{busy === 'cast' ? 'Generating…' : 'Generate cast'}
              </button>
            </div>
          </div>
        </div>
      {:else}
        <div class="qbar">
          <span class="qhead">Cast · {confirmedCount}/{harnesses.length} confirmed</span>
          <span class="qhint">click a node to inspect · ★ marks the MC (you) — bonds point toward them</span>
          {#if facingCount}<span class="qhint gen"><Icon name="sparkles" size={11} /> rendering {facingCount} portrait{facingCount > 1 ? 's' : ''}…</span>{/if}
        </div>

        <div class="potwrap">
          <div class="potgraph">
            <RelationshipGraph cast={draftCast} relationships={potentialRels} size={400}
                               focus={leadId} selected={activeId} onSelect={selectNode} legend={false} directed={false} youFocus={true} />
            <p class="glegend"><b>★</b> MC (you) · <span class="lg solid"></span> drawn to you · <span class="lg dash"></span> open · click a node</p>
          </div>

          {#if activeChar}
            <aside class="potpanel detail">
              <div class="pprow">
                <span class="ppair">
                  {view === 'character' && activeChar.name ? activeChar.name : (activeChar.role || 'character')}
                  {#if activeId === leadId}<span class="leadtag">★ lead</span>{/if}
                </span>
                <button class="x" onclick={() => (activeId = '')} aria-label="close"><Icon name="x" size={15} /></button>
              </div>

              <div class="prow2">
                <div class="vtoggle" role="group" aria-label="Card view">
                  <button class:on={view === 'character'} onclick={() => (view = 'character')}>Character</button>
                  <button class:on={view === 'harness'} onclick={() => (view = 'harness')}>Harness</button>
                </div>
                <span class="dacts">
                  <button class="hb star" class:active={leadId === activeId} onclick={() => (leadId = activeId)} title="Make this the MC (the player's role)" aria-label="Make MC">★</button>
                  {#if view === 'character' && scaffolded(activeChar) && !fleshed(activeChar) && !formalizing[activeId]}
                    <button class="hb" onclick={() => formalize(activeChar)} title="Flesh out into a person" aria-label="Flesh out"><Icon name="sparkles" size={13} /></button>
                  {/if}
                  <button class="hb" class:spin={faceBusy[activeId]} onclick={() => genFace(activeChar, true)} disabled={!!faceBusy[activeId]} title="Generate face portrait" aria-label="Generate face"><Icon name="user" size={13} /></button>
                  <button class="hb ok" class:active={kept[activeId]} onclick={() => confirmH(activeId)} title="Confirm" aria-label="Confirm"><Icon name="check" size={14} /></button>
                  <button class="hb" onclick={() => { dropH(activeId); activeId = ''; }} title="Remove" aria-label="Remove"><Icon name="x" size={14} /></button>
                </span>
              </div>

              <input class="ginput" class:set={activeChar.group} list="draft-groups" value={activeChar.group || ''}
                     style="--ghue:{hueOf(activeChar.group)}"
                     placeholder="+ social group" title="Tag a visual cluster — groups don't create bonds"
                     onchange={(e) => setGroup(activeId, e.currentTarget.value)} />

              {@render charBody(activeChar)}

              {#if activeId !== leadId && leadId}
                {@const mine = activeRel ? sidesOf(activeRel, activeId)?.mine : null}
                <div class="potsection">
                  <div class="pslabel">{activeChar.name || activeChar.role}'s potential for closeness with <b>{leadH?.name || leadH?.role}</b> <span class="youtag">you</span> — the ground they could build on, and where they start. No outcome; play decides.</div>
                  {#if activeRel?.potential}
                    <div class="rpot picked">
                      {#if activeRel.potential}<p class="rline"><span class="rk">common core</span> {activeRel.potential}</p>{/if}
                      {#if mine}
                        <div class="rsides">
                          <div class="rside"><span class="rsname">{activeChar.name || activeChar.role} →</span> <span class="rst {mine.stance}">{mine.stance}</span>{#if mine.read} · {mine.read}{/if}</div>
                        </div>
                      {/if}
                    </div>
                  {/if}
                  <button class="soft ib sg" onclick={() => suggestFor(activeChar)} disabled={!!suggesting[activeId]}>
                    <Icon name="sparkles" size={12} /> {suggesting[activeId] ? 'Thinking…' : (activeRel?.potential ? 'Re-suggest' : 'Suggest a potential')}
                  </button>
                  {#if (suggestions[activeId] || []).length}
                    <div class="potcards col">
                      {#each suggestions[activeId] as pot, pi (pi)}
                        <div class="potcard">
                          <p class="pccore">{pot.common}</p>
                          <div class="psides">
                            <span class="pside"><span class="rsname">{activeChar.name || activeChar.role} →</span> <span class="pcst {pot.stance}">{pot.stance}</span>{#if pot.read} · {pot.read}{/if}</span>
                          </div>
                          <button class="ib pick" onclick={() => pickPotential(activeChar, pot)}><Icon name="check" size={12} /> Pick this</button>
                        </div>
                      {/each}
                    </div>
                  {/if}
                </div>
              {:else if activeId === leadId && others.length}
                <!-- The lead IS the MC (the player's role). Show how each NPC is drawn TOWARD them — each
                     row jumps into that NPC's potential editor. The MC's own feelings stay the player's. -->
                <div class="potsection">
                  <div class="pslabel"><b>{leadH?.name || leadH?.role}</b> is the MC <span class="youtag">you</span> — how the cast is drawn to you. Click one to shape it.</div>
                  {#each others as o (o.id)}
                    {@const rel = relWith(o.id)}
                    <button class="bondrow" onclick={() => (activeId = o.id)}>
                      <span class="bn">{o.name || o.role}</span>
                      {#if rel}
                        <span class="bnat {rel.stance || 'neutral'}">{rel.dynamic || 'drawn in'}</span>
                      {:else}
                        <span class="bnat open">define…</span>
                      {/if}
                    </button>
                  {/each}
                </div>
              {/if}
            </aside>
          {:else}
            <aside class="potpanel empty"><p class="none">Click a character in the web to inspect them — see their card, toggle harness ⇄ character, and shape their bond with the lead.</p></aside>
          {/if}
        </div>
        <datalist id="draft-groups">{#each groups as g (g)}<option value={g}></option>{/each}</datalist>

        <div class="webacts">
          <button class="soft ib" onclick={reweave} disabled={!!busy}><Icon name="refresh" />{busy === 'weave' ? 'Weaving…' : 'Re-weave'}</button>
          <button class="ib" onclick={() => derive()} disabled={!!busy}>{busy === 'derive' ? 'Deriving…' : 'Derive stories'}<Icon name="arrowRight" /></button>
        </div>
      {/if}
      {#if candidates.length}
        <div class="storybar">
          <input class="nm" bind:value={newName} placeholder="Story name (defaults to the candidate title)" />
          <div class="types">
            <button class="ty ib" class:on={newType === 'novel'} onclick={() => (newType = 'novel')}><Icon name="book" /> Novel</button>
            <button class="ty ib" class:on={newType === 'vn'} onclick={() => (newType = 'vn')}><Icon name="film" /> VN</button>
          </div>
        </div>
        <div class="cands">
          {#each candidates as c, i (i)}
            <div class="ccard" class:lead={i === 0}>
              {#if i === 0}<span class="badge">strongest tension</span>{/if}
              <p class="cq">{c.dramatic_question}</p>
              <p class="clog">{c.logline}</p>
              <div class="rides">{#each (c.anchors || []) as a (a)}<span class="ride" class:prot={a === c.protagonist}>{roleOf(a)}</span>{/each}</div>
              <table class="cmeta"><tbody>
                {#if c.stakes}<tr><td>stakes</td><td>{c.stakes}</td></tr>{/if}
                {#if c.intended_ending}<tr><td>ending</td><td>{c.intended_ending}</td></tr>{/if}
              </tbody></table>
              <button class="buildbtn ib" onclick={() => build(c)} disabled={!!busy}><Icon name="sparkles" />{busy === 'commit' ? 'Building…' : 'Build this one'}</button>
            </div>
          {/each}
        </div>
        <button class="soft more ib" onclick={() => derive('a different, darker angle on the same web')} disabled={!!busy}><Icon name="refresh" />Derive different stories</button>
      {/if}

    {/if}

    </div>
  </div>
</div></div>

<!-- New story: the chat is the entry — its first message explains how to start; it reshapes the
     DRAFT cast queue (nothing persists until you build). -->
<AgentChat storyKey={''} dock="left" propose initialMode={stories.draft?.chat?.mode || '_smith_tools'} firstMessage={FIRST_MSG}
           artifact={draftArtifact} label="CAST" target="draft" commit={false} onArtifact={applyDraft} model={genModel}
           initialConvo={stories.draft?.chat?.convo || []} initialHistory={stories.draft?.chat?.history || []}
           initialBehaviour={stories.draft?.chat?.activeBehaviour || ''} onConvo={onChat} />

<style>
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .title { margin: 0; font-size: 20px; font-weight: 680; }
  .err { color: var(--bad); font-size: 12.5px; margin: 8px 0 0; }
  .ib { display: inline-flex; align-items: center; gap: 6px; }
  .main { min-width: 0; }
  .page.withchat { padding-left: 356px; }

  /* new-story empty state — points to the chat (the entry) */
  .newhint { margin: 36px auto 18px; max-width: 440px; text-align: center; color: var(--muted);
    display: flex; flex-direction: column; align-items: center; gap: 10px; }
  .newhint p { margin: 0; line-height: 1.55; font-size: 13.5px; }
  .genform { width: 100%; display: flex; flex-direction: column; gap: 10px; margin-top: 4px; text-align: left; }
  .idearow { display: flex; flex-direction: column; gap: 6px; }
  .seed { width: 100%; resize: none; overflow: hidden; padding: 9px 11px; font: inherit; font-size: 13px; line-height: 1.45;
    border-radius: 10px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--text); }
  .seed:focus { outline: none; border-color: var(--accent); }
  .world-sg { align-self: flex-end; font-size: 11.5px; padding: 5px 11px; }

  /* World frame — the authored stage */
  .worldpanel { display: flex; flex-direction: column; gap: 8px; padding: 11px 12px; border-radius: 11px;
    background: var(--elev-2); border: 1px solid var(--border-soft); }
  .wtitle { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .whint { font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--faint); margin-left: 6px; }
  .wgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .wf { display: flex; flex-direction: column; gap: 3px; }
  .wfhead { display: flex; align-items: center; justify-content: space-between; gap: 6px;
    font-size: 10px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); }
  .regen { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px;
    padding: 0; border: 0; background: none; color: var(--faint); cursor: pointer; border-radius: 5px; }
  .regen:hover:not(:disabled) { color: var(--accent); background: var(--elev); }
  .regen:disabled { opacity: .5; cursor: default; }
  .regen.spin :global(svg) { animation: rgspin 1s linear infinite; }
  @keyframes rgspin { to { transform: rotate(360deg); } }
  .wf input, .wf textarea { width: 100%; resize: none; overflow: hidden; padding: 6px 9px; font: inherit; font-size: 12.5px;
    line-height: 1.45; border-radius: 8px; background: var(--panel); border: 1px solid var(--border-soft); color: var(--text); }
  .wf input:focus, .wf textarea:focus { outline: none; border-color: var(--accent); }
  .modelpick { display: inline-flex; align-items: center; gap: 6px; }
  .modelpick span { font-size: 10px; text-transform: uppercase; letter-spacing: .3px; font-weight: 600; color: var(--faint); }
  .modelpick select { padding: 5px 8px; font-size: 12px; border-radius: 8px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .genrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .cnt { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--muted); }
  .cnt input { width: 52px; padding: 5px 7px; font: inherit; font-size: 13px; text-align: center;
    border-radius: 7px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--text); }
  .gen { padding: 7px 14px; justify-content: center; }
  .qbar { display: flex; align-items: baseline; gap: 12px; margin: 22px 0 10px; flex-wrap: wrap; }
  .qhead { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .qhint { font-size: 11.5px; color: var(--faint); }
  .qhint.gen { color: var(--accent); display: inline-flex; align-items: center; gap: 4px; }
  .vtoggle { display: inline-flex; gap: 2px; padding: 2px; border-radius: 9px; background: var(--elev-2); border: 1px solid var(--border-soft); }
  .vtoggle button { font: inherit; font-size: 11.5px; padding: 4px 12px; border: 0; border-radius: 7px; cursor: pointer;
    background: transparent; color: var(--muted); }
  .vtoggle button.on { background: var(--panel); color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.25); }

  /* compact chip rows — flaws/strengths/hobbies (cuts the wordiness) */
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 6px 0 0; }
  .chip { font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev-2);
    border: 1px solid var(--border-soft); color: var(--muted); }
  .says { margin-top: 7px; display: flex; flex-direction: column; gap: 3px; }
  .say { margin: 0; font-size: 11.5px; font-style: italic; color: var(--muted); line-height: 1.4; }
  .ginput { width: 100%; margin: 1px 0 9px; padding: 3px 8px; font-size: 11px; border-radius: 7px;
    background: transparent; border: 1px dashed var(--border-soft); color: var(--muted); }
  .ginput:focus { outline: none; border-style: solid; border-color: var(--accent); }
  .ginput.set { border-style: solid; border-color: hsl(var(--ghue) 55% 58%);
    color: hsl(var(--ghue) 55% 70%); background: hsl(var(--ghue) 55% 58% / .08); }
  .hb { width: 24px; height: 24px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
    border-radius: 7px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .hb.ok.active { background: color-mix(in srgb, var(--accent) 20%, transparent); border-color: var(--accent); color: var(--accent); }
  .hb.star { font-size: 13px; line-height: 1; }
  .hb.star.active { background: color-mix(in srgb, #f5c518 22%, transparent); border-color: #f5c518; color: #f5c518; }
  .hb:hover { filter: brightness(1.12); }
  .hb.spin { opacity: .7; animation: hbpulse 1s ease-in-out infinite; }
  @keyframes hbpulse { 0%,100% { opacity: .4; } 50% { opacity: .9; } }

  /* graph-as-cast-queue: ring on the left, the clicked node's inspector on the right */
  .potwrap { display: flex; gap: 16px; align-items: flex-start; flex-wrap: wrap; margin-top: 4px; }
  .potgraph { flex: 1; min-width: 300px; position: sticky; top: 8px; }
  .glegend { display: flex; align-items: center; gap: 6px; justify-content: center; margin: 4px 0 0;
    font-size: 10.5px; color: var(--faint); }
  .lg { width: 16px; height: 0; border-top: 2px solid var(--faint); display: inline-block; margin-right: 2px; }
  .lg.solid { border-color: var(--good, #6ec77f); }
  .lg.dash { border-top-style: dashed; }
  .potpanel { flex: 0 0 300px; max-width: 330px; border: 1px solid var(--border-soft); border-radius: 12px;
    padding: 12px 13px; background: var(--panel); }
  .potpanel.empty { display: flex; align-items: center; min-height: 120px; }
  .pprow { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
  .ppair { font-size: 14px; font-weight: 640; color: var(--text); display: inline-flex; align-items: center; gap: 7px; }
  .leadtag { font-size: 10px; font-weight: 600; padding: 1px 7px; border-radius: 999px;
    background: color-mix(in srgb, #f5c518 20%, transparent); color: #f5c518; }
  .prow2 { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 9px; }
  .dacts { display: inline-flex; gap: 4px; flex: none; }
  .potsection { margin-top: 12px; padding-top: 11px; border-top: 1px solid var(--border-soft); }
  .pslabel { font-size: 11.5px; color: var(--muted); line-height: 1.5; margin-bottom: 8px;
    display: inline-flex; align-items: center; gap: 4px; flex-wrap: wrap; }
  .pslabel b { color: var(--text); }
  .youtag { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    padding: 1px 6px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }
  .bondrow { display: flex; align-items: center; justify-content: space-between; gap: 8px; width: 100%;
    padding: 6px 9px; margin: 3px 0; border-radius: 8px; background: var(--elev-2);
    border: 1px solid var(--border-soft); color: var(--text); cursor: pointer; font: inherit; }
  .bondrow:hover { border-color: var(--accent); }
  .bn { font-size: 12.5px; font-weight: 600; }
  .bnat { font-size: 10.5px; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--border-soft); color: var(--muted); }
  .bnat.warm, .bnat.devoted { color: var(--good, #6ec77f); border-color: color-mix(in srgb, var(--good, #6ec77f) 40%, transparent); }
  .bnat.strained, .bnat.hostile { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, transparent); }
  .bnat.open { color: var(--faint); font-style: italic; }
  .sg { font-size: 11.5px; padding: 5px 11px; width: 100%; justify-content: center; }
  .potcards.col { grid-template-columns: 1fr; margin-top: 10px; }
  .rpot { margin-top: 6px; }
  .rpot.picked { margin: 0 0 10px; padding: 8px 10px; border-radius: 9px; background: var(--elev-2); border: 1px solid var(--border-soft); }
  .rnat { font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--accent); }
  .rline { margin: 4px 0 0; font-size: 12px; color: var(--muted); line-height: 1.5; }
  .rk { font-size: 9.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-right: 6px; }
  .potcards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin-top: 8px; }
  .potcard { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 11px; background: var(--panel); display: flex; flex-direction: column; gap: 5px; }
  .pchead { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .pcnat { font-size: 12px; font-weight: 640; color: var(--text); }
  .pcst { font-size: 10px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border-soft); color: var(--muted); }
  .pcst.warm, .pcst.devoted, .rst.warm, .rst.devoted { color: var(--good, #6ec77f); border-color: color-mix(in srgb, var(--good, #6ec77f) 40%, transparent); }
  .pcst.strained, .pcst.hostile, .rst.strained, .rst.hostile { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 40%, transparent); }
  /* a bond reads both ways — each character's side, stacked */
  .rsides { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; padding-top: 7px; border-top: 1px dashed var(--border-soft); }
  .rside { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
  .rsname { color: var(--text); font-weight: 600; }
  .rst { font-size: 9.5px; padding: 1px 7px; border-radius: 999px; border: 1px solid var(--border-soft); color: var(--muted); }
  .psides { display: flex; flex-direction: column; gap: 3px; margin: 2px 0; }
  .pside { font-size: 10.5px; color: var(--muted); display: flex; align-items: center; gap: 5px; flex-wrap: wrap; }
  .pccore { margin: 0; font-size: 11.5px; color: var(--text); line-height: 1.5; opacity: .92; }
  .pctraj { margin: 0; font-size: 11.5px; color: var(--muted); line-height: 1.5; font-style: italic; }
  .pick { align-self: flex-start; font-size: 11.5px; padding: 4px 11px; margin-top: 2px; }
  .hpersona { margin: 0 0 7px; font-size: 12px; color: var(--text); line-height: 1.5; opacity: .92; }
  .hwork { margin: 0 0 5px; font-size: 11px; color: var(--accent); font-style: italic; }
  .htemp { margin: 0 0 5px; font-size: 11.5px; color: var(--muted); line-height: 1.45; font-style: italic; }
  .hline { margin: 1px 0; font-size: 12px; color: var(--muted); line-height: 1.4; }
  .hline.secret { display: flex; align-items: center; gap: 5px; color: color-mix(in srgb, var(--bad) 80%, var(--muted)); }
  .hk { font-size: 10px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-right: 5px; }
  .webacts { display: flex; justify-content: flex-end; gap: 8px; margin: 14px 0 4px; }
  .storybar { display: flex; gap: 10px; align-items: center; margin: 22px 0 10px; }
  .nm { flex: 1; }
  .types { display: flex; gap: 4px; } .ty { font-size: 12px; padding: 6px 12px; } .ty.on { border-color: var(--accent); color: var(--accent); }
  .cands { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
  .ccard { border: 1px solid var(--border); border-radius: 12px; padding: 14px 15px; background: var(--panel); display: flex; flex-direction: column; gap: 8px; }
  .ccard.lead { border-color: var(--accent); }
  .badge { align-self: flex-start; font-size: 10.5px; padding: 2px 9px; border-radius: 999px; background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); }
  .cq { margin: 0; font-size: 14.5px; font-weight: 600; line-height: 1.35; color: var(--text); }
  .clog { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; }
  .rides { display: flex; flex-wrap: wrap; gap: 5px; }
  .ride { font-size: 11px; padding: 2px 9px; border-radius: 999px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .ride.prot { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  .cmeta { font-size: 12px; color: var(--muted); border-collapse: collapse; }
  .cmeta td:first-child { color: var(--faint); text-transform: uppercase; font-size: 10px; letter-spacing: .3px; padding: 2px 10px 2px 0; vertical-align: top; white-space: nowrap; }
  .cmeta td { padding: 2px 0; }
  .buildbtn { margin-top: 4px; width: 100%; justify-content: center; }
  .more { margin: 12px auto 0; width: max-content; }

  /* shared with the genesis inspector */
  .x { background: none; border: 0; color: var(--faint); cursor: pointer; padding: 0; display: inline-flex; }
  .none { font-size: 12px; color: var(--faint); margin: 8px 0; }
</style>

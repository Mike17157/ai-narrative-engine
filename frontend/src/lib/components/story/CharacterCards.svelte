<script>
  // Narrative character CARDS with the tiered depth ladder, gated per arc STAGE.
  // The card = base (surface + voice) + ladder (tell → shape → full). Each stage unlocks the next
  // tier; a locked tier's content is ABSENT by design (the gate is the content — [[storymaster-pacing]]).
  // Presentation-first: maps to real fields where they exist (surface←role/system, shape←wound); the
  // NEW tiers (voice/tell/full) show as gated authorable slots. The stage selector reveals tiers live.
  import { page } from '$app/stores';
  import { stories } from '$lib/stories.svelte.js';
  import { chars, charName } from '$lib/characters.svelte.js';

  let st = $derived(stories.current);
  let cast = $derived(st?.cast || []);
  let primary = $derived(cast.find((m) => m.primary)?.character || cast[0]?.character);

  // Stages = the arc spine (base + ladder mutated per arc). No arcs yet → a representative 3-stage
  // schedule so the gate design is visible. Each ladder tier unlocks at a stage index.
  let stages = $derived((st?.arcs || []).length
    ? st.arcs.map((a, i) => ({ label: a.name || `Arc ${i + 1}` }))
    : [{ label: 'Arc 1' }, { label: 'Arc 2' }, { label: 'Arc 3' }]);
  let gate = $derived({ tell: 0, shape: Math.max(1, Math.floor(stages.length / 2)), full: Math.max(2, stages.length - 1) });

  // Selected stage — seeded from ?stage, driven by the segmented selector.
  let stageIdx = $state(0);
  let seededStage = -1;
  $effect(() => {
    const q = parseInt($page.url.searchParams.get('stage') ?? '', 10);
    const v = Number.isFinite(q) ? q : 0;
    if (seededStage !== v) { seededStage = v; stageIdx = Math.min(Math.max(0, v), stages.length - 1); }
  });
  let charParam = $derived($page.url.searchParams.get('char') || '');

  const firstSentence = (s) => (s || '').split(/(?<=[.!?])\s/)[0]?.trim() || '';

  // Build a card model per cast member from real fields; new tiers left unauthored (gated slots).
  let cards = $derived(cast.map((m) => {
    const ci = (chars.list || []).find((c) => c.key === m.character) || {};
    const f = ci.fields || {};
    return {
      key: m.character, name: charName(m.character) || m.character, primary: m.primary,
      role: f.role || '', img: ci.reference || ci.avatar || null,
      surface: [f.role, firstSentence(ci.system)].filter(Boolean).join(' — ') || firstSentence(ci.system),
      voice: null,                         // NEW tier — a representative line, authored later
      tell: null,                          // NEW tier — an observable, unexplained behaviour
      shape: f.wound || null,              // maps to the existing wound (the outline)
      full: null,                          // NEW tier — the act of love (the why)
      psych: [['want', f.want], ['need', f.need], ['lie', f.lie]].filter(([, v]) => v),
    };
  }));

  const LADDER = [
    { k: 'tell', label: 'Tell', note: 'an observable, unexplained habit' },
    { k: 'shape', label: 'Shape', note: 'the outline of the wound' },
    { k: 'full', label: 'Full', note: 'the act of love beneath it' },
  ];
  const unlocked = (k) => stageIdx >= gate[k];
  const justHere = (k) => gate[k] === stageIdx;   // the tier this stage reveals
  const shownTiers = $derived(LADDER.filter((t) => unlocked(t.k)).map((t) => t.label.toLowerCase()));
</script>

<div class="wrap">
  <header class="top">
    <div class="ttl">
      <h1>Characters</h1>
      <p class="sub">The narrative cards — <b>base</b> + the depth ladder (<i>tell → shape → full</i>),
        gated per arc. A locked tier isn't hidden text — it isn't on the card yet. Wardrobe &amp; sprites
        live in <b>Outfits</b>.</p>
    </div>
    <!-- STAGE selector — moving it reveals the next tier across the whole cast at once -->
    <div class="stagepick" role="tablist" aria-label="Arc stage">
      {#each stages as s, i (i)}
        <button class="stage" class:on={stageIdx === i} role="tab" aria-selected={stageIdx === i}
                onclick={() => (stageIdx = i)}>{s.label}</button>
      {/each}
    </div>
  </header>

  <p class="gatebar">At <b>{stages[stageIdx]?.label}</b> the card shows:
    <span class="pill base">base</span>{#each shownTiers as t}<span class="pill on">{t}</span>{/each}
    {#each LADDER.filter((l) => !unlocked(l.k)) as l}<span class="pill lock">🔒 {l.label.toLowerCase()}</span>{/each}
  </p>

  <div class="cards">
    {#each cards as c (c.key)}
      <article class="card" class:target={charParam === c.key} id={`card-${c.key}`}>
        <div class="chead">
          <div class="avatar" class:ph={!c.img}>{#if c.img}<img src={c.img} alt="" />{:else}{c.name[0]}{/if}</div>
          <div class="cid">
            <div class="cname">{#if c.primary}<span class="star">★</span>{/if}{c.name}</div>
            {#if c.role}<div class="crole">{c.role}</div>{/if}
          </div>
        </div>

        <!-- BASE — always on the card -->
        <section class="tier base">
          <div class="tlabel"><span class="dot base"></span>Base <span class="tnote">surface + voice</span></div>
          {#if c.surface}<p class="tcontent">{c.surface}</p>{:else}<p class="slot">＋ surface</p>{/if}
          {#if c.voice}<p class="voice">“{c.voice}”</p>{:else}<p class="slot">＋ a representative line (voice)</p>{/if}
        </section>

        <!-- LADDER — gated by the selected stage -->
        {#each LADDER as t (t.k)}
          {@const open = unlocked(t.k)}
          <section class="tier" class:locked={!open} class:reveal={open && justHere(t.k)}>
            <div class="tlabel">
              <span class="dot" class:on={open}></span>{t.label}
              <span class="tnote">{t.note}</span>
              {#if open && justHere(t.k)}<span class="revtag">revealed here</span>{/if}
            </div>
            {#if !open}
              <p class="lockmsg">🔒 unlocks at <b>{stages[gate[t.k]]?.label || `Arc ${gate[t.k] + 1}`}</b> — not on the card yet</p>
            {:else if c[t.k]}
              <p class="tcontent">{c[t.k]}</p>
            {:else}
              <p class="slot">＋ author this character’s {t.label.toLowerCase()}</p>
            {/if}
          </section>
        {/each}

        {#if c.psych.length}
          <div class="psych">
            {#each c.psych as [k, v]}<div class="prow"><span class="pk">{k}</span><span class="pv">{v}</span></div>{/each}
          </div>
        {/if}
      </article>
    {/each}
    {#if !cards.length}<p class="empty">No characters in this story yet.</p>{/if}
  </div>
</div>

<style>
  .wrap { padding: 20px 26px 60px; max-width: 1180px; }
  .top { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; flex-wrap: wrap; }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 800; }
  .sub { margin: 0; max-width: 620px; font-size: 12.5px; color: var(--muted); line-height: 1.55; }
  .sub b { color: var(--text); } .sub i { color: var(--accent); font-style: normal; }

  /* stage selector */
  .stagepick { display: inline-flex; padding: 3px; gap: 2px; background: var(--elev); border: 1px solid var(--border); border-radius: 10px; flex: none; }
  .stage { padding: 6px 14px; border: none; background: none; border-radius: 7px; font-size: 12.5px; font-weight: 600;
           color: var(--muted); cursor: pointer; white-space: nowrap; transition: background .12s, color .12s; }
  .stage:hover { color: var(--text); }
  .stage.on { background: var(--accent); color: #0b0e14; }

  .gatebar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin: 16px 0 18px;
             font-size: 12px; color: var(--faint); }
  .pill { font-size: 11px; font-weight: 700; padding: 2px 9px; border-radius: 999px; text-transform: lowercase; }
  .pill.base { background: var(--elev-2, #222838); color: var(--text); }
  .pill.on { background: color-mix(in srgb, var(--accent) 20%, transparent); color: var(--accent); }
  .pill.lock { background: none; border: 1px dashed var(--border); color: var(--faint); font-weight: 600; }

  /* cards — ONE full-width column: this surface exists to see EVERY tier in full, nothing clipped */
  .cards { display: flex; flex-direction: column; gap: 16px; }
  .card { border: 1px solid var(--border-soft); border-radius: 14px; background: var(--panel);
          padding: 16px 20px 14px; display: flex; flex-direction: column; gap: 2px; }
  .card.target { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }

  .chead { display: flex; align-items: center; gap: 11px; margin-bottom: 8px; }
  .avatar { width: 42px; height: 42px; flex: none; border-radius: 11px; overflow: hidden; display: grid; place-items: center;
            background: var(--elev-2, #222838); color: var(--muted); font-weight: 800; font-size: 17px; }
  .avatar img { width: 100%; height: 100%; object-fit: cover; }
  .cname { font-size: 15px; font-weight: 800; display: flex; align-items: center; gap: 5px; }
  .star { color: #ffd45e; font-size: 12px; }
  .crole { font-size: 11.5px; color: var(--muted); }

  /* tiers */
  .tier { padding: 9px 0; border-top: 1px solid var(--border-soft); }
  .tier.base { border-top: none; }
  .tlabel { display: flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 700;
            text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .tnote { text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--faint); font-size: 10.5px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--border); }
  .dot.base, .dot.on { background: var(--accent); }
  .tcontent { margin: 5px 0 0; font-size: 13px; color: var(--text); line-height: 1.5; }
  .voice { margin: 6px 0 0; font-size: 12.5px; color: var(--muted); font-style: italic; line-height: 1.5; }
  .slot { margin: 5px 0 0; font-size: 12px; color: var(--faint); padding: 5px 9px; border: 1px dashed var(--border);
          border-radius: 8px; display: inline-block; cursor: default; }
  .tier.locked { opacity: .82; }
  .lockmsg { margin: 5px 0 0; font-size: 12px; color: var(--faint); }
  .lockmsg b { color: var(--muted); }
  .tier.reveal { background: color-mix(in srgb, var(--accent) 7%, transparent); border-radius: 9px;
                 margin: 0 -10px; padding-left: 10px; padding-right: 10px; }
  .revtag { margin-left: auto; font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .4px;
            color: var(--accent); background: color-mix(in srgb, var(--accent) 15%, transparent); padding: 2px 7px; border-radius: 999px; }

  .psych { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border-soft); }
  .prow { display: flex; gap: 9px; font-size: 12px; color: var(--muted); line-height: 1.5; }
  .pk { flex: none; width: 42px; color: var(--accent); text-transform: uppercase; font-size: 9.5px;
        font-weight: 700; letter-spacing: .3px; padding-top: 2px; }
  .pv { flex: 1; min-width: 0; }   /* full text — wraps to as many lines as needed, never clipped */
  .empty { color: var(--faint); font-size: 13px; padding: 30px 0; }
</style>

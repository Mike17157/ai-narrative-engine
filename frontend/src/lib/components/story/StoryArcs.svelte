<script>
  // The Arcs page = the SPINE ([[storymaster-pacing]]). Each arc is a MISSION whose completion is the
  // boundary; the boundary CHANGES the world conditions and UNLOCKS the next tier of every character's
  // secrets. Card = Mission · Condition-change · Phenomenon ceiling · per-character Secret-gate tiers ·
  // the ordered Scene beats (the checklist the code-driven pointer walks; last = the dramatized climax).
  // Presentation-first: maps real arcs where present; else a representative EXAMPLE spine (real cast in
  // the gate rows) so the design is visible. Single column, nothing truncated.
  import { stories } from '$lib/stories.svelte.js';
  import { charName } from '$lib/characters.svelte.js';
  import { arcSpine, ACCESS } from '$lib/storyspine.js';

  let st = $derived(stories.current);
  let primary = $derived(st?.cast?.find((m) => m.primary)?.character || st?.cast?.[0]?.character);
  let spine = $derived(arcSpine(st));   // ONE source of truth (shared with Scenes pane + navigator)
  let arcs = $derived(spine.arcs);
  let isExample = $derived(spine.isExample);

  const TIER = { locked: { l: '🔒 locked', c: 'lock' }, tell: { l: 'tell', c: 'tell' }, shape: { l: 'shape', c: 'shape' }, full: { l: 'full', c: 'full' }, present: { l: 'present', c: 'present' } };
</script>

<div class="wrap">
  <header class="top">
    <div class="ttl">
      <h1>Arcs <span class="lo">· the spine</span></h1>
      <p class="sub">Each arc is a <b>mission</b> whose completion is the <b>boundary</b> — it changes the
        world's conditions and unlocks the next tier of every character's secrets. The scene beats are the
        ordered checklist the story walks; the last is the dramatized climax.</p>
    </div>
  </header>

  {#if isExample}
    <div class="examplebar">Example spine — no arcs generated yet. This shows the shape; generate the
      spine to replace it with your story's arcs.</div>
  {/if}

  <div class="arcs">
    {#each arcs as a (a.n)}
      <article class="arc">
        <div class="ahead">
          <span class="anum">{a.n}</span>
          <span class="atitle">{a.title}</span>
          {#if a.tag}<span class="tag">{a.tag}</span>{/if}
          {#if a.access && ACCESS[a.access]}
            <span class="access" style:color={ACCESS[a.access].color}
                  style:background={`color-mix(in srgb, ${ACCESS[a.access].color} 15%, transparent)`}
                  title="how much of the story's hidden depth is open at this arc">
              <span class="adot" style:background={ACCESS[a.access].color}></span>{ACCESS[a.access].label}</span>
          {/if}
        </div>

        <div class="field mission">
          <div class="flabel">Mission <span class="fnote">completing it = the boundary</span></div>
          {#if a.mission}<p class="ftext">{a.mission}</p>{:else}<p class="slot">＋ the goal that ends this arc</p>{/if}
        </div>

        <div class="field">
          <div class="flabel">When it completes →</div>
          {#if a.boundary}<p class="ftext cond">{a.boundary}</p>{:else}<p class="slot">＋ the condition-change it triggers</p>{/if}
        </div>

        <div class="field">
          <div class="flabel">Phenomenon ceiling <span class="fnote">the most the strange escalates this arc</span></div>
          {#if a.ceiling}<p class="ftext">{a.ceiling}</p>{:else}<p class="slot">＋ the escalation cap</p>{/if}
        </div>

        <div class="field">
          <div class="flabel">Secret gates <span class="fnote">the depth tier unlocked per character this arc</span></div>
          {#if a.gates.length}
            <div class="gates">
              {#each a.gates as g (g.key)}
                {@const t = TIER[g.tier] || TIER.locked}
                <span class="gate {t.c}">{#if g.key === primary}<span class="star">★</span>{/if}{charName(g.key) || g.key}<span class="gtier">{t.l}</span></span>
              {/each}
            </div>
          {:else}<p class="slot">＋ assign each character's tier</p>{/if}
        </div>

        <div class="field">
          <div class="flabel">Scenes <span class="fnote">the ordered beats — last is the played climax</span></div>
          {#if a.scenes.length}
            <ol class="scenes">
              {#each a.scenes as s, i (i)}
                <li class:climax={s.climax}>{s.title}{#if s.climax}<span class="climaxtag">climax</span>{/if}</li>
              {/each}
            </ol>
          {:else}<p class="slot">＋ the arc's scene beats</p>{/if}
        </div>
      </article>
    {/each}
  </div>
</div>

<style>
  .wrap { padding: 20px 26px 60px; max-width: 1000px; }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 800; }
  h1 .lo { color: var(--faint); font-weight: 500; font-size: 16px; }
  .sub { margin: 0; max-width: 720px; font-size: 12.5px; color: var(--muted); line-height: 1.55; }
  .sub b { color: var(--text); }
  .examplebar { margin: 14px 0 0; padding: 8px 12px; border-radius: 9px; font-size: 12px; color: var(--faint);
                background: var(--elev); border: 1px dashed var(--border); }

  .arcs { display: flex; flex-direction: column; gap: 16px; margin-top: 16px; }
  .arc { border: 1px solid var(--border-soft); border-radius: 14px; background: var(--panel); padding: 16px 20px 14px;
         display: flex; flex-direction: column; gap: 2px; }

  .ahead { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
  .anum { width: 26px; height: 26px; flex: none; border-radius: 50%; display: grid; place-items: center;
          font-size: 12px; font-weight: 800; background: var(--accent); color: #0b0e14; }
  .atitle { font-size: 16px; font-weight: 800; color: var(--text); }
  .tag { font-size: 10.5px; font-weight: 700; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent);
         padding: 2px 9px; border-radius: 999px; }
  .access { display: inline-flex; align-items: center; gap: 5px; margin-left: auto; font-size: 10.5px; font-weight: 700;
            padding: 2px 10px; border-radius: 999px; white-space: nowrap; }
  .adot { width: 7px; height: 7px; border-radius: 50%; flex: none; }

  .field { padding: 9px 0; border-top: 1px solid var(--border-soft); }
  .field.mission { border-top: none; }
  .flabel { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .fnote { text-transform: none; letter-spacing: 0; font-weight: 500; color: var(--faint); font-size: 10.5px; margin-left: 8px; }
  .ftext { margin: 5px 0 0; font-size: 13px; color: var(--text); line-height: 1.55; }
  .ftext.cond { color: var(--accent); }
  .slot { margin: 5px 0 0; font-size: 12px; color: var(--faint); padding: 5px 9px; border: 1px dashed var(--border);
          border-radius: 8px; display: inline-block; }

  .gates { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .gate { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; padding: 3px 5px 3px 10px;
          border-radius: 999px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); }
  .gate .star { color: #ffd45e; font-size: 10px; }
  .gtier { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; padding: 2px 7px; border-radius: 999px; }
  .gate.lock .gtier, .gate.present .gtier { background: var(--border); color: var(--faint); }
  .gate.tell .gtier { background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }
  .gate.shape .gtier { background: color-mix(in srgb, #d68cff 22%, transparent); color: #d68cff; }
  .gate.full .gtier { background: color-mix(in srgb, #ff9e6d 24%, transparent); color: #ff9e6d; }

  .scenes { margin: 6px 0 0; padding-left: 20px; display: flex; flex-direction: column; gap: 4px; }
  .scenes li { font-size: 12.5px; color: var(--muted); line-height: 1.5; }
  .scenes li.climax { color: var(--text); font-weight: 600; }
  .climaxtag { margin-left: 8px; font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .4px;
               color: var(--accent); background: color-mix(in srgb, var(--accent) 15%, transparent); padding: 1px 6px; border-radius: 999px; }
</style>

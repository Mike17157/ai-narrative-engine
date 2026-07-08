<script>
  // The Scenes pane — SCOPED to one arc (scenes are scoped into arcs). Pick an arc → see only its
  // scenes, each as its FULL PLAN (the storymaster scene brief: what happens · tone · day/time · POV ·
  // location · cast · what it serves · what stays in shadow). See [[storymaster-pacing]].
  // Presentation-first: real arc.nodes where present; else a representative EXAMPLE (real cast).
  import { page } from '$app/stores';
  import { stories } from '$lib/stories.svelte.js';
  import { arcSpine } from '$lib/storyspine.js';

  let st = $derived(stories.current);
  let spine = $derived(arcSpine(st));   // ONE source of truth (shared with Arcs page + navigator)
  let arcs = $derived(spine.arcs);
  let isExample = $derived(spine.isExample);

  // SCOPE to one arc — seeded from ?arc, switchable via the selector.
  let selId = $state('');
  let seeded = '';
  $effect(() => { const q = $page.url.searchParams.get('arc') || ''; if (seeded !== q) { seeded = q; selId = q; } });
  let arc = $derived(arcs.find((a) => a.id === selId) || arcs[0] || null);
</script>

<div class="wrap">
  <header class="top">
    <h1>Scenes</h1>
    <p class="sub">Scenes are scoped into arcs — pick an arc to see <b>only its scenes</b>, each shown as
      its full plan. The last scene in an arc is the played climax.</p>
    {#if arcs.length > 1}
      <div class="arcpick" role="tablist">
        {#each arcs as a (a.id)}
          <button class="apick" class:on={arc?.id === a.id} role="tab" aria-selected={arc?.id === a.id}
                  onclick={() => (selId = a.id)}>{a.title}</button>
        {/each}
      </div>
    {/if}
  </header>

  {#if isExample}
    <div class="examplebar">Example — no scenes generated yet. This shows the full-plan shape; the story
      master fills each arc's beats when the spine runs.</div>
  {/if}

  {#if arc}
    <div class="archead"><span class="anum">{arc.n}</span><span class="atitle">{arc.title}</span>
      <span class="count">{arc.scenes.length} scene{arc.scenes.length === 1 ? '' : 's'}</span></div>

    {#if arc.scenes.length}
      <ol class="scenes">
        {#each arc.scenes as s, i (i)}
          <li class="scene" class:climax={s.climax}>
            <div class="shead">
              <span class="snum">{i + 1}</span>
              <span class="stitle">{s.title}</span>
              {#if s.tone}<span class="tone">{s.tone}</span>{/if}
              {#if s.climax}<span class="climaxtag">climax</span>{/if}
            </div>
            {#if s.beat}<p class="beat">{s.beat}</p>{/if}
            <div class="brief">
              {#if s.day}<div class="bf"><span class="bk">When</span><span class="bv">{s.day}</span></div>{/if}
              {#if s.pov}<div class="bf"><span class="bk">POV</span><span class="bv">{s.pov}</span></div>{/if}
              {#if s.location}<div class="bf"><span class="bk">Location</span><span class="bv">{s.location}</span></div>{/if}
              {#if s.cast?.length}<div class="bf"><span class="bk">Cast</span><span class="bv">{s.cast.join(', ')}</span></div>{/if}
            </div>
            {#if s.serves}<div class="line"><span class="lk">Why it's here</span><span class="lv">{s.serves}</span></div>{/if}
            {#if s.shadow}<div class="line"><span class="lk shadow">Kept hidden</span><span class="lv">{s.shadow}</span></div>{/if}
          </li>
        {/each}
      </ol>
    {:else}
      <p class="none">No scenes in this arc yet.</p>
    {/if}
  {:else}
    <p class="none">No arcs yet — scenes live inside arcs.</p>
  {/if}
</div>

<style>
  .wrap { padding: 20px 26px 60px; max-width: 1000px; }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 800; }
  .sub { margin: 0 0 12px; max-width: 720px; font-size: 12.5px; color: var(--muted); line-height: 1.55; }
  .sub b { color: var(--text); }
  .arcpick { display: inline-flex; gap: 2px; padding: 3px; background: var(--elev); border: 1px solid var(--border); border-radius: 10px; }
  .apick { padding: 6px 13px; border: none; background: none; border-radius: 7px; font-size: 12.5px; font-weight: 600; color: var(--muted); cursor: pointer; white-space: nowrap; }
  .apick:hover { color: var(--text); }
  .apick.on { background: var(--accent); color: #0b0e14; }

  .examplebar { margin: 8px 0 0; padding: 8px 12px; border-radius: 9px; font-size: 12px; color: var(--faint); background: var(--elev); border: 1px dashed var(--border); }

  .archead { display: flex; align-items: center; gap: 9px; margin: 18px 0 12px; padding-bottom: 8px; border-bottom: 1px solid var(--border-soft); }
  .anum { width: 22px; height: 22px; flex: none; border-radius: 50%; display: grid; place-items: center; font-size: 11px; font-weight: 800; background: var(--accent); color: #0b0e14; }
  .atitle { font-size: 15px; font-weight: 800; color: var(--text); }
  .count { font-size: 11px; color: var(--faint); margin-left: auto; }

  .scenes { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
  .scene { padding: 14px 16px; border: 1px solid var(--border-soft); border-radius: 12px; background: var(--panel); }
  .scene.climax { border-color: color-mix(in srgb, var(--accent) 40%, var(--border-soft)); }
  .shead { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
  .snum { width: 24px; height: 24px; flex: none; border-radius: 7px; display: grid; place-items: center; font-size: 12px; font-weight: 700; background: var(--elev-2, #222838); color: var(--muted); }
  .scene.climax .snum { background: var(--accent); color: #0b0e14; }
  .stitle { font-size: 14px; font-weight: 800; color: var(--text); }
  .tone { font-size: 10.5px; color: var(--accent); background: color-mix(in srgb, var(--accent) 13%, transparent); padding: 2px 9px; border-radius: 999px; }
  .climaxtag { font-size: 9px; font-weight: 800; text-transform: uppercase; letter-spacing: .4px; color: var(--accent); background: color-mix(in srgb, var(--accent) 15%, transparent); padding: 2px 7px; border-radius: 999px; }
  .beat { margin: 9px 0 0; font-size: 13px; color: var(--text); line-height: 1.55; }

  .brief { display: flex; flex-wrap: wrap; gap: 6px 8px; margin-top: 10px; }
  .bf { display: flex; align-items: baseline; gap: 6px; font-size: 11.5px; background: var(--elev); border: 1px solid var(--border-soft); padding: 3px 10px; border-radius: 8px; }
  .bk { color: var(--faint); text-transform: uppercase; font-size: 9px; font-weight: 700; letter-spacing: .3px; }
  .bv { color: var(--text); }

  .line { display: flex; gap: 9px; margin-top: 8px; font-size: 12px; line-height: 1.5; }
  .lk { flex: 0 0 66px; color: var(--muted); text-transform: uppercase; font-size: 9px; font-weight: 700; letter-spacing: .3px; padding-top: 2px; }
  .lk.shadow { color: #d68cff; }
  .lv { flex: 1; min-width: 0; color: var(--muted); }
  .none { font-size: 13px; color: var(--faint); padding: 20px 0; }
</style>

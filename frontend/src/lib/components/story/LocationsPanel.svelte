<script>
  // The story's LOCATIONS as an editable hierarchy — root locations are coloured containers that
  // hold their child locations, mirroring LocationRoster. Each location edits in place (name /
  // description / background prompt) AND manipulates its SCENE IMAGE: generate candidates → pick →
  // save (the candidate/select flow that used to live in SceneModal). Replaces the map flow-graph.
  import { post } from '$lib/api.js';

  // Text fields + scene-image (production) edit here; adding/removing/nesting/setting-start are
  // structural narrative ops → the section editor. (onAdd/onRemove/onSetStart props retired.)
  let { storyKey, locations = [], start = '', onChange = () => {} } = $props();

  let locIds = $derived(new Set(locations.map((l) => l.id)));
  let roots = $derived(locations.filter((l) => !l.parent || !locIds.has(l.parent)));
  const childrenOf = (id) => locations.filter((l) => l.parent === id);
  const rootHue = (i) => (i * 137 + 25) % 360;

  // ── Per-location scene-image generation (candidate → pick → save) ──
  const N = 4;
  let busy = $state({});      // loc.id → generating candidates
  let cands = $state({});     // loc.id → [dataURI]
  let err = $state({});
  let promptBusy = $state({});
  let tagBusy = $state({});

  async function genBg(loc) {
    if (busy[loc.id]) return;
    busy = { ...busy, [loc.id]: true }; err = { ...err, [loc.id]: null }; cands = { ...cands, [loc.id]: [] };
    for (let i = 0; i < N; i++) {
      const r = await post(`/stories/${storyKey}/locations/${loc.id}/background/candidate`, {});
      if (r.ok && r.data?.image) cands = { ...cands, [loc.id]: [...(cands[loc.id] || []), r.data.image] };
      else { err = { ...err, [loc.id]: r.data?.error || 'render failed' }; break; }
    }
    busy = { ...busy, [loc.id]: false };
  }
  async function pickBg(loc, dataUri) {
    const r = await post(`/stories/${storyKey}/locations/${loc.id}/background/select`, { data: dataUri });
    if (r.ok && r.data?.url) { loc.background = `${r.data.url}?t=${Date.now()}`; cands = { ...cands, [loc.id]: [] }; onChange(); }
    else err = { ...err, [loc.id]: r.data?.error || 'save failed' };
  }
  function clearCands(loc) { cands = { ...cands, [loc.id]: [] }; }

  async function regenPrompt(loc) {
    if (promptBusy[loc.id]) return;
    promptBusy = { ...promptBusy, [loc.id]: true };
    const r = await post(`/stories/${storyKey}/locations/${loc.id}/regen-prompt`, {});
    promptBusy = { ...promptBusy, [loc.id]: false };
    if (r.ok && r.data?.prompt) { loc.background_prompt = r.data.prompt; onChange(); }
    else err = { ...err, [loc.id]: r.data?.error || 'prompt gen failed' };
  }
  async function tagify(loc) {
    const text = (loc.background_prompt || '').trim();
    if (!text || tagBusy[loc.id]) return;
    tagBusy = { ...tagBusy, [loc.id]: true };
    const r = await post('/tagify', { text, kind: 'scene' });
    tagBusy = { ...tagBusy, [loc.id]: false };
    if (r.ok && r.data?.tags) { loc.background_prompt = r.data.tags; onChange(); }
  }
</script>

<div class="locs">
  <p class="hint">Every place in the world, grouped under its area. Text edits in place and each
    carries its own <b>scene image</b> (generate options and pick one). Add, remove, nest, or set the
    story <b>start</b> in the editor.</p>

  {#each roots as root, i (root.id)}
    <section class="root" style={`--rc: hsl(${rootHue(i)} 60% 60%)`}>
      {@render editor(root, true)}
      {#each childrenOf(root.id) as kid (kid.id)}
        <div class="kid">{@render editor(kid, false)}</div>
      {/each}
    </section>
  {/each}

  {#if !locations.length}
    <div class="none">No locations yet — ask the editor to add one.</div>
  {/if}
</div>

{#snippet editor(loc, isRoot)}
  <div class="loc" class:root-loc={isRoot}>
    <div class="ltop">
      <span class="marker">{isRoot ? '📍' : '↳'}</span>
      <input class="lname" bind:value={loc.name} oninput={onChange} placeholder="location name" />
      {#if start === loc.id}<span class="startbadge" title="The place the story opens in">◆ start</span>{/if}
    </div>

    <div class="lbody">
      <!-- Scene image -->
      <div class="img">
        {#if cands[loc.id]?.length || busy[loc.id]}
          <div class="cand-wrap">
            <div class="cand-grid">
              {#each cands[loc.id] || [] as c, ci (ci)}
                <button class="cand" onclick={() => pickBg(loc, c)} title="Use this image">
                  <img src={c} alt="candidate" /><span class="use">Use</span>
                </button>
              {/each}
              {#if busy[loc.id]}
                {#each Array(N - (cands[loc.id]?.length || 0)) as _, k (k)}<div class="cand ph"><span class="spin"></span></div>{/each}
              {/if}
            </div>
            {#if !busy[loc.id]}<button class="mini" onclick={() => clearCands(loc)}>cancel</button>{/if}
          </div>
        {:else if loc.background}
          <button class="thumb" onclick={() => genBg(loc)} title="Regenerate — new options">
            <img src={loc.background} alt={loc.name} /><span class="redo">↻ New options</span>
          </button>
        {:else}
          <button class="thumb empty" onclick={() => genBg(loc)}>🖼 Generate scene image</button>
        {/if}
      </div>

      <!-- Text -->
      <div class="fields">
        <input class="fld" bind:value={loc.description} oninput={onChange} placeholder="description — objective, no people" />
        <div class="promptrow">
          <input class="fld" bind:value={loc.background_prompt} oninput={onChange}
                 placeholder="scene image prompt — pure environment, Danbooru tags" />
          <button class="mini" onclick={() => regenPrompt(loc)} disabled={promptBusy[loc.id]}
                  title="Write a fresh image prompt from the description (AI)">{promptBusy[loc.id] ? '…' : '✨'}</button>
          <button class="mini" onclick={() => tagify(loc)} disabled={tagBusy[loc.id]}
                  title="Convert prose → Danbooru tags">{tagBusy[loc.id] ? '…' : '⇥'}</button>
        </div>
        {#if err[loc.id]}<span class="err">{err[loc.id]}</span>{/if}
      </div>
    </div>
  </div>
{/snippet}

<style>
  .locs { display: flex; flex-direction: column; gap: 12px; }
  .hint { margin: 0 0 2px; }
  .none { padding: 26px 4px; color: var(--faint); font-size: 13px; }

  /* Root = coloured container holding its children (matches LocationRoster). */
  .root { --rc: var(--accent); border: 1px solid color-mix(in srgb, var(--rc) 40%, var(--border-soft));
          border-left: 5px solid var(--rc); border-radius: 12px; background: var(--panel);
          overflow: hidden; }
  .kid { margin: 0 10px 10px 18px; border-left: 3px solid color-mix(in srgb, var(--rc) 55%, transparent);
         border-radius: 9px; background: color-mix(in srgb, var(--rc) 5%, transparent); }

  .loc { padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
  .loc.root-loc { background: color-mix(in srgb, var(--rc) 9%, var(--panel)); }
  .ltop { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
  .marker { flex: none; }
  .lname { flex: 1; min-width: 120px; font-size: 13.5px; font-weight: 700; color: var(--text);
           background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 4px 7px; }
  .lname:hover { border-color: var(--border-soft); }
  .lname:focus { outline: none; border-color: var(--rc); background: var(--elev); }
  .startbadge { flex: none; font-size: 10.5px; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent);
                padding: 2px 9px; border-radius: 999px; white-space: nowrap; }

  .lbody { display: flex; gap: 12px; align-items: flex-start; }
  .fields { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .fld { width: 100%; box-sizing: border-box; font-size: 12px; color: var(--text); background: var(--elev);
         border: 1px solid var(--border-soft); border-radius: 7px; padding: 6px 8px; }
  .fld:focus { outline: none; border-color: var(--rc); }
  .promptrow { display: flex; align-items: center; gap: 6px; }

  /* Scene image — thumb / candidate grid */
  .img { flex: none; width: 240px; }
  .thumb { width: 240px; height: 135px; border-radius: 9px; overflow: hidden; position: relative;
           border: 1px solid var(--border-soft); background: var(--elev-2, var(--bg)); cursor: pointer;
           padding: 0; color: var(--muted); font-size: 12.5px; }
  .thumb.empty { border-style: dashed; display: grid; place-items: center; }
  .thumb:hover { border-color: var(--rc); }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .redo { position: absolute; inset: auto 0 0 0; padding: 3px 0; font-size: 11px; color: #fff;
          background: rgba(0,0,0,.55); opacity: 0; transition: opacity .15s; }
  .thumb:hover .redo { opacity: 1; }

  .cand-wrap { display: flex; flex-direction: column; gap: 5px; }
  .cand-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; width: 240px; }
  .cand { position: relative; height: 66px; border-radius: 7px; overflow: hidden; padding: 0;
          border: 1px solid var(--border-soft); background: var(--elev-2, var(--bg)); cursor: pointer; }
  .cand:hover { border-color: var(--rc); }
  .cand img { width: 100%; height: 100%; object-fit: cover; }
  .cand .use { position: absolute; inset: 0; display: grid; place-items: center; font-size: 11px; font-weight: 700;
               color: #fff; background: rgba(0,0,0,.45); opacity: 0; }
  .cand:hover .use { opacity: 1; }
  .cand.ph { display: grid; place-items: center; }

  .mini { flex: none; padding: 4px 8px; border-radius: 7px; font-size: 11.5px; background: var(--elev);
          border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .mini:hover:not(:disabled) { border-color: var(--rc); color: var(--text); }
  .err { font-size: 11px; color: var(--bad, #ff7a7a); }
  .spin { width: 18px; height: 18px; border-radius: 50%; border: 2px solid rgba(255,255,255,.25);
          border-top-color: var(--rc); animation: sp .7s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } }
</style>

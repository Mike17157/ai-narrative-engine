<script>
  // Scenes — character-anchored spots that live ON a location (the orbits: who is usually
  // where). Text fields + the per-scene background image (production) edit here; adding/removing
  // scenes, the character-anchor, and the home-slot flag are structural → the section editor.
  // Only locations that HAVE scenes are shown. onChange(locations).
  import { post } from '$lib/api.js';
  let { storyKey = '', locations = [], cast = [], onChange } = $props();

  let bgState = $state({});   // { [sid]: { busy, cand, err } }
  const bg = (sid) => bgState[sid] || { busy: false, cand: null, err: null };
  const nameOf = (k) => cast.find((c) => c.key === k)?.name || k;

  // Only locations with scenes are editable here.
  let withScenes = $derived(locations.filter((l) => (l.scenes || []).length));

  function emit(next) { onChange?.(next); }
  function updateLoc(i, patch) {
    // `i` is the index into `withScenes`; map back to the full locations array.
    const target = withScenes[i];
    emit(locations.map((l) => (l.id === target.id ? { ...l, ...patch } : l)));
  }
  function updateScene(i, si, patch) {
    const l = withScenes[i];
    updateLoc(i, { scenes: l.scenes.map((s, j) => (j === si ? { ...s, ...patch } : s)) });
  }

  async function genBg(s) {
    if (!storyKey || !s.background_prompt?.trim()) {
      bgState = { ...bgState, [s.id]: { busy: false, cand: null, err: 'Add a background plate prompt first.' } };
      return;
    }
    bgState = { ...bgState, [s.id]: { busy: true, cand: null, err: null } };
    const r = await post(`/stories/${storyKey}/scene/${s.id}/background/candidate`, {});
    if (r.ok && r.data?.image) bgState = { ...bgState, [s.id]: { busy: false, cand: r.data.image, err: null } };
    else bgState = { ...bgState, [s.id]: { busy: false, cand: null, err: r.data?.error || 'render failed' } };
  }
  async function useBg(i, si, s) {
    const img = bg(s.id).cand;
    if (!img) return;
    const r = await post(`/stories/${storyKey}/scene/${s.id}/background/select`, { data: img });
    if (r.ok && r.data?.url) { updateScene(i, si, { background: r.data.url }); bgState = { ...bgState, [s.id]: { busy: false, cand: null, err: null } }; }
    else bgState = { ...bgState, [s.id]: { ...bg(s.id), err: r.data?.error || 'save failed' } };
  }
</script>

<div class="pe">
  {#each withScenes as l, i (l.id)}
    <div class="place">
      <div class="place-head">
        <span class="pname">{l.name}</span>
      </div>
      {#if l.description}<div class="pdesc">{l.description}</div>{/if}

      <div class="scenes">
        {#each l.scenes || [] as s, si (s.id)}
          <div class="scene" class:home={s.role === 'persona_home'}>
            <div class="scene-top">
              <input class="sname" value={s.name} oninput={(e) => updateScene(i, si, { name: e.target.value })} placeholder="Scene / spot (Mom's kitchen…)" />
              <span class="anchor-badge" class:amb={!s.character}>{s.character ? nameOf(s.character) : 'shared'}</span>
              {#if s.role === 'persona_home'}<span class="home-badge" title="the swappable player-home slot">🏠 home slot</span>{/if}
            </div>
            <textarea class="sback" rows="2" value={s.backstory} oninput={(e) => updateScene(i, si, { backstory: e.target.value })}
              placeholder="What {s.character ? nameOf(s.character) : 'whoever is here'} usually does in this spot…"></textarea>
            <div class="scene-foot">
              <input class="sbg" value={s.background_prompt} oninput={(e) => updateScene(i, si, { background_prompt: e.target.value })} placeholder="Background plate (booru tags, no people)" />
              <button class="bgbtn" title="Render this spot's background" disabled={bg(s.id).busy} onclick={() => genBg(s)}>{bg(s.id).busy ? '…' : '🎨'}</button>
            </div>
            {#if s.background || bg(s.id).cand || bg(s.id).err}
              <div class="bgrow">
                {#if s.background && !bg(s.id).cand}<img class="bgthumb" src={s.background} alt="background" />{/if}
                {#if bg(s.id).cand}
                  <img class="bgthumb cand" src={bg(s.id).cand} alt="candidate" />
                  <button class="bguse" onclick={() => useBg(i, si, s)}>Use this</button>
                  <button class="bgredo" onclick={() => genBg(s)}>↻</button>
                {/if}
                {#if bg(s.id).err}<span class="bgerr">{bg(s.id).err}</span>{/if}
              </div>
            {/if}
          </div>
        {:else}
          <p class="none">No scenes here — ask the editor to add some.</p>
        {/each}
      </div>
    </div>
  {:else}
    <p class="none">No scenes yet — ask the editor to add some to a location.</p>
  {/each}
</div>

<style>
  .pe { display: flex; flex-direction: column; gap: 12px; }
  .none { font-size: 12.5px; color: var(--faint); margin: 0; }
  .place { border: 1px solid var(--border-soft); border-radius: 12px; padding: 12px 14px; background: var(--panel); display: flex; flex-direction: column; gap: 8px; }
  .place-head { display: flex; align-items: center; gap: 8px; }
  .pname { flex: 1; font-size: 14px; font-weight: 650; color: var(--text); }
  .pdesc { font-size: 12.5px; color: var(--muted); }
  .sname:focus, .sbg:focus, .sback:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }

  .scenes { display: flex; flex-direction: column; gap: 8px; padding-left: 12px; border-left: 2px solid var(--border-soft); margin-left: 2px; }
  .scene { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 10px; background: var(--elev); display: flex; flex-direction: column; gap: 6px; }
  .scene.home { border-color: color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 7%, var(--elev)); }
  .scene-top { display: flex; gap: 7px; align-items: center; }
  .sname { flex: 1; font-size: 13px; font-weight: 600; padding: 6px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .anchor-badge { flex: none; font-size: 11px; color: var(--text); background: var(--bg); border: 1px solid var(--border-soft); padding: 3px 9px; border-radius: 999px; }
  .anchor-badge.amb { color: var(--faint); }
  .home-badge { flex: none; font-size: 10.5px; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent); padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
  .sback { resize: vertical; font: inherit; font-size: 12.5px; line-height: 1.45; padding: 7px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); }
  .scene-foot { display: flex; gap: 10px; align-items: center; }
  .sbg { flex: 1; font-size: 11.5px; padding: 5px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--muted); }
  .bgbtn { width: 28px; height: 28px; flex: none; padding: 0; display: grid; place-items: center; font-size: 12px; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .bgbtn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .bgrow { display: flex; align-items: center; gap: 8px; }
  .bgthumb { width: 72px; height: 44px; border-radius: 6px; object-fit: cover; border: 1px solid var(--border); }
  .bgthumb.cand { border-color: var(--accent); }
  .bguse { font-size: 11.5px; padding: 4px 10px; border-radius: 7px; background: var(--accent); color: #fff; border: none; }
  .bgredo { width: 26px; height: 26px; padding: 0; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .bgerr { font-size: 11.5px; color: var(--bad); }
</style>

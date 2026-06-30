<script>
  // Places & Scenes editor — story-authored containers (Places) each holding
  // character-anchored Scenes ("mom's kitchen", "the baker's bakery"). Edits bubble up
  // via onChange(places); the parent owns persistence (PUT /stories/{key} {places}).
  import { post } from '$lib/api.js';
  let { storyKey = '', places = [], cast = [], onChange } = $props();

  // Per-scene background generation (keyed by scene id): render a candidate, then "use" it.
  let bgState = $state({});   // { [sid]: { busy, cand, err } }
  const bg = (sid) => bgState[sid] || { busy: false, cand: null, err: null };

  // cast: [{ key, name }] — anchor options for a scene.
  const nameOf = (k) => cast.find((c) => c.key === k)?.name || k;

  let uid = 0;
  const newId = (p) => `${p}_${Date.now().toString(36)}_${uid++}`;

  function emit(next) { onChange?.(next); }

  function addPlace() {
    emit([...places, { id: newId('place'), name: 'New place', description: '', background_prompt: '', scenes: [] }]);
  }
  function updatePlace(i, patch) {
    emit(places.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  }
  function removePlace(i) {
    emit(places.filter((_, j) => j !== i));
  }
  function addScene(i) {
    const p = places[i];
    const scene = { id: newId('scene'), name: '', character: null, characters: [], backstory: '', background_prompt: '', role: '' };
    updatePlace(i, { scenes: [...(p.scenes || []), scene] });
  }
  function updateScene(i, si, patch) {
    const p = places[i];
    updatePlace(i, { scenes: p.scenes.map((s, j) => (j === si ? { ...s, ...patch } : s)) });
  }
  function removeScene(i, si) {
    const p = places[i];
    updatePlace(i, { scenes: p.scenes.filter((_, j) => j !== si) });
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
    if (r.ok && r.data?.url) {
      updateScene(i, si, { background: r.data.url });
      bgState = { ...bgState, [s.id]: { busy: false, cand: null, err: null } };
    } else {
      bgState = { ...bgState, [s.id]: { ...bg(s.id), err: r.data?.error || 'save failed' } };
    }
  }
</script>

<div class="pe">
  {#each places as p, i (p.id)}
    <div class="place">
      <div class="place-head">
        <input class="pname" value={p.name} oninput={(e) => updatePlace(i, { name: e.target.value })} placeholder="Place name (The House, Main Street…)" />
        <button class="rm" title="Delete place" onclick={() => removePlace(i)}>🗑</button>
      </div>
      <input class="pdesc" value={p.description} oninput={(e) => updatePlace(i, { description: e.target.value })} placeholder="What this place is, objectively" />

      <div class="scenes">
        {#each p.scenes || [] as s, si (s.id)}
          <div class="scene" class:home={s.role === 'persona_home'}>
            <div class="scene-top">
              <input class="sname" value={s.name} oninput={(e) => updateScene(i, si, { name: e.target.value })} placeholder="Scene / spot (Mom's kitchen…)" />
              <select class="anchor" value={s.character ?? ''} onchange={(e) => updateScene(i, si, { character: e.target.value || null })}>
                <option value="">— shared / ambient —</option>
                {#each cast as c (c.key)}
                  <option value={c.key}>{c.name}</option>
                {/each}
              </select>
              <button class="rm sm" title="Delete scene" onclick={() => removeScene(i, si)}>✕</button>
            </div>
            <textarea class="sback" rows="2" value={s.backstory} oninput={(e) => updateScene(i, si, { backstory: e.target.value })}
              placeholder="What {s.character ? nameOf(s.character) : 'whoever is here'} usually does in this spot…"></textarea>
            <div class="scene-foot">
              <input class="sbg" value={s.background_prompt} oninput={(e) => updateScene(i, si, { background_prompt: e.target.value })} placeholder="Background plate (booru tags, no people)" />
              <button class="bgbtn" title="Render this spot's background" disabled={bg(s.id).busy} onclick={() => genBg(s)}>{bg(s.id).busy ? '…' : '🎨'}</button>
              <label class="home-toggle" title="Mark as the swappable player-home slot — an embodied persona's home stands in for it">
                <input type="checkbox" checked={s.role === 'persona_home'} onchange={(e) => updateScene(i, si, { role: e.target.checked ? 'persona_home' : '' })} />
                🏠 home slot
              </label>
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
        {/each}
        <button class="add-scene" onclick={() => addScene(i)}>+ Scene in {p.name || 'this place'}</button>
      </div>
    </div>
  {/each}
  <button class="add-place" onclick={addPlace}>+ Add a place</button>
</div>

<style>
  .pe { display: flex; flex-direction: column; gap: 12px; }
  .place { border: 1px solid var(--border-soft); border-radius: 12px; padding: 12px 14px; background: var(--panel); display: flex; flex-direction: column; gap: 8px; }
  .place-head { display: flex; align-items: center; gap: 8px; }
  .pname { flex: 1; font-size: 14px; font-weight: 650; padding: 7px 10px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .pdesc { font-size: 12.5px; padding: 6px 10px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--muted); }
  .pname:focus, .pdesc:focus, .sname:focus, .sbg:focus, .sback:focus, .anchor:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }

  .scenes { display: flex; flex-direction: column; gap: 8px; padding-left: 12px; border-left: 2px solid var(--border-soft); margin-left: 2px; }
  .scene { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 10px; background: var(--elev); display: flex; flex-direction: column; gap: 6px; }
  .scene.home { border-color: color-mix(in srgb, var(--accent) 45%, transparent); background: color-mix(in srgb, var(--accent) 7%, var(--elev)); }
  .scene-top { display: flex; gap: 7px; align-items: center; }
  .sname { flex: 1; font-size: 13px; font-weight: 600; padding: 6px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .anchor { font-size: 12px; padding: 6px 8px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); max-width: 170px; }
  .sback { resize: vertical; font: inherit; font-size: 12.5px; line-height: 1.45; padding: 7px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--text); }
  .scene-foot { display: flex; gap: 10px; align-items: center; }
  .sbg { flex: 1; font-size: 11.5px; padding: 5px 9px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border-soft); color: var(--muted); }
  .home-toggle { display: flex; align-items: center; gap: 5px; font-size: 11.5px; color: var(--muted); white-space: nowrap; cursor: pointer; }
  .home-toggle input { accent-color: var(--accent); }
  .bgbtn { width: 28px; height: 28px; flex: none; padding: 0; display: grid; place-items: center; font-size: 12px; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .bgbtn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .bgrow { display: flex; align-items: center; gap: 8px; }
  .bgthumb { width: 72px; height: 44px; border-radius: 6px; object-fit: cover; border: 1px solid var(--border); }
  .bgthumb.cand { border-color: var(--accent); }
  .bguse { font-size: 11.5px; padding: 4px 10px; border-radius: 7px; background: var(--accent); color: #fff; border: none; }
  .bgredo { width: 26px; height: 26px; padding: 0; border-radius: 7px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .bgerr { font-size: 11.5px; color: var(--bad); }

  .rm { width: 30px; height: 30px; flex: none; padding: 0; display: grid; place-items: center; font-size: 12px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  .rm.sm { width: 26px; height: 26px; }
  .rm:hover { color: var(--bad); border-color: rgba(255,122,122,.5); }

  .add-scene { align-self: flex-start; font-size: 12px; padding: 5px 11px; border-radius: 7px; background: none; border: 1px dashed var(--border); color: var(--muted); }
  .add-scene:hover { border-color: var(--accent); color: var(--accent); }
  .add-place { align-self: flex-start; font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border); color: var(--text); }
  .add-place:hover { border-color: var(--accent); color: var(--accent); }
</style>

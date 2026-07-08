<script>
  // The PROMPTS surface — every image prompt in the story in one place, editable, for TUNING: the
  // art-style layer, each character's appearance + per-outfit prompt, and every location / scene
  // background plate. Direct type-editing (image prompts are production, not the narrative bible);
  // debounced saves route to the right endpoint per prompt.
  import { stories } from '$lib/stories.svelte.js';
  import { charName } from '$lib/characters.svelte.js';
  import { get, put, patch } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let st = $derived(stories.current);
  let cast = $derived(st?.cast || []);
  let locs = $derived(st?.locations || []);

  // Per-character portrait sheets (appearance + outfits with their prompts).
  let sheets = $state([]);
  let loading = $state(true);
  let seeded = '';
  async function load() {
    loading = true;
    const results = await Promise.all(cast.map(async (m) => {
      const r = await get(`/characters/${m.character}/portraits`);
      return { key: m.character, name: charName(m.character) || m.character,
        appearance: r?.appearance || '',
        outfits: (r?.outfits || []).map((o) => ({ id: o.id, name: o.name || o.id, prompt: (o.attire_prompt ?? o.prompt ?? '') })) };
    }));
    sheets = results; loading = false;
  }
  $effect(() => { const k = st?.key; if (k && seeded !== k) { seeded = k; load(); } });

  // ── debounced saves, one endpoint per prompt kind ──
  const timers = {};
  function debounce(k, fn) { clearTimeout(timers[k]); timers[k] = setTimeout(fn, 500); }
  function saveArt() { debounce('art', () => put(`/stories/${st.key}`, { art_style: st.art_style || '' })); }
  function saveAppearance(s) { debounce('app-' + s.key, () => put(`/characters/${s.key}/portraits/appearance`, { appearance: s.appearance })); }
  function saveOutfit(s, o) { debounce('o-' + o.id, () => patch(`/characters/${s.key}/portraits/outfit/${o.id}`, { attire_prompt: o.prompt })); }
  function saveLocs() { debounce('locs', () => put(`/stories/${st.key}`, { locations: stories.current.locations })); }
</script>

<div class="wrap">
  <header>
    <h1>Prompts <span class="lo">· every image prompt, in one place</span></h1>
    <p class="sub">Tune the image output here: the art-style layer every render opens with, each
      character's appearance + per-outfit prompt, and each location / scene background plate.</p>
  </header>

  <!-- Art style — layer 0 -->
  <section class="grp">
    <div class="gh"><span class="gt">🎨 Art style</span><span class="gs">layer 0 — every image leads with this</span></div>
    <textarea class="p style" use:autosize={st.art_style} bind:value={st.art_style} oninput={saveArt}
              placeholder="e.g. Polished 2D anime illustration, crisp lineart, rich cel shading… (leave empty for the global default)"></textarea>
  </section>

  <!-- Characters: appearance + per-outfit prompts -->
  <section class="grp">
    <div class="gh"><span class="gt">👤 Characters</span><span class="gs">appearance + each outfit</span></div>
    {#if loading}<p class="none">Loading prompts…</p>{/if}
    {#each sheets as s (s.key)}
      <div class="char">
        <div class="cname">{s.name}</div>
        <div class="row"><span class="rk">appearance</span>
          <textarea class="p" use:autosize={s.appearance} bind:value={s.appearance} oninput={() => saveAppearance(s)}
                    placeholder="the character's fixed features — hair, eyes, build…"></textarea></div>
        {#each s.outfits as o (o.id)}
          <div class="row"><span class="rk out">{o.name}</span>
            <textarea class="p" use:autosize={o.prompt} bind:value={o.prompt} oninput={() => saveOutfit(s, o)}
                      placeholder="the full outfit prompt (booru tags / prose per your model)"></textarea></div>
        {/each}
        {#if !s.outfits.length}<div class="row"><span class="rk">outfits</span><span class="emptyrow">none yet</span></div>{/if}
      </div>
    {:else}
      {#if !loading}<p class="none">No cast yet.</p>{/if}
    {/each}
  </section>

  <!-- Locations -->
  {#if locs.length}
    <section class="grp">
      <div class="gh"><span class="gt">🌐 Locations</span><span class="gs">scene background plates</span></div>
      {#each locs as l, i (l.id)}
        <div class="row"><span class="rk">{l.name || 'location'}</span>
          <textarea class="p" use:autosize={l.background_prompt} bind:value={l.background_prompt} oninput={saveLocs}
                    placeholder="scene image prompt — pure environment, no people"></textarea></div>
      {/each}
    </section>
  {/if}

  <!-- Scene backgrounds (per-spot plates on locations) -->
  {#if locs.some((l) => (l.scenes || []).length)}
    <section class="grp">
      <div class="gh"><span class="gt">🗺 Scenes</span><span class="gs">per-spot background plates</span></div>
      {#each locs as l (l.id)}
        {#each l.scenes || [] as sc (sc.id)}
          <div class="row"><span class="rk">{l.name} · {sc.name || 'spot'}</span>
            <textarea class="p" use:autosize={sc.background_prompt} bind:value={sc.background_prompt} oninput={saveLocs}
                      placeholder="background plate — booru tags, no people"></textarea></div>
        {/each}
      {/each}
    </section>
  {/if}
</div>

<style>
  .wrap { padding: 20px 26px 60px; max-width: 1000px; }
  h1 { margin: 0 0 4px; font-size: 22px; font-weight: 800; }
  h1 .lo { color: var(--faint); font-weight: 500; font-size: 15px; }
  .sub { margin: 0 0 6px; max-width: 720px; font-size: 12.5px; color: var(--muted); line-height: 1.55; }

  .grp { margin-top: 18px; }
  .gh { display: flex; align-items: baseline; gap: 9px; margin-bottom: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--border-soft); }
  .gt { font-size: 13px; font-weight: 800; color: var(--text); }
  .gs { font-size: 11px; color: var(--faint); }
  .none { font-size: 12.5px; color: var(--faint); margin: 4px 0; }

  .char { margin-bottom: 14px; }
  .cname { font-size: 13.5px; font-weight: 800; color: var(--text); margin: 8px 0 5px; }

  .row { display: grid; grid-template-columns: 130px 1fr; gap: 10px; align-items: start; margin-bottom: 6px; }
  .rk { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; font-weight: 700; padding-top: 8px;
        overflow: hidden; text-overflow: ellipsis; }
  .rk.out { color: var(--accent); text-transform: none; letter-spacing: 0; font-weight: 600; font-size: 12px; }
  .emptyrow { font-size: 12px; color: var(--faint); padding-top: 7px; }

  .p { width: 100%; box-sizing: border-box; resize: none; font: inherit; font-size: 12.5px; line-height: 1.5;
       color: var(--text); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 8px; padding: 7px 10px; }
  .p:focus { outline: none; border-color: var(--accent); }
  .p.style { font-size: 13px; background: color-mix(in srgb, var(--accent) 6%, var(--elev)); }
</style>

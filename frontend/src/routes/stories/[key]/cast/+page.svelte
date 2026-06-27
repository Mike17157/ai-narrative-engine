<script>
  import { page } from '$app/state';
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import CastDashboard from '$lib/components/story/CastDashboard.svelte';
  import CharacterCatalogue from '$lib/components/story/CharacterCatalogue.svelte';

  let st = $derived(stories.current);
  let view = $state('dashboard');   // 'dashboard' | 'catalogue'
  // ?c=<key> (from the side-menu cast tier) selects that member in the carousel; no separate page.
  // ?job=<id> passed by the story wizard after firing plan-wardrobe-all immediately on save.
  let selKey = $derived(page.url.searchParams.get('c') || '');
  let jobParam = $derived(page.url.searchParams.get('job') || '');
  let sourceK = $derived(st.fields?.source_character);
  // ONE surface: the dashboard switches character, investigates properties inline, and runs all
  // (re)generation through the gated RegenModal. (The per-character ?c= Portrait Studio is retired.)
  let cast = $derived(st.cast.map((m) => {
    const ci = chars.list.find((c) => c.key === m.character);
    const own = (ci?.images || []).filter((im) => im.kind === 'card link').map((im) => im.url);
    const srcImgs = m.primary
      ? (chars.list.find((c) => c.key === sourceK)?.images || [])
          .filter((im) => im.kind === 'card link' || im.kind === 'avatar').map((im) => im.url)
      : [];
    return { character: m.character, name: charName(m.character), hasRef: !!ci?.reference,
             primary: m.primary, role: (ci?.fields?.role) || '', desc: ci?.system || '',
             height: ci?.fields?.height_cm ?? null,
             images: [...new Set([...own, ...srcImgs])] };
  }));

  async function reload() { await Promise.all([loadStory(st.key), loadChars()]); }
</script>

<div class="page castpage"><div class="col full fillh">
  <div class="view-toggle">
    <button class="vt-btn" class:active={view === 'dashboard'} onclick={() => view = 'dashboard'}>Dashboard</button>
    <button class="vt-btn" class:active={view === 'catalogue'} onclick={() => view = 'catalogue'}>Catalogue</button>
  </div>
  {#if view === 'dashboard'}
    <CastDashboard storyKey={st.key} {cast} selectKey={selKey} onChanged={reload} initialJob={jobParam} />
  {:else}
    <CharacterCatalogue storyKey={st.key} {cast} locations={st.locations || []} onChanged={reload} />
  {/if}
</div></div>

<style>
  /* Override global .page padding so the snap container can fill flush */
  .castpage { padding: 0; overflow: hidden; }
  .fillh { height: 100%; position: relative; }

  /* Floating Dashboard / Catalogue switch */
  .view-toggle { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); z-index: 40;
                 display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
                 background: var(--panel); box-shadow: 0 4px 14px rgba(0,0,0,.25); }
  .vt-btn { padding: 5px 14px; font-size: 12.5px; font-weight: 600; background: none; border: none;
            box-shadow: none; color: var(--faint); cursor: pointer; }
  .vt-btn:hover { color: var(--text); background: var(--elev); filter: none; }
  .vt-btn.active { color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--panel)); filter: none; }
</style>

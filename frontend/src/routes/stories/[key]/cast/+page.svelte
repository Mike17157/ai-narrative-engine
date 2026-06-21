<script>
  import { page } from '$app/state';
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import CastDashboard from '$lib/components/story/CastDashboard.svelte';

  let st = $derived(stories.current);
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
  <CastDashboard storyKey={st.key} {cast} selectKey={selKey} onChanged={reload} initialJob={jobParam} />
</div></div>

<style>
  /* Override global .page padding so the snap container can fill flush */
  .castpage { padding: 0; overflow: hidden; }
  .fillh { height: 100%; }
</style>

<script>
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import CharacterCatalogue from '$lib/components/story/CharacterCatalogue.svelte';

  let st = $derived(stories.current);
  let onStage = $state('');   // the character centered in the catalogue
  let sourceK = $derived(st.fields?.source_character);
  // ONE surface: the catalogue owns everything — outfit-by-outfit selection at the top, and the
  // selected outfit's own emotion set (sprites + per-cell regen) in the strip under the stage.
  let cast = $derived(st.cast.map((m) => {
    const ci = chars.list.find((c) => c.key === m.character);
    const own = (ci?.images || []).filter((im) => im.kind === 'card link').map((im) => im.url);
    const srcImgs = m.primary
      ? (chars.list.find((c) => c.key === sourceK)?.images || [])
          .filter((im) => im.kind === 'card link' || im.kind === 'avatar').map((im) => im.url)
      : [];
    return { character: m.character, name: charName(m.character), hasRef: !!ci?.reference,
             primary: m.primary, role: (ci?.fields?.role) || '', desc: ci?.system || '',
             outfit: m.outfit || '',          // the CastMember's active wardrobe selection
             height: ci?.fields?.height_cm ?? null,
             images: [...new Set([...own, ...srcImgs])] };
  }));

  async function reload() { await Promise.all([loadStory(st.key), loadChars(st.key)]); }
</script>

<div class="page castpage"><div class="col full fillh">
  <div class="stage-wrap">
    <CharacterCatalogue storyKey={st.key} {cast}
                        onChanged={reload} onCharacter={(k) => onStage = k} />
  </div>
</div></div>

<style>
  /* Override global .page padding so the catalogue can fill flush */
  .castpage { padding: 0; overflow: hidden; }
  .fillh { height: 100%; display: flex; flex-direction: column; }
  .stage-wrap { flex: 1; min-height: 0; position: relative; }
</style>

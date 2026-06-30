<script>
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import CharacterCatalogue from '$lib/components/story/CharacterCatalogue.svelte';
  import AgentChat from '$lib/components/story/AgentChat.svelte';

  let st = $derived(stories.current);
  let onStage = $state('');   // the character centered in the catalogue → the outfit agent works on them
  let sourceK = $derived(st.fields?.source_character);
  // ONE surface: the catalogue swaps character + outfit; the bottom agent chat builds/edits outfits.
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

  // When a character is centered, the bottom chat edits THAT character's fields (persona/appearance/
  // wants…) via the universal loop, target='character:<key>'. Outfit/action tools still run as before;
  // with no one on stage it falls back to editing the story graph. See AgentChat.
  let charArtifact = $derived.by(() => {
    if (!onStage) return null;
    const ci = chars.list.find((c) => c.key === onStage);
    if (!ci) return null;
    const f = ci.fields || {};
    return { cast: [{ id: onStage, name: ci.name, persona: ci.system || '', role: f.role || '',
      appearance: f.appearance || '', base_prompt: f.base_prompt || '', temperament: f.temperament || '',
      want: f.want || '', lie: f.lie || '', wound: f.wound || '', secret: f.secret || '' }] };
  });

  let agent;        // AgentChat instance (.ask seeds it)
  let catalogue;    // CharacterCatalogue instance (.refresh pulls new outfits)
  async function reload() { await Promise.all([loadStory(st.key), loadChars()]); }
  async function afterAgent() { await reload(); await catalogue?.refresh(); }
</script>

<div class="page castpage"><div class="col full fillh">
  <div class="stage-wrap">
    <CharacterCatalogue bind:this={catalogue} storyKey={st.key} {cast} locations={st.locations || []}
                        onChanged={reload} onCharacter={(k) => onStage = k} onAsk={(t) => agent?.ask(t)} />
  </div>
  <AgentChat bind:this={agent} storyKey={st.key} primaryChar={onStage} dock="bottom"
             target={onStage ? `character:${onStage}` : 'story'} artifact={charArtifact} onApplied={afterAgent} />
</div></div>

<style>
  /* Override global .page padding so the catalogue can fill flush */
  .castpage { padding: 0; overflow: hidden; }
  .fillh { height: 100%; display: flex; flex-direction: column; }
  .stage-wrap { flex: 1; min-height: 0; position: relative; }
</style>

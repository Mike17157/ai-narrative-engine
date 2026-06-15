<script>
  import { page } from '$app/state';
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories, loadStory } from '$lib/stories.svelte.js';
  import Wardrobe from '$lib/components/Wardrobe.svelte';
  import CastDashboard from '$lib/components/CastDashboard.svelte';

  let st = $derived(stories.current);
  let primaryK = $derived(st.cast.find((m) => m.primary)?.character || st.cast[0]?.character);
  // Style sources = the protagonist's looks. The generated primary card usually has only its
  // reference, so also pull the original source card's images (avatar + card art) for more options.
  let sourceK = $derived(st.fields?.source_character);
  let primaryImgs = $derived([...new Set([
    ...(chars.list.find((c) => c.key === primaryK)?.images || []),
    ...(chars.list.find((c) => c.key === sourceK)?.images || []),
  ].map((im) => im.url))]);
  let cast = $derived(st.cast.map((m) => {
    const ci = chars.list.find((c) => c.key === m.character);
    // Selectable card images for "use as base". The primary is a generated card (only a
    // reference), so also offer the original source card's art so it has real options too.
    const own = (ci?.images || []).filter((im) => im.kind === 'card link').map((im) => im.url);
    const srcImgs = m.primary
      ? (chars.list.find((c) => c.key === sourceK)?.images || [])
          .filter((im) => im.kind === 'card link' || im.kind === 'avatar').map((im) => im.url)
      : [];
    return { character: m.character, name: charName(m.character), hasRef: !!ci?.reference,
             primary: m.primary, role: (ci?.fields?.role) || '', desc: ci?.system || '',
             images: [...new Set([...own, ...srcImgs])] };
  }));

  // ?c=<key> opens ONE member's wardrobe; no ?c= shows the whole-cast dashboard.
  let selKey = $derived(page.url.searchParams.get('c') || '');
  let shown = $derived(cast.filter((c) => c.character === selKey));
  let shownName = $derived(charName(selKey));

  async function reload() { await Promise.all([loadStory(st.key), loadChars()]); }
</script>

<div class="page"><div class="col wide">
  {#if selKey && shown.length}
    <div class="chead">
      <a class="back" href={`/stories/${st.key}/cast`}>← All cast</a>
      <h4>{shownName} <span class="lo">— base image, then outfits + expressions, saved to this character</span></h4>
    </div>
    <Wardrobe storyKey={st.key} primaryKey={primaryK} primaryImages={primaryImgs} cast={shown} />
  {:else}
    <CastDashboard storyKey={st.key} {cast} onChanged={reload} />
  {/if}
</div></div>

<style>
  .chead { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
  .back { text-decoration: none; font-size: 12.5px; font-weight: 600; color: var(--muted);
          background: var(--elev); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px; }
  .back:hover { color: var(--text); background: var(--elev-2); }
  h4 { margin: 0; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
</style>

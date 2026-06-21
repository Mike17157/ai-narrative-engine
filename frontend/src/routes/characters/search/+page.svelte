<script>
  import { onMount } from 'svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import { goto } from '$app/navigation';
  import { chars, blurb, selectChar, deleteChar } from '$lib/characters.svelte.js';

  function openChar(c) {
    if (c.story) goto(`/stories/${c.story}/cast?c=${c.key}`);
    else selectChar(c.key);
  }

  async function delCard(c) {
    if (await askConfirm({ title: `Delete ${c.name || c.key}?`,
        message: 'This removes the character and strips them from any story cast.',
        confirmLabel: 'Delete', danger: true })) deleteChar(c.key);
  }

  let query = $state('');
  let storyFilter = $state('');   // '' = all · '__lib' · '__detached' · <story key>
  let storyNames = $state({}); // story key -> name
  onMount(async () => {
    try { for (const s of await get('/stories')) storyNames[s.key] = s.name; storyNames = { ...storyNames }; }
    catch { /* offline */ }
  });

  const lsNum = (k, d) => { try { return +localStorage.getItem(k) || d; } catch { return d; } };
  let cardSize = $state(lsNum('loom.cardSize', 170));   // card WIDTH; the image scales with it
  let textH = $state(lsNum('loom.cardTextH', 56));      // room reserved for the text block
  $effect(() => { try { localStorage.setItem('loom.cardSize', cardSize); localStorage.setItem('loom.cardTextH', textH); } catch { /* ignore */ } });

  const match = (c) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    const tags = (c.fields?.tags || []).join(' ');
    return `${c.name} ${c.key} ${tags} ${blurb(c)}`.toLowerCase().includes(q);
  };
  // Library = ONLY imported / standalone cards (not generated, not story-bound).
  let library = $derived(chars.list.filter((c) => !c.story && !c.generated && match(c)));
  // Detached = generated characters that lost their story (junk to clean up).
  let detached = $derived(chars.list.filter((c) => !c.story && c.generated && match(c)));
  // Story sections = characters bound to a story.
  let storyGroups = $derived.by(() => {
    const g = {};
    for (const c of chars.list) if (c.story && match(c)) (g[c.story] ||= []).push(c);
    return Object.entries(g)
      .map(([key, list]) => ({ key, name: storyNames[key] || key, list }))
      .sort((a, b) => a.name.localeCompare(b.name));
  });

  // Filter-by-story dropdown options.
  let filterItems = $derived([
    { value: '', label: 'All characters' },
    { value: '__lib', label: `Library / imported (${library.length})` },
    ...(detached.length ? [{ value: '__detached', label: `Detached (${detached.length})` }] : []),
    ...storyGroups.map((g) => ({ value: g.key, label: `${g.name} (${g.list.length})` }))
  ]);
  const showLib = $derived(storyFilter === '' || storyFilter === '__lib');
  const showDetached = $derived(storyFilter === '' || storyFilter === '__detached');
  const shownStories = $derived(storyFilter === '' ? storyGroups
    : storyGroups.filter((g) => g.key === storyFilter));
</script>

{#snippet card(c)}
  <div class="pcard" class:sel={c.key === app.activeChar} role="button" tabindex="0" onclick={() => openChar(c)}>
    {#if c.reference || c.avatar}
      <img class="pav" src={c.reference || c.avatar} alt={c.name} />
    {:else}
      <span class="pav ph">{(c.name?.[0] || '?').toUpperCase()}</span>
    {/if}
    <span class="pnm">{c.name || c.key}</span>
    <span class="pbl">{blurb(c)}</span>
    <button class="del" title="Delete character"
      onclick={(e) => { e.stopPropagation(); delCard(c); }}>🗑</button>
  </div>
{/snippet}

<div class="searchbar">
  <input class="search" placeholder="Search characters by name, tag, or description…" bind:value={query} />
  <div class="storyfilter"><Combobox items={filterItems} bind:value={storyFilter} placeholder="Filter by story…" /></div>
</div>

<div class="knobs">
  <label>Card width <input type="range" min="120" max="340" step="5" bind:value={cardSize} /></label>
  <label>Text room <input type="range" min="0" max="220" step="4" bind:value={textH} /></label>
</div>

<div class="cols" style="--cardw:{cardSize}px; --texth:{textH}px">
  {#if showLib}
    <section>
      <h3 class="sec">Library <span class="cnt">{library.length}</span>
        <span class="lo">— imported cards; reusable in chat & new stories</span></h3>
      {#if library.length}
        <div class="grid">{#each library as c (c.key)}{@render card(c)}{/each}</div>
      {:else}<p class="empty">No imported characters{query ? ' match' : ' yet'}.</p>{/if}
    </section>
  {/if}

  {#if showDetached && detached.length}
    <section>
      <h3 class="sec detached">Detached <span class="cnt">{detached.length}</span>
        <span class="lo">— generated characters whose story is gone; safe to delete</span></h3>
      <div class="grid">{#each detached as c (c.key)}{@render card(c)}{/each}</div>
    </section>
  {/if}

  {#each shownStories as g (g.key)}
    <section>
      <h3 class="sec story">{g.name} <span class="cnt">{g.list.length}</span>
        <span class="lo">— characters bound to this story</span></h3>
      <div class="grid">{#each g.list as c (c.key)}{@render card(c)}{/each}</div>
    </section>
  {/each}
</div>

<style>
  .searchbar { display: flex; align-items: center; gap: 12px; }
  .search { flex: 1; padding: 9px 12px; }
  .storyfilter { width: 240px; flex: none; }
  .knobs {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); z-index: 40;
    display: flex; gap: 22px; align-items: center; padding: 10px 20px;
    background: rgba(20, 24, 34, .82); border: 1px solid var(--border); border-radius: 999px;
    box-shadow: 0 14px 40px rgba(0, 0, 0, .5); backdrop-filter: blur(8px);
  }
  .knobs label { display: flex; align-items: center; gap: 9px; font-size: 12px; color: var(--muted); white-space: nowrap; }
  .knobs input[type="range"] { width: 120px; accent-color: var(--accent); }

  .cols { margin-top: 16px; padding-bottom: 72px; display: flex; flex-direction: column; gap: 22px; }
  .sec { font-size: 14px; font-weight: 700; margin: 0 0 10px; display: flex; align-items: baseline; gap: 8px; padding-bottom: 6px; border-bottom: 1px solid var(--border-soft); }
  .sec.story { color: var(--accent); }
  .sec.detached { color: var(--bad); }
  .cnt { font-size: 11.5px; color: var(--muted); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 999px; padding: 1px 8px; font-weight: 600; }
  .lo { font-size: 11.5px; color: var(--faint); font-weight: 400; }
  .empty { color: var(--muted); font-size: 13px; margin: 0; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(var(--cardw, 150px), 1fr)); gap: 12px; }
  .pcard {
    position: relative;
    display: flex; flex-direction: column; text-align: left; padding: 0; overflow: hidden;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px;
    box-shadow: none; cursor: pointer; transition: border-color .12s, background .12s;
  }
  .pcard .del {
    position: absolute; top: 6px; right: 6px; z-index: 3; width: 26px; height: 26px; padding: 0;
    display: grid; place-items: center; font-size: 12px; border-radius: 7px; opacity: .5;
    background: rgba(10,12,18,.7); border: 1px solid var(--border); color: var(--muted); box-shadow: none;
  }
  .pcard:hover .del { opacity: 1; }
  .pcard .del:hover { color: var(--bad); border-color: rgba(255,122,122,.5); background: rgba(255,122,122,.16); filter: none; }
  .pcard:hover { background: var(--elev-2); border-color: #323847; filter: none; }
  .pcard.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  /* Image scales with the card WIDTH (fixed portrait ratio), so the width slider grows it. */
  .pav { width: 100%; aspect-ratio: 3 / 4; object-fit: cover; display: block; }
  .pav.ph {
    display: grid; place-items: center; aspect-ratio: 3 / 4; font-size: 46px; font-weight: 700; color: #fff;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
  }
  .pcard .pnm { font-weight: 600; font-size: 13.5px; padding: 9px 10px 0; }
  /* The text slider gives the blurb more room (height-clamped, so more lines show). */
  .pcard .pbl {
    font-size: 11.5px; color: var(--muted); padding: 3px 10px 10px; line-height: 1.4;
    height: var(--texth, 56px); overflow: hidden;
  }
</style>

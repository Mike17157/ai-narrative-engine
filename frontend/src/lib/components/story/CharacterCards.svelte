<script>
  import { page } from '$app/stores';
  import { stories } from '$lib/stories.svelte.js';
  import { chars, charName } from '$lib/characters.svelte.js';

  let st = $derived(stories.current);
  let selected = $derived($page.url.searchParams.get('char') || '');
  let cards = $derived((st?.cast || []).map((member) => {
    const character = chars.list.find((c) => c.key === member.character) || {};
    return { ...character, key: member.character, name: character.name || charName(member.character), image: character.reference || character.avatar || null };
  }));
  const label = (key) => key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const entries = (obj) => Object.entries(obj || {}).filter(([key, value]) => value !== '' && value != null && !['image', 'images', 'reference', 'avatar', 'imported', 'primary'].includes(key));
</script>

<div class="wrap">
  <header><h1>Character cards</h1><p>Each view renders the stored character card directly. New persisted fields appear automatically.</p></header>
  <div class="cards">
    {#each cards as card (card.key)}
      <article class:selected={selected === card.key}>
        <div class="head">{#if card.image}<img src={card.image} alt="" />{:else}<span class="avatar">{card.name?.[0] || '•'}</span>{/if}<div><h2>{card.name}</h2><span>{card.key}</span></div></div>
        {#each entries({ system: card.system, greeting: card.greeting }) as [key, value] (key)}<section><b>{label(key)}</b><p>{typeof value === 'string' ? value : JSON.stringify(value)}</p></section>{/each}
        {#each entries(card.fields) as [key, value] (key)}<section><b>{label(key)}</b><p>{typeof value === 'string' ? value : JSON.stringify(value)}</p></section>{/each}
        <div class="discover">＋ Generate or add a detail when the story needs it</div>
      </article>
    {:else}<p class="empty">No character cards in this story yet.</p>{/each}
  </div>
</div>

<style>
  .wrap { max-width: 980px; padding: 24px; } h1,h2,p { margin: 0; } header p { margin-top: 6px; color: var(--muted); max-width: 720px; line-height: 1.5; } .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; margin-top:20px; } article { padding:16px; border:1px solid var(--border); border-radius:14px; background:var(--elev); } article.selected { border-color:var(--accent); } .head { display:flex; gap:10px; align-items:center; margin-bottom:12px; } .head img,.avatar { width:42px; height:42px; border-radius:10px; object-fit:cover; display:grid; place-items:center; background:var(--elev-2); } h2 { font-size:16px; } .head span { color:var(--muted); font-size:12px; } section { border-top:1px solid var(--border-soft); padding-top:10px; margin-top:10px; } b,em { font-size:10px; text-transform:uppercase; letter-spacing:.4px; color:var(--accent); } section p { white-space:pre-wrap; margin-top:5px; font-size:13px; line-height:1.5; } .empty { color:var(--faint); font-style:italic; } .discover { margin-top:12px; border:1px dashed var(--border); border-radius:8px; padding:8px; color:var(--muted); font-size:12px; }
</style>

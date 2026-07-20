<script>
  // Personas = PLAYABLE character cards (the portable "you" puppets). This page is the
  // gallery + active-puppet selector; "New persona" launches the guided wizard. (The legacy
  // thin-Persona system still backs free-chat as a fallback; it's no longer surfaced here —
  // personas are cards now.)
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { app, setActivePlayerChar } from '$lib/app.svelte.js';
  import { chars, loadChars, blurb } from '$lib/characters.svelte.js';

  let playable = $derived((chars.list || []).filter((c) => c.playable)
    .sort((a, b) => (a.name || a.key).localeCompare(b.name || b.key)));

  function choose(key) { setActivePlayerChar(key === app.activePlayerChar ? '' : key); }

  onMount(loadChars);
</script>

<div class="page">
  <div class="page-head">
    <div>
      <h2 class="page-title">Personas</h2>
      <p class="page-sub">Who <b>you</b> are in a story. A persona is a playable character card — pick one as your “you”, and its backstory rides into every scene. Portable across stories.</p>
    </div>
    <button onclick={() => goto('/settings/personas/new')}>✨ New persona</button>
  </div>

  {#if playable.length}
    <div class="grid">
      {#each playable as c (c.key)}
        <div class="card" class:active={c.key === app.activePlayerChar}
          role="button" tabindex="0" onclick={() => choose(c.key)}>
          <div class="card-av">
            {#if c.reference || c.avatar}
              <img src={c.reference || c.avatar} alt={c.name} />
            {:else}
              <span class="av-ph">🎭</span>
            {/if}
          </div>
          <div class="card-info">
            <div class="card-name">{c.name || c.key}</div>
            <div class="card-summ">{blurb(c)}</div>
          </div>
          {#if c.key === app.activePlayerChar}<span class="active-badge">active</span>{/if}
        </div>
      {/each}
    </div>
  {:else}
    <div class="empty">
      <div class="emk">🎭</div>
      <p>No personas yet.</p>
      <span>Build a “you” to embody in any story.</span>
      <button onclick={() => goto('/settings/personas/new')}>✨ New persona</button>
    </div>
  {/if}
</div>

<style>
  .page { padding: 24px 28px; display: flex; flex-direction: column; gap: 22px; }
  .page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .page-title { margin: 0 0 4px; font-size: 18px; font-weight: 700; }
  .page-sub { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; max-width: 560px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
  .card {
    position: relative; display: flex; flex-direction: column; align-items: center; gap: 10px;
    padding: 18px 14px 14px; cursor: pointer; text-align: center; transition: border-color .12s, background .12s;
  }
  .card:hover { border-color: var(--border); background: var(--elev); }
  .card.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  .card-av {
    width: 76px; height: 100px; border-radius: 10px; overflow: hidden; flex: none;
    background: linear-gradient(135deg, var(--accent), #9a6dff); display: grid; place-items: center;
  }
  .card-av img { width: 100%; height: 100%; object-fit: cover; }
  .av-ph { font-size: 30px; }
  .card-info { display: flex; flex-direction: column; gap: 4px; width: 100%; }
  .card-name { font-size: 13px; font-weight: 650; color: var(--text); }
  .card-summ {
    font-size: 11px; color: var(--muted); line-height: 1.4;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .active-badge {
    position: absolute; top: 9px; left: 9px; font-size: 9.5px; font-weight: 700; text-transform: uppercase;
    letter-spacing: .4px; color: var(--accent); background: color-mix(in srgb, var(--accent) 14%, transparent);
    padding: 2px 7px; border-radius: 999px;
  }
  .edit {
    position: absolute; top: 6px; right: 6px; width: 26px; height: 26px; padding: 0; display: grid;
    place-items: center; font-size: 12px; border-radius: 7px; opacity: 0;
    background: rgba(10,12,18,.7); border: 1px solid var(--border); color: var(--muted);
  }
  .card:hover .edit { opacity: 1; }
  .edit:hover { color: var(--accent); border-color: var(--accent); }

  .empty {
    margin: 50px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column;
    align-items: center; gap: 8px; max-width: 360px;
  }
  .emk {
    width: 52px; height: 52px; display: grid; place-items: center; font-size: 26px; border-radius: 14px;
    background: var(--elev-2); border: 1px solid var(--border); margin-bottom: 4px;
  }
  .empty p { margin: 0; color: var(--text); font-size: 15px; }
  .empty span { font-size: 12.5px; line-height: 1.5; }
  .empty button { margin-top: 10px; }
</style>

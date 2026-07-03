<script>
  // The "Default personas" pane — the playable "you" cards this story suggests. ONE component
  // (this block was copy-pasted verbatim on both the Overview and Cast tabs).
  import { put } from '$lib/api.js';
  import { chars, blurb } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import Section from './Section.svelte';

  let { open = false } = $props();
  let st = $derived(stories.current);
  let playable = $derived((chars.list || []).filter((c) => c.playable));
  const isOn = (k) => (st?.default_personas || []).includes(k);
  async function toggle(k) {
    const cur = st.default_personas || [];
    stories.current.default_personas = cur.includes(k) ? cur.filter((x) => x !== k) : [...cur, k];
    await put(`/stories/${st.key}`, { default_personas: stories.current.default_personas });
  }
</script>

<Section icon="🎭" title="Default personas" count={(st?.default_personas || []).length || ''} {open}>
  <p class="hint">Playable cards this story suggests you embody. They float to the top of the <b>Playing as</b> menu when someone plays — pick the “you” that fits this world.</p>
  {#if playable.length}
    <div class="chips">
      {#each playable as c (c.key)}
        <button class="pchip" class:on={isOn(c.key)} onclick={() => toggle(c.key)} title={blurb(c)}>
          {#if c.reference || c.avatar}<img src={c.reference || c.avatar} alt={c.name} />{:else}<span class="ph">🎭</span>{/if}
          {c.name || c.key}
          <span class="mark">{isOn(c.key) ? '✓' : '+'}</span>
        </button>
      {/each}
    </div>
  {:else}
    <p class="empty">No playable characters yet — make one in <a href="/characters/personas">Characters ▸ Personas</a>.</p>
  {/if}
</Section>

<style>
  .chips { display: flex; flex-wrap: wrap; gap: 7px; }
  .pchip {
    display: inline-flex; align-items: center; gap: 7px; padding: 4px 9px 4px 5px; border-radius: 999px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer;
    font-size: 12px;
  }
  .pchip:hover { color: var(--text); border-color: var(--border); }
  .pchip.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--elev)); }
  .pchip img { width: 20px; height: 20px; border-radius: 50%; object-fit: cover; flex: none; }
  .ph { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; font-size: 11px; background: var(--elev-2); flex: none; }
  .mark { font-size: 11px; color: var(--faint); font-weight: 700; }
  .pchip.on .mark { color: var(--accent); }
  .empty { font-size: 12px; color: var(--faint); margin: 0; }
  .empty a { color: var(--accent); }
</style>

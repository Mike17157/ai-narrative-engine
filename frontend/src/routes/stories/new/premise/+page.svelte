<script>
  // Step 1 — Premise. The seed for the whole story (enough on its own — no spine, no
  // storyboard). Also pick the cast: existing characters you'll develop in the next step.
  import { goto } from '$app/navigation';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';

  let wz = $derived(stories.wizard);
  let roster = $derived(chars.list || []);

  // Seed the cast with the character the wizard was launched from.
  $effect(() => {
    if (wz.character && !wz.castKeys.includes(wz.character)) wz.castKeys = [wz.character, ...wz.castKeys];
  });

  function toggle(key) {
    wz.castKeys = wz.castKeys.includes(key) ? wz.castKeys.filter((k) => k !== key) : [...wz.castKeys, key];
  }
  function next() { wz.step = 1; goto('/stories/new/characters'); }
</script>

<div class="page"><div class="col">
  <h2 class="title">New story</h2>
  <p class="sub">Start from a premise and a cast. We build the characters first, then their outfits, then the scenes — no spine, no storyboard.</p>

  <label class="field">
    <span>Story name</span>
    <input bind:value={wz.name} placeholder="Untitled story" />
  </label>

  <label class="field">
    <span>Premise</span>
    <textarea bind:value={wz.premise} rows="4"
      placeholder="A sentence or two — who, where, the situation. This seeds the cast's scenes."></textarea>
  </label>

  <div class="field">
    <span>Cast — pick the characters this story is about ({wz.castKeys.length})</span>
    <div class="roster">
      {#each roster as c (c.key)}
        <button class="chip" class:on={wz.castKeys.includes(c.key)} onclick={() => toggle(c.key)}>
          {c.name || c.key}
        </button>
      {/each}
    </div>
  </div>

  <div class="acts">
    <button class="ghost" onclick={() => goto('/stories')}>← Library</button>
    <button onclick={next} disabled={!wz.castKeys.length}>Develop characters →</button>
  </div>
</div></div>

<style>
  .title { margin: 0 0 4px; font-size: 20px; font-weight: 680; }
  .sub { margin: 0 0 18px; font-size: 13px; color: var(--muted); max-width: 560px; }
  .field { display: flex; flex-direction: column; gap: 6px; margin-bottom: 16px; }
  .field > span { font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .field input, .field textarea { padding: 9px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13.5px; resize: vertical; }
  .field input:focus, .field textarea:focus { outline: none; border-color: var(--accent); }
  .roster { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .chip.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .acts { display: flex; justify-content: space-between; gap: 10px; margin-top: 22px; }
  .acts button { padding: 9px 18px; font-size: 13.5px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .acts button:disabled { opacity: .45; cursor: default; }
  .acts .ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
</style>

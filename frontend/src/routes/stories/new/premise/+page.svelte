<script>
  // Step 0 — Start. Name the story, give it a premise SEED (optionally generated one-shot from an
  // imported reference card), and pick the cast. The premise's components (protagonist/lie/inciting/
  // opposition/stakes/texture) are no longer drawn out by a scripted interview here — you develop
  // and check them on the story Overview (premise-coverage), with the Author agent.
  import { goto } from '$app/navigation';
  import { post } from '$lib/api.js';
  import { chars } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';

  let wz = $derived(stories.wizard);
  let roster = $derived(chars.list || []);
  let imported = $derived(roster.filter((c) => c.imported));     // reference cards (premise seeds)
  let ours = $derived(roster.filter((c) => !c.imported));        // our characters (the cast)

  let refKeys = $state([]);   // selected reference card(s) to generate a premise from
  let busy = $state(false);
  let err = $state(null);

  // Seed from the card the wizard was launched on — to references if imported, else the cast.
  $effect(() => {
    if (!wz.character) return;
    const c = roster.find((x) => x.key === wz.character);
    if (!c) return;
    if (c.imported) { if (!refKeys.includes(c.key)) refKeys = [c.key, ...refKeys]; }
    else if (!wz.castKeys.includes(c.key)) wz.castKeys = [c.key, ...wz.castKeys];
  });

  const toggleRef = (k) => refKeys = refKeys.includes(k) ? refKeys.filter((x) => x !== k) : [...refKeys, k];
  const toggleCast = (k) => wz.castKeys = wz.castKeys.includes(k) ? wz.castKeys.filter((x) => x !== k) : [...wz.castKeys, k];

  // One-shot: invent a premise seed from the selected reference card(s) (no interview).
  async function premiseFromRef() {
    if (busy || !refKeys.length) return;
    busy = true; err = null;
    const r = await post('/stories/premise-from-character', { characters: refKeys, premise: wz.premise });
    busy = false;
    if (r.ok && r.data?.premise) wz.premise = r.data.premise;
    else err = r.data?.error || 'generation failed';
  }

  function next() { wz.step = 1; goto('/stories/new/characters'); }
</script>

<div class="page"><div class="col wide">
  <h2 class="title">New story</h2>
  <p class="sub">Name it, drop in a premise seed (or generate one from a reference card), and pick the cast.
    You'll flesh the premise out — and check every component is covered — on the Overview.</p>

  <label class="field">
    <span>Story name</span>
    <input bind:value={wz.name} placeholder="Untitled story" />
  </label>

  <div class="field">
    <div class="lblrow">
      <span>Premise <span class="opt">— a seed, optional</span></span>
      {#if refKeys.length}
        <button class="genbtn" onclick={premiseFromRef} disabled={busy}
          title="Invent a premise from the selected reference card(s)">
          {busy ? '…' : '✨ Premise from reference'}
        </button>
      {/if}
    </div>
    <textarea bind:value={wz.premise} rows="3"
      placeholder="What is this story about? Write a line or two — or leave it and build it on the Overview."></textarea>
    {#if err}<span class="generr">{err}</span>{/if}
  </div>

  <div class="field">
    <span>Reference card — seed a premise from an imported card ({refKeys.length})</span>
    {#if imported.length}
      <div class="roster">
        {#each imported as c (c.key)}
          <button class="chip ref" class:on={refKeys.includes(c.key)} onclick={() => toggleRef(c.key)}>
            {#if c.reference || c.avatar}<img class="cav" src={c.reference || c.avatar} alt="" />{/if}
            {c.name || c.key}
          </button>
        {/each}
      </div>
    {:else}
      <p class="hint">No imported cards yet. <a href="/characters/import">Import a character card</a> to seed a premise from it.</p>
    {/if}
  </div>

  <div class="field">
    <span>Cast — your characters in this story ({wz.castKeys.length})</span>
    {#if ours.length}
      <div class="roster">
        {#each ours as c (c.key)}
          <button class="chip" class:on={wz.castKeys.includes(c.key)} onclick={() => toggleCast(c.key)}>
            {c.name || c.key}
          </button>
        {/each}
      </div>
    {:else}
      <p class="hint">No characters yet. <a href="/characters/personas/new">Build one</a> to add to the cast.</p>
    {/if}
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
  .opt { text-transform: none; font-weight: 400; letter-spacing: 0; color: var(--faint); }
  .lblrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .genbtn { font-size: 11.5px; font-weight: 600; padding: 4px 11px; border-radius: 7px; cursor: pointer;
    background: color-mix(in srgb, var(--accent) 12%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    color: var(--accent); }
  .genbtn:disabled { opacity: .4; cursor: default; }
  .genbtn:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .generr { font-size: 12px; color: var(--bad); }
  .field input, .field textarea { padding: 9px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13.5px; resize: vertical; }
  .field input:focus, .field textarea:focus { outline: none; border-color: var(--accent); }
  .hint { margin: 0; font-size: 12.5px; color: var(--faint); }
  .hint a { color: var(--accent); }
  .roster { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; padding: 5px 12px; border-radius: 999px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .chip.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .chip.ref { padding-left: 5px; }
  .cav { width: 18px; height: 18px; border-radius: 50%; object-fit: cover; }
  .acts { display: flex; justify-content: space-between; gap: 10px; margin-top: 22px; }
  .acts button { padding: 9px 18px; font-size: 13.5px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .acts button:disabled { opacity: .45; cursor: default; }
  .acts .ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
</style>

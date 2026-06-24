<script>
  // Step 4 — Scenes. Generate the story's locations straight from the premise (no storyboard),
  // then save the whole thing as a Story. Backgrounds are rendered later, per location.
  import { goto } from '$app/navigation';
  import { stories, genScenesFromPremise, saveLeanStory } from '$lib/stories.svelte.js';

  let wz = $derived(stories.wizard);
  let locations = $derived(wz.locations || []);

  function removeLoc(i) {
    const next = [...locations];
    const [gone] = next.splice(i, 1);
    wz.locations = next;
    if (wz.start === gone?.id) wz.start = next[0]?.id || null;
  }
</script>

<div class="page"><div class="col wide">
  <div class="head">
    <div>
      <h2 class="title">Scenes</h2>
      <p class="sub">Locations generated from your premise — the places this story visits.</p>
    </div>
    <div class="right">
      <button class="ghost" onclick={() => { wz.step = 2; goto('/stories/new/outfits'); }}>← Outfits</button>
      <button class="gen" onclick={genScenesFromPremise} disabled={wz.busy || !wz.premise}>
        {wz.busy ? 'Generating…' : locations.length ? '↻ Regenerate' : 'Generate locations'}
      </button>
    </div>
  </div>

  {#if wz.error}<div class="err">{wz.error}</div>{/if}

  {#if locations.length}
    <div class="grid">
      {#each locations as l, i (l.id)}
        <div class="loc" class:start={wz.start === l.id}>
          <div class="loc-head">
            <input class="lname" bind:value={l.name} />
            <button class="x" title="Remove" onclick={() => removeLoc(i)}>✕</button>
          </div>
          <textarea class="ldesc" bind:value={l.description} rows="2" placeholder="Description"></textarea>
          <button class="startbtn" class:on={wz.start === l.id} onclick={() => (wz.start = l.id)}>
            {wz.start === l.id ? '★ Starting location' : 'Set as start'}
          </button>
        </div>
      {/each}
    </div>

    <div class="save">
      <button class="primary" onclick={saveLeanStory} disabled={stories.saving || !wz.castKeys.length}>
        {stories.saving ? 'Saving…' : 'Save story →'}
      </button>
      <span class="hint">{wz.castKeys.length} cast · {locations.length} locations{wz.arcs?.length ? ` · ${wz.arcs.length} arcs` : ''}</span>
    </div>
  {:else}
    <div class="empty">
      <p>No locations yet.</p>
      <span>Generate them from your premise, or skip and save the cast as-is.</span>
      <div class="erow">
        <button class="gen" onclick={genScenesFromPremise} disabled={wz.busy || !wz.premise}>Generate locations</button>
        <button class="ghost" onclick={saveLeanStory} disabled={stories.saving || !wz.castKeys.length}>Skip & save story →</button>
      </div>
    </div>
  {/if}
</div></div>

<style>
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 16px; flex-wrap: wrap; }
  .title { margin: 0; font-size: 19px; font-weight: 680; }
  .sub { margin: 3px 0 0; font-size: 12.5px; color: var(--muted); }
  .right { display: flex; gap: 8px; }
  .gen, .primary { padding: 8px 16px; font-size: 13px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .gen:disabled, .primary:disabled { opacity: .45; cursor: default; }
  .ghost { padding: 8px 14px; font-size: 13px; border-radius: 9px; background: transparent; border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .err { color: var(--bad); font-size: 12.5px; margin-bottom: 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
  .loc { border: 1px solid var(--border-soft); border-radius: 10px; padding: 11px; background: var(--panel); display: flex; flex-direction: column; gap: 7px; }
  .loc.start { border-color: color-mix(in srgb, var(--accent) 45%, transparent); }
  .loc-head { display: flex; gap: 6px; align-items: center; }
  .lname { flex: 1; padding: 6px 9px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; font-weight: 600; }
  .ldesc { padding: 7px 9px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border); color: var(--muted); font: inherit; font-size: 12.5px; resize: vertical; }
  .x { background: none; border: 0; color: var(--faint); cursor: pointer; font-size: 12px; }
  .x:hover { color: var(--bad); }
  .startbtn { align-self: flex-start; background: none; border: 0; color: var(--faint); font-size: 11.5px; cursor: pointer; padding: 0; }
  .startbtn.on { color: var(--accent); }
  .save { display: flex; align-items: center; gap: 12px; margin-top: 18px; }
  .hint { font-size: 12px; color: var(--faint); }
  .empty { margin: 40px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; gap: 8px; align-items: center; }
  .empty p { margin: 0; color: var(--text); font-size: 15px; }
  .erow { display: flex; gap: 10px; margin-top: 10px; }
</style>

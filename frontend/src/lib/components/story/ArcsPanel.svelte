<script>
  // Weave THEMED ARCS from a developed cast (Phase 2). Drama arises from who the characters
  // are — their exemplars — not from a story spine. Each arc carries its own themes.
  import { goto } from '$app/navigation';
  import { post } from '$lib/api.js';
  import { loadStories } from '$lib/stories.svelte.js';

  let { roster = [], defaultKeys = [] } = $props();

  let chosen = $state([...defaultKeys]);
  let premise = $state('');
  let arcs = $state([]);
  let cast = $state([]);
  let busy = $state(false);
  let err = $state(null);
  let open = $state(false);
  let storyName = $state('');

  function toggle(key) {
    chosen = chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key];
  }

  async function weave() {
    if (chosen.length < 1) { err = 'Pick at least one character.'; return; }
    busy = true; err = null; arcs = [];
    const r = await post('/stories/arcs/weave', { characters: chosen, premise });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'arc weave failed'; return; }
    arcs = r.data.arcs || [];
    cast = r.data.cast || [];
  }

  async function saveStory() {
    busy = true; err = null;
    const r = await post('/stories/from-cast', {
      name: storyName, premise, characters: chosen, arcs,
    });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'could not save story'; return; }
    await loadStories();
    goto(`/stories/${r.data.key}/overview`);
  }
</script>

<div class="arcs">
  <button class="bar" onclick={() => (open = !open)}>
    <span class="ico">{open ? '▾' : '▸'}</span> Weave themed arcs from your cast
  </button>

  {#if open}
    <div class="body">
      <div class="row">
        {#each roster as c (c.key)}
          <button class="chip" class:on={chosen.includes(c.key)} onclick={() => toggle(c.key)}>
            {c.name || c.key}
          </button>
        {/each}
      </div>
      <div class="run-row">
        <input bind:value={premise} placeholder="Optional steer — kind of story, setting, tone…" />
        <button class="go" onclick={weave} disabled={busy || chosen.length < 1}>
          {busy ? 'Weaving…' : 'Weave arcs'}
        </button>
      </div>

      {#if err}<div class="err">{err}</div>{/if}

      {#if arcs.length}
        <div class="arc-list">
          {#each arcs as a, i (i)}
            <div class="arc">
              <div class="arc-head">
                <span class="arc-name">{a.name}</span>
                <div class="themes">
                  {#each a.themes || [] as t (t)}<span class="theme">{t}</span>{/each}
                </div>
              </div>
              <p class="premise">{a.premise}</p>
              <div class="meta">
                {#if a.spotlight?.length}<span class="spot">★ {a.spotlight.join(', ')}</span>{/if}
                {#if a.turn}<span class="turn">↳ {a.turn}</span>{/if}
              </div>
            </div>
          {/each}
        </div>

        <div class="save-row">
          <input bind:value={storyName} placeholder="Story name (optional)" />
          <button class="go" onclick={saveStory} disabled={busy}>
            {busy ? 'Saving…' : 'Save as story →'}
          </button>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .arcs { border: 1px solid var(--border-soft); border-radius: 12px; background: var(--panel); margin-bottom: 14px; }
  .bar { width: 100%; text-align: left; background: none; border: 0; padding: 11px 14px; color: var(--text);
    font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .ico { color: var(--muted); }
  .body { padding: 0 14px 14px; display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; padding: 4px 11px; border-radius: 999px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .chip.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .run-row { display: flex; gap: 8px; align-items: center; }
  .run-row input { flex: 1; padding: 8px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; }
  .run-row input:focus { outline: none; border-color: var(--accent); }
  .go { padding: 7px 14px; border-radius: 9px; background: var(--accent); color: #fff; border: 0;
    cursor: pointer; font-size: 13px; white-space: nowrap; }
  .go:disabled { opacity: .45; cursor: default; }
  .arc-list { display: flex; flex-direction: column; gap: 10px; }
  .arc { border: 1px solid var(--border-soft); border-radius: 10px; padding: 11px 13px; background: var(--elev); }
  .arc-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 5px; }
  .arc-name { font-size: 13.5px; font-weight: 680; }
  .themes { display: flex; flex-wrap: wrap; gap: 4px; }
  .theme { font-size: 10.5px; padding: 1px 8px; border-radius: 999px; color: var(--accent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
  .save-row { display: flex; gap: 8px; align-items: center; padding-top: 4px; border-top: 1px solid var(--border-soft); margin-top: 2px; }
  .save-row input { flex: 1; padding: 8px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; }
  .save-row input:focus { outline: none; border-color: var(--accent); }
  .premise { margin: 0 0 6px; font-size: 12.5px; color: var(--muted); line-height: 1.5; }
  .meta { display: flex; flex-wrap: wrap; gap: 12px; font-size: 11.5px; }
  .spot { color: var(--text); }
  .turn { color: var(--faint); }
  .err { font-size: 12.5px; color: var(--bad); }
</style>

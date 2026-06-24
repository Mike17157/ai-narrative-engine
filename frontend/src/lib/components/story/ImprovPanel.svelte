<script>
  // Improv → harvest: put 2-3 characters in a scene, watch them bounce off each other, then
  // distil what they revealed into new exemplars on each character's lorebook.
  import { post } from '$lib/api.js';

  let { roster = [], defaultKeys = [], onHarvest } = $props();

  let chosen = $state([...defaultKeys]);
  let situation = $state('');
  let transcript = $state([]);
  let busy = $state(false);
  let note = $state('');
  let err = $state(null);
  let open = $state(false);

  function toggle(key) {
    chosen = chosen.includes(key) ? chosen.filter((k) => k !== key) : [...chosen, key];
  }
  function nameOf(key) { return roster.find((c) => c.key === key)?.name || key; }

  async function run() {
    if (chosen.length < 2) { err = 'Pick at least two characters.'; return; }
    busy = true; err = null; note = ''; transcript = [];
    const r = await post('/stories/improv', { characters: chosen, situation });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'improv failed'; return; }
    transcript = r.data.transcript || [];
    situation = r.data.situation || situation;
  }

  async function harvest() {
    busy = true; err = null;
    const r = await post('/stories/improv/harvest', { characters: chosen, transcript });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'harvest failed'; return; }
    const counts = Object.entries(r.data.characters || {})
      .map(([k, v]) => `${nameOf(k)} +${v.added.length}`).join(' · ');
    note = `Harvested → ${counts || 'nothing new'}`;
    onHarvest?.();
  }

  async function contrast() {
    if (chosen.length < 2) { err = 'Pick at least two characters to contrast.'; return; }
    busy = true; err = null; note = '';
    const r = await post('/stories/contrast', { characters: chosen });
    busy = false;
    if (!r.data?.ok) { err = r.data?.error || 'contrast failed'; return; }
    const counts = Object.entries(r.data.characters || {})
      .map(([k, v]) => `${nameOf(k)} +${v.added.length}`).join(' · ');
    note = `Differentiated → ${counts || 'nothing new'}`;
    onHarvest?.();
  }
</script>

<div class="improv">
  <button class="bar" onclick={() => (open = !open)}>
    <span class="ico">{open ? '▾' : '▸'}</span> Run a scene — let characters bounce off each other
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
        <input bind:value={situation} placeholder="Optional situation — e.g. 'stuck in a stalled elevator'" />
        <button class="go" onclick={run} disabled={busy || chosen.length < 2}>
          {busy && !transcript.length ? 'Running…' : 'Run scene'}
        </button>
        <button class="go alt" onclick={contrast} disabled={busy || chosen.length < 2}
          title="Invent exemplars that make each selected character distinct from the others">
          Differentiate
        </button>
      </div>

      {#if err}<div class="err">{err}</div>{/if}

      {#if transcript.length}
        <div class="script">
          {#each transcript as t, i (i)}
            <div class="line"><span class="sp">{t.speaker}</span> {t.text}</div>
          {/each}
        </div>
        <div class="harvest-row">
          <button class="go" onclick={harvest} disabled={busy}>
            {busy ? 'Harvesting…' : 'Harvest into exemplars'}
          </button>
          {#if note}<span class="note">{note}</span>{/if}
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .improv { border: 1px solid var(--border-soft); border-radius: 12px; background: var(--panel); margin-bottom: 14px; }
  .bar { width: 100%; text-align: left; background: none; border: 0; padding: 11px 14px; color: var(--text);
    font: inherit; font-size: 13px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .ico { color: var(--muted); }
  .body { padding: 0 14px 14px; display: flex; flex-direction: column; gap: 10px; }
  .row { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { font-size: 12px; padding: 4px 11px; border-radius: 999px; cursor: pointer;
    background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .chip.on { background: color-mix(in srgb, var(--accent) 14%, transparent);
    border-color: color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent); }
  .run-row, .harvest-row { display: flex; gap: 8px; align-items: center; }
  .run-row input { flex: 1; padding: 8px 11px; border-radius: 9px; background: var(--elev);
    border: 1px solid var(--border); color: var(--text); font: inherit; font-size: 13px; }
  .run-row input:focus { outline: none; border-color: var(--accent); }
  .go { padding: 7px 14px; border-radius: 9px; background: var(--accent); color: #fff; border: 0;
    cursor: pointer; font-size: 13px; white-space: nowrap; }
  .go.alt { background: transparent; border: 1px solid var(--border); color: var(--text); }
  .go.alt:hover { border-color: var(--accent); color: var(--accent); }
  .go:disabled { opacity: .45; cursor: default; }
  .script { display: flex; flex-direction: column; gap: 6px; max-height: 260px; overflow-y: auto;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 10px; padding: 11px; }
  .line { font-size: 12.5px; line-height: 1.5; color: var(--text); }
  .sp { font-weight: 700; color: var(--accent); margin-right: 4px; }
  .note { font-size: 12px; color: var(--good); }
  .err { font-size: 12.5px; color: var(--bad); }
</style>

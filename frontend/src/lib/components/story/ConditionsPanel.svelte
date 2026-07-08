<script>
  // SETTING CONDITIONS — the recurring stages the world moves through. Fields are DIRECTLY
  // TYPE-EDITABLE in place; the targeted actions (generate / add / remove / kind-picker) are gone —
  // structural + generative ops go to the section editor. Usage/orphan info is read-only.
  import { get } from '$lib/api.js';

  let { storyKey, conditions = [], onChange = () => {} } = $props();

  const ICON = { seasonal: '❄', event: '⚔', place: '🚪' };

  // LINT: how many cast exemplars each stage activates (`when:<id>`), + orphaned when: ids.
  let usage = $state({});
  let orphans = $state({});
  async function loadUsage() {
    const r = await get(`/stories/${storyKey}/conditions/usage`);
    usage = r?.usage || {}; orphans = r?.orphans || {};
  }
  loadUsage();

  function edit() { onChange([...(conditions || [])]); }   // fields bind directly; nudge a save
</script>

<div class="conds">
  <p class="hint">The stages this world moves through — each a different way to live that brings out
    new sides of the cast. Situational character reactions and secrets key to these. Add or remove
    stages in the editor.</p>

  {#if Object.keys(orphans).length}
    <div class="orph">⚠ Situational character content bound to stages that no longer exist
      (dormant until the id is restored or the exemplars re-tagged):
      {#each Object.entries(orphans) as [id, n] (id)}<code>{id}</code> ({n}){/each}
    </div>
  {/if}

  {#each conditions as c, i (c.id || i)}
    <div class="card">
      <div class="crow">
        <span class="ic">{ICON[c.kind] || '◆'}</span>
        <input class="nm" bind:value={c.name} oninput={edit} placeholder="stage name — 'The flood season'" />
        {#if c.kind}<span class="kind">{c.kind}</span>{/if}
        <span class="use" class:zero={!usage[c.id]}
          title="cast exemplars this stage activates (when:{c.id})">⚡ {usage[c.id] || 0}</span>
      </div>
      <input class="fld" bind:value={c.description} oninput={edit} placeholder="what it is — the objective world-change" />
      <input class="fld" bind:value={c.effect} oninput={edit} placeholder="how it bends daily life — what stops, what people do differently" />
    </div>
  {:else}
    <p class="none">No stages yet — ask the editor to add some.</p>
  {/each}
</div>

<style>
  .conds { display: flex; flex-direction: column; gap: 8px; }
  .hint { margin: 0; }
  .none { font-size: 12.5px; color: var(--faint); margin: 0; }

  .card { display: flex; flex-direction: column; gap: 5px; padding: 9px 11px; border-radius: 10px; background: var(--panel); border: 1px solid var(--border-soft); }
  .crow { display: flex; align-items: center; gap: 8px; }
  .ic { flex: none; font-size: 15px; }
  .nm { flex: 1; min-width: 0; font-size: 13px; font-weight: 700; color: var(--text); background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 4px 7px; }
  .nm:hover { border-color: var(--border-soft); } .nm:focus { outline: none; border-color: var(--accent); background: var(--elev); }
  .kind { flex: none; font-size: 10px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .fld { width: 100%; box-sizing: border-box; font-size: 12px; color: var(--text); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 7px; padding: 6px 8px; }
  .fld:focus { outline: none; border-color: var(--accent); }
  .use { flex: none; font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev);
         border: 1px solid var(--border-soft); color: var(--accent); cursor: default; }
  .use.zero { color: var(--faint); }
  .orph { font-size: 11.5px; color: var(--bad, #d0655a); padding: 8px 11px; border-radius: 9px;
          border: 1px solid color-mix(in srgb, var(--bad, #d0655a) 35%, transparent);
          background: color-mix(in srgb, var(--bad, #d0655a) 6%, var(--panel)); line-height: 1.6; }
  .orph code { margin: 0 3px; padding: 1px 6px; border-radius: 5px; background: var(--elev); color: var(--text); }
</style>

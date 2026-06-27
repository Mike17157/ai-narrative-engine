<script>
  // Character detail modal — opens on a relationship-node click AND when the narrative agent
  // modifies a character. Shows the inner-life fields, bonds, persona excerpt + a link to the
  // full card / cast. See [[overview-is-editor]].
  import Modal from '$lib/components/shared/Modal.svelte';

  let { char = null, bonds = [], storyKey = '', onClose = () => {} } = $props();

  const FIELDS = [
    ['role', 'Role'], ['arc_type', 'Arc'], ['want', 'Want'], ['need', 'Need'],
    ['lie', 'Lie'], ['wound', 'Wound'], ['conflict', 'Conflict'], ['growth', 'Growth'],
  ];
</script>

{#if char}
  <Modal {onClose} width="560px" flush maxHeight="86vh">
    <div class="ci-head">
      {#if char.reference || char.avatar}
        <img class="ci-av" src={char.reference || char.avatar} alt={char.name} />
      {:else}<span class="ci-ph">🎭</span>{/if}
      <div class="ci-title">
        <b>{char.name}</b>
        {#if char.fields?.role}<span class="ci-role">{char.fields.role}</span>{/if}
      </div>
      <button class="ci-x" onclick={onClose} aria-label="Close">✕</button>
    </div>

    <div class="ci-body">
      <div class="ci-grid">
        {#each FIELDS as [k, label]}
          {#if char.fields?.[k]}
            <div class="ci-field"><span class="ci-k">{label}</span><span class="ci-v">{char.fields[k]}</span></div>
          {/if}
        {/each}
      </div>

      {#if bonds.length}
        <div class="ci-section">Bonds</div>
        <div class="ci-bonds">
          {#each bonds as b}
            <span class="ci-bond" class:pos={b.stance === 'warm' || b.stance === 'devoted'}
                  class:neg={b.stance === 'strained' || b.stance === 'hostile'} title={b.dynamic}>
              {b.dir} {b.other}{b.nature ? ` (${b.nature})` : ''}: {b.dynamic || b.stance}
            </span>
          {/each}
        </div>
      {/if}

      {#if char.system}
        <div class="ci-section">Persona</div>
        <p class="ci-persona">{char.system}</p>
      {/if}
    </div>

    <div class="ci-foot">
      <a class="ci-open" href={`/characters/selected?c=${char.key}`}>Open full card →</a>
      {#if storyKey}<a class="ci-open" href={`/stories/${storyKey}/cast?c=${char.key}`}>Cast & wardrobe →</a>{/if}
    </div>
  </Modal>
{/if}

<style>
  .ci-head { display: flex; align-items: center; gap: 12px; padding: 14px 16px 12px;
             border-bottom: 1px solid var(--border-soft); flex: none; }
  .ci-av { width: 46px; height: 46px; border-radius: 50%; object-fit: cover; flex: none; }
  .ci-ph { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center;
           background: var(--elev-2); flex: none; font-size: 22px; }
  .ci-title { display: flex; flex-direction: column; gap: 2px; flex: 1; min-width: 0; }
  .ci-title b { font-size: 17px; color: var(--text); }
  .ci-role { font-size: 12px; color: var(--accent); }
  .ci-x { width: 28px; height: 28px; flex: none; border-radius: 7px; background: var(--elev);
          border: 1px solid var(--border); color: var(--muted); font-size: 11px; cursor: pointer; box-shadow: none; }
  .ci-x:hover { color: var(--text); background: var(--elev-2); filter: none; }

  .ci-body { padding: 14px 16px; overflow: auto; flex: 1; display: flex; flex-direction: column; gap: 12px; }
  .ci-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; }
  .ci-field { display: flex; flex-direction: column; gap: 2px; }
  .ci-k { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .ci-v { font-size: 12.5px; color: var(--text); line-height: 1.45; }
  .ci-section { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
                color: var(--faint); margin-top: 2px; }
  .ci-bonds { display: flex; flex-wrap: wrap; gap: 6px; }
  .ci-bond { font-size: 11.5px; padding: 3px 9px; border-radius: 999px; background: var(--elev);
             border: 1px solid var(--border-soft); color: var(--muted); }
  .ci-bond.pos { border-color: rgba(110,199,127,.4); color: #8fd39c; }
  .ci-bond.neg { border-color: rgba(224,122,122,.4); color: #e69a9a; }
  .ci-persona { font-size: 13px; color: var(--muted); line-height: 1.6; margin: 0; white-space: pre-wrap; }

  .ci-foot { display: flex; gap: 16px; padding: 12px 16px; border-top: 1px solid var(--border-soft); flex: none; }
  .ci-open { font-size: 12.5px; color: var(--accent); }
</style>

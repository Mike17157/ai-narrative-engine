<script>
  // Non-linear build overview: every step stacked with its status, a read-only
  // preview of what it produced, and "Go to step" / "Re-run" actions. Presentational —
  // the host wires onGoStep(route) and onRerun(route). `source` is the live wizard or
  // a loaded story (board vs storyboard handled defensively).
  import { storySteps } from '$lib/stories.svelte.js';
  import SpineDisplay from './SpineDisplay.svelte';

  let { source, onGoStep, onRerun } = $props();

  let steps = $derived(storySteps(source));
  let board = $derived(source?.board || source?.storyboard || null);
  let beats = $derived(board?.beats || []);
  let locations = $derived(Array.isArray(source?.locations) ? source.locations : []);
  let cast = $derived(Array.isArray(source?.cast) ? source.cast : []);
  let primary = $derived(cast.find((c) => c.primary) || cast[0] || null);

  const STATUS_LABEL = { done: 'Done', active: 'In progress', todo: 'Not started' };
</script>

<div class="hub">
  {#each steps as st (st.key)}
    <div class="step {st.status}">
      <div class="shead">
        <span class="dot {st.status}"></span>
        <span class="slabel">{st.label}</span>
        <span class="sstatus {st.status}">{STATUS_LABEL[st.status]}</span>
        <span class="spacer"></span>
        <button class="ghost sm" onclick={() => onGoStep?.(st.route)}>Go to step</button>
        <button class="ghost sm" onclick={() => onRerun?.(st.route)}>↻ Re-run</button>
      </div>

      <div class="sbody">
        {#if st.key === 'spine'}
          {#if source?.spine}<SpineDisplay spine={source.spine} compact />
          {:else}<p class="empty">No spine yet.</p>{/if}

        {:else if st.key === 'storyboard'}
          {#if beats.length}
            <p class="lead">{beats.length} chapter{beats.length === 1 ? '' : 's'}{board?.logline ? ` · ${board.logline}` : ''}</p>
            <ol class="beatlist">
              {#each beats.slice(0, 4) as b, i (i)}<li>{b.title || `Chapter ${i + 1}`}</li>{/each}
              {#if beats.length > 4}<li class="more">+{beats.length - 4} more</li>{/if}
            </ol>
          {:else}<p class="empty">No storyboard yet.</p>{/if}

        {:else if st.key === 'scenes'}
          {#if locations.length}
            <p class="lead">{locations.length} location{locations.length === 1 ? '' : 's'}</p>
            <div class="pills">
              {#each locations.slice(0, 6) as l (l.id || l.name)}<span class="pill">{l.name}</span>{/each}
              {#if locations.length > 6}<span class="pill more">+{locations.length - 6}</span>{/if}
            </div>
          {:else}<p class="empty">No scenes yet.</p>{/if}

        {:else if st.key === 'cast'}
          {#if cast.length}
            <p class="lead">{cast.length} cast{primary?.name ? ` · ${primary.name} (lead)` : ''}</p>
            <div class="pills">
              {#each cast.slice(0, 8) as c (c.name || c.character)}
                <span class="pill" class:lead={c.primary}>{c.name || c.character}</span>
              {/each}
            </div>
          {:else}<p class="empty">No cast yet.</p>{/if}
        {/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .hub { display: flex; flex-direction: column; gap: 12px; }
  .step { border: 1px solid var(--border-soft); border-radius: 12px; padding: 14px 16px;
    background: var(--panel); }
  .step.active { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }

  .shead { display: flex; align-items: center; gap: 9px; margin-bottom: 10px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--faint); }
  .dot.done { background: var(--accent); }
  .dot.active { background: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }
  .dot.todo { background: var(--border); }
  .slabel { font-size: 14px; font-weight: 650; }
  .sstatus { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; }
  .sstatus.done { color: var(--accent); }
  .sstatus.active { color: var(--accent); }
  .sstatus.todo { color: var(--faint); }
  .spacer { flex: 1; }
  .ghost.sm { padding: 4px 11px; font-size: 12px; border-radius: 8px; background: transparent;
    border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .ghost.sm:hover { border-color: var(--accent); color: var(--accent); }

  .sbody { font-size: 12.5px; color: var(--muted); }
  .lead { margin: 0 0 8px; color: var(--text); }
  .empty { margin: 0; color: var(--faint); font-style: italic; }
  .beatlist { margin: 0; padding-left: 18px; display: flex; flex-direction: column; gap: 3px; }
  .beatlist .more { list-style: none; color: var(--faint); margin-left: -18px; }
  .pills { display: flex; flex-wrap: wrap; gap: 5px; }
  .pill { font-size: 11px; background: var(--elev-2); border: 1px solid var(--border-soft);
    border-radius: 999px; padding: 2px 9px; color: var(--muted); }
  .pill.lead { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 40%, transparent); }
  .pill.more { color: var(--faint); }
</style>

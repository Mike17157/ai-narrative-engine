<script>
  // An in-progress (draft) story card: portrait + name + the 4-step progress chips
  // (Spine · Storyboard · Scenes · Cast), driven by artifact presence, plus actions.
  import { storySteps } from '$lib/stories.svelte.js';

  let { draft, onResume, onOverview, onDiscard } = $props();
  let steps = $derived(storySteps(draft));

  function relTime(iso) {
    if (!iso) return '';
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
    return `${Math.floor(diff / 86400)}d ago`;
  }
</script>

<div class="card draft-card" onclick={() => onResume?.(draft)} role="button" tabindex="0"
     onkeydown={(e) => { if (e.key === 'Enter') onResume?.(draft); }}>
  <div class="dportrait">
    {#if draft.character}
      <img src="/api/characters/{draft.character}/reference" alt={draft.charName || draft.name}
           onerror={(e) => (e.target.style.display = 'none')} />
    {/if}
  </div>

  <div class="dbody">
    <div class="cname">{draft.name || draft.charName || 'Untitled story'}</div>
    {#if draft.premise}<p class="cprem">{draft.premise}</p>{/if}

    <div class="chips">
      {#each steps as st (st.key)}
        <span class="chip {st.status}" title="{st.label} · {st.status}">
          {#if st.status === 'done'}✓ {/if}{st.label}
        </span>
      {/each}
    </div>

    <div class="dactions">
      <button class="resume" onclick={(e) => { e.stopPropagation(); onResume?.(draft); }}>Resume</button>
      <button class="ghost sm" onclick={(e) => { e.stopPropagation(); onOverview?.(draft); }}>Overview</button>
      {#if draft.updated_at}<span class="dtime">{relTime(draft.updated_at)}</span>{/if}
    </div>
  </div>

  <button class="del" title="Discard draft"
          onclick={(e) => { e.stopPropagation(); onDiscard?.(draft); }}>✕</button>
</div>

<style>
  .card {
    position: relative; background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: 14px; padding: 14px; cursor: pointer;
  }
  .card:hover { background: var(--elev); }

  .draft-card { display: flex; gap: 12px; align-items: flex-start;
    border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); }
  .draft-card:hover { border-color: color-mix(in srgb, var(--accent) 50%, var(--border)); }
  .dportrait { width: 48px; height: 64px; flex: none; border-radius: 8px; background: var(--elev-2);
    border: 1px solid var(--border); overflow: hidden; }
  .dportrait img { width: 100%; height: 100%; object-fit: cover; }
  .dbody { flex: 1; min-width: 0; }

  .cname { font-size: 15px; font-weight: 650; margin-bottom: 6px; }
  .cprem { margin: 0 0 10px; font-size: 12.5px; color: var(--muted);
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

  /* step chips */
  .chips { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 12px; }
  .chip { font-size: 10.5px; font-weight: 600; border-radius: 999px; padding: 2px 9px;
    border: 1px solid var(--border-soft); background: var(--elev-2); color: var(--faint); }
  .chip.todo { color: var(--faint); opacity: .72; }
  .chip.active { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, transparent);
    background: color-mix(in srgb, var(--accent) 10%, transparent); }
  .chip.done { color: #fff; border-color: transparent;
    background: color-mix(in srgb, var(--accent) 80%, transparent); }

  .dactions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .resume { padding: 5px 14px; font-size: 12.5px; border-radius: 8px;
    background: var(--accent); color: #fff; border: 0; cursor: pointer; }
  .ghost.sm { padding: 5px 12px; font-size: 12.5px; border-radius: 8px;
    background: transparent; border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .ghost.sm:hover { border-color: var(--accent); color: var(--accent); }
  .dtime { font-size: 11px; color: var(--faint); margin-left: auto; }

  .del { position: absolute; top: 8px; right: 8px; width: 26px; height: 26px; padding: 0;
    border-radius: 7px; background: rgba(10,12,18,.6); border: 1px solid var(--border);
    color: var(--muted); font-size: 12px; opacity: 0; cursor: pointer; }
  .card:hover .del { opacity: 1; }
  .del:hover { color: var(--bad); }
</style>

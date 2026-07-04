<script>
  // The CONTEXT CARD readout — the pipeline as five content cards (overview → cast), each
  // showing THE ACTUAL INFORMATION its layer feeds downstream (style + premise parts, arcs,
  // who lives where, which scenes exist, sprite coverage) plus what's still to do. Click a
  // card to open its tab. Data = GET /stories/{key}/card (loom/stories/card.py); refetched
  // on tab switch so finishing a step updates its card.
  import { get } from '$lib/api.js';
  import { goto } from '$app/navigation';
  import { charName } from '$lib/characters.svelte.js';

  let { storyKey, active = '' } = $props();

  const LABEL = { overview: 'Overview', plot: 'Plot', relationships: 'Relationships',
                  map: 'Map', cast: 'Cast' };
  let layers = $state([]);
  async function load() {
    try { layers = (await get(`/stories/${storyKey}/card`))?.layers || []; }
    catch { /* keep the stale rail */ }
  }
  $effect(() => { void active; if (storyKey) load(); });

  const href = (id) => id === 'cast' ? `/stories/${storyKey}/cast`
                                     : `/stories/${storyKey}/structure?tab=${id}`;
  const nm = (k) => charName(k) || k;
  const clip = (s, n = 72) => { s = (s || '').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  // Each layer's digest: the real information it contributes, as short lines.
  function digest(l) {
    const c = l.content || {};
    if (l.id === 'overview') {
      const parts = c.premise_parts || {};
      const done = Object.keys(parts).length, total = c.premise_parts_total || 7;
      return [
        { t: clip(c.art_style, 64), tag: c.art_style_source === 'story' ? '🎨' : '🎨 global' },
        { t: clip(c.premise) || 'no premise yet', dim: !c.premise },
        { t: `${done}/${total} premise components`, dim: done < total },
      ];
    }
    if (l.id === 'plot') {
      const arcs = c.arcs || [];
      if (!arcs.length && !c.flat_beats) return [{ t: 'no plot yet', dim: true }];
      return [
        ...arcs.slice(0, 2).map((a) => ({ t: `${a.name || a.id} · ${a.beats} beats` })),
        ...(c.flat_beats ? [{ t: `${c.flat_beats} storyboard beats` }] : []),
      ];
    }
    if (l.id === 'relationships') {
      const mem = c.cast || [];
      return [
        { t: mem.map((m) => `${m.primary ? '★' : ''}${nm(m.character)}`).join(' · ') || 'no cast', dim: !mem.length },
        { t: `${c.bonds || 0} bond${c.bonds === 1 ? '' : 's'} · ${mem.filter((m) => m.home).length}/${mem.length} placed` },
      ];
    }
    if (l.id === 'map') {
      const locs = c.locations || [];
      const done = locs.filter((x) => x.has_image).length;
      return [
        { t: locs.map((x) => `${x.has_image ? '🖼' : '▢'}${x.name || x.id}`).join(' · ') || 'no locations', dim: !locs.length },
        { t: `${done}/${locs.length} scene images` },
      ];
    }
    if (l.id === 'cast') {
      const chars = c.characters || [];
      return chars.length
        ? chars.slice(0, 3).map((ch) => {
            const total = ch.outfits.reduce((n, o) => n + o.range, 0);
            const done = ch.outfits.reduce((n, o) => n + Math.min(o.rendered, o.range || o.rendered), 0);
            return { t: `${nm(ch.character)} — ${ch.outfits.length} outfit${ch.outfits.length === 1 ? '' : 's'}, ${done}/${total || '?'} sprites` };
          })
        : [{ t: 'no cast yet', dim: true }];
    }
    return [];
  }
</script>

{#if layers.length}
  <div class="rail">
    {#each layers as l (l.id)}
      <button class="lcard" class:here={active === l.id} onclick={() => goto(href(l.id))}
              title={l.todo.length ? l.todo.join('\n') : 'Step complete'}>
        <div class="lhead">
          <span class="lname">{LABEL[l.id] || l.id}</span>
          <span class="badge" class:ok={!l.todo.length}>{l.todo.length || '✓'}</span>
        </div>
        <div class="lbody">
          {#each digest(l) as d}
            <div class="line" class:dim={d.dim}>{#if d.tag}<span class="tag">{d.tag}</span>{/if}{d.t}</div>
          {/each}
        </div>
        {#if l.todo.length}<div class="todo">☐ {clip(l.todo[0], 58)}</div>{/if}
      </button>
    {/each}
  </div>
{/if}

<style>
  .rail { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
  @media (max-width: 1100px) { .rail { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); } }
  .lcard { display: flex; flex-direction: column; gap: 5px; min-width: 0; padding: 8px 10px;
           border-radius: 11px; text-align: left; cursor: pointer;
           background: var(--panel); border: 1px solid var(--border-soft); }
  .lcard:hover { border-color: var(--border); }
  .lcard.here { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--panel)); }
  .lhead { display: flex; align-items: center; justify-content: space-between; gap: 6px; }
  .lname { font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .badge { min-width: 17px; height: 17px; padding: 0 4px; box-sizing: border-box; border-radius: 999px;
           display: grid; place-items: center; font-size: 10px; font-weight: 800;
           background: var(--bad, #c96a5a); color: #fff; }
  .badge.ok { background: color-mix(in srgb, var(--good, #5ec27a) 75%, transparent); }
  .lbody { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .line { font-size: 11px; color: var(--text); line-height: 1.45; overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; }
  .line.dim { color: var(--faint); font-style: italic; }
  .tag { color: var(--faint); margin-right: 4px; font-style: normal; font-size: 10px; }
  .todo { font-size: 10.5px; color: var(--bad, #c96a5a); overflow: hidden; text-overflow: ellipsis;
          white-space: nowrap; }
</style>

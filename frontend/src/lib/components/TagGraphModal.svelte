<script>
  import { get, post } from '$lib/api.js';
  import { pickerState, resolveGraphPicker } from '$lib/graphpicker.svelte.js';
  import TagSearch from '$lib/components/TagSearch.svelte';
  import FacetPalette from '$lib/components/FacetPalette.svelte';
  import TagGraphCanvas from '$lib/components/TagGraphCanvas.svelte';

  // Graph-driven prompt editor (orchestrator). The prompt is a list of chips; click one to TARGET it
  // for replacement. Two content tabs — Suggestions (a faceted list of compatible tags) and Graph
  // (the live similarity network for the targeted tag). Search adds/swaps from the full vocabulary;
  // the length slider sets per-category density + the AI-regenerate target. Apply returns the tags.
  const norm = (s) => (s || '').toString().trim().toLowerCase().replace(/\s+/g, ' ');
  const dedupe = (a) => { const seen = new Set(), out = []; for (const t of a) { const k = norm(t); if (t && !seen.has(k)) { seen.add(k); out.push(t); } } return out; };

  let tags = $state([]);
  let kind = $state('clothing');
  let target = $state(null);       // chip selected for replacement (or null = add mode)
  let view = $state('list');       // 'list' (Suggestions) | 'graph'
  let suggestions = $state({});    // { facet: [tag, ...] }
  let busy = $state(false);
  let length = $state(20);         // per-category display count + AI-regenerate target
  let similarity = $state(60);     // 0 = broad graph search · 100 = tight similarity
  let regening = $state(false);
  let seeded = false;

  // seed local state on the rising edge of `open`
  $effect(() => {
    if (pickerState.open && !seeded) {
      seeded = true;
      tags = pickerState.tags.slice().filter((t) => String(t).toUpperCase() !== 'BREAK');  // edit flat; regions re-derived on Apply
      kind = pickerState.kind;
      target = pickerState.target || null;
      view = 'list';
      length = Math.min(40, Math.max(8, tags.length || 18));
      refreshSuggestions();
    }
    if (!pickerState.open) seeded = false;
  });

  let sugTimer = null;
  function scheduleSuggestions() { clearTimeout(sugTimer); sugTimer = setTimeout(refreshSuggestions, 180); }
  async function refreshSuggestions() {
    const seed = tags.join(',');
    if (!seed) { suggestions = {}; return; }
    busy = true;
    // unified view: ALL 18 categories across clothing + appearance, uncapped (per=0); the length
    // slider caps per-category display client-side, similarity zooms relevance.
    try { const d = await get('/tags/related?kind=all&per=0&sim=' + similarity + '&tags=' + encodeURIComponent(seed)); suggestions = d?.palette || {}; }
    catch { suggestions = {}; }
    busy = false;
  }

  function applyTag(t) {
    t = (t || '').trim(); if (!t) return;
    if (target) { tags = dedupe(tags.map((x) => (norm(x) === norm(target) ? t : x))); target = null; }
    else { tags = dedupe([...tags, t]); }
    scheduleSuggestions();
  }
  function removeChip(t) { tags = tags.filter((x) => norm(x) !== norm(t)); if (target && norm(target) === norm(t)) target = null; scheduleSuggestions(); }
  function setTarget(t) { target = (t && target && norm(target) === norm(t)) ? null : t; }
  function setKind(k) { if (k !== kind) { kind = k; refreshSuggestions(); } }
  async function regen() {
    if (!tags.length || regening) return;
    regening = true;
    const r = await post('/tags/recompose', { tags, kind, length });
    if (r.ok && r.data?.tags?.length) { tags = r.data.tags; target = null; scheduleSuggestions(); }
    regening = false;
  }
  async function apply() {
    if (!tags.length) { resolveGraphPicker([]); return; }
    // assemble into category regions separated by literal BREAK (the going-forward prompt shape)
    try {
      const r = await post('/tags/regionize', { tags: dedupe(tags) });
      resolveGraphPicker(r.ok && r.data?.tags?.length ? r.data.tags : dedupe(tags));
    } catch { resolveGraphPicker(dedupe(tags)); }
  }
  const cancel = () => resolveGraphPicker(null);
</script>

{#if pickerState.open}
  <div class="overlay" onclick={cancel} role="presentation">
    <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <div class="head">
        <h3 class="title">{pickerState.title}</h3>
        <div class="seg">
          <button class:on={kind === 'clothing'} onclick={() => setKind('clothing')}>clothing</button>
          <button class:on={kind === 'appearance'} onclick={() => setKind('appearance')}>appearance</button>
        </div>
      </div>

      <div class="seclabel">Prompt <span class="lo">— click a tag to replace it, × to remove</span></div>
      <div class="chips">
        {#each tags as t (t)}
          <span class="chip" class:target={target && norm(target) === norm(t)}>
            <button class="lbl" onclick={() => setTarget(t)} title="select to replace">{t}</button>
            <button class="x" onclick={() => removeChip(t)} title="remove">×</button>
          </span>
        {/each}
        {#if !tags.length}<span class="empty">no tags yet — search or pick below</span>{/if}
      </div>

      <div class="toolbar">
        <div class="ctrls">
          <label class="slbl">length <b>{length}</b></label>
          <input class="slider" type="range" min="6" max="100" value={length}
            oninput={(e) => (length = +e.target.value)}
            title="suggestions shown per category, and the target size for AI regenerate" />
          <label class="slbl">similarity <b>{similarity}</b></label>
          <input class="slider" type="range" min="0" max="100" value={similarity}
            oninput={(e) => { similarity = +e.target.value; scheduleSuggestions(); }}
            title="low = broad graph search (many, looser) · high = tight similarity (fewer, closer)" />
        </div>
        <button class="aibtn" onclick={regen} disabled={regening || !tags.length}
          title="regenerate the prompt with AI at the chosen length, drawing on the graph">
          {regening ? 'Regenerating…' : '✨ Regenerate with AI'}</button>
      </div>

      <div class="tabs">
        <div class="seg">
          <button class:on={view === 'list'} onclick={() => (view = 'list')}>Suggestions</button>
          <button class:on={view === 'graph'} onclick={() => (view = 'graph')}>Graph</button>
        </div>
        {#if target}
          <span class="repl">Replacing <b>{target}</b> <button class="link" onclick={() => setTarget(null)}>· back</button></span>
        {/if}
      </div>

      <div class="content">
        <TagSearch onpick={applyTag} />
        <div class="cbody">
          {#if view === 'graph'}
            {#if target}
              <TagGraphCanvas seed={target} {kind} sim={similarity} onpick={applyTag} />
            {:else}
              <div class="hintbox">Click a tag in the prompt above to explore its graph.</div>
            {/if}
          {:else}
            <FacetPalette palette={suggestions} per={length} swapLabel={target} {busy} onpick={applyTag} />
          {/if}
        </div>
      </div>

      <div class="acts">
        <span class="lo">{tags.length} tag{tags.length === 1 ? '' : 's'}</span>
        <button class="ghost" onclick={cancel}>Cancel</button>
        <button onclick={apply}>Apply</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (pickerState.open && e.key === 'Escape') cancel(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 90; background: rgba(6,8,12,.62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(95vw, 880px); height: min(92vh, 880px); display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg, 14px);
    box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55)); padding: 16px 18px; animation: pop .13s ease; }
  .head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .title { margin: 0; font-size: 16px; font-weight: 680; color: var(--text); flex: 1; }
  /* segmented control — shared by kind toggle + content tabs */
  .seg { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; flex: none; }
  .seg button { background: var(--elev); border: 0; color: var(--muted); padding: 5px 13px; font-size: 12px; box-shadow: none; cursor: pointer; }
  .seg button.on { background: var(--accent); color: #fff; }
  .seclabel { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); margin: 0 2px 5px; }
  .lo { color: var(--faint); font-size: 11.5px; text-transform: none; letter-spacing: 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 9px; border: 1px solid var(--border);
    border-radius: 10px; background: var(--bg); min-height: 44px; max-height: 18vh; overflow: auto; }
  .chip { display: inline-flex; align-items: center; border: 1px solid var(--border-soft); background: var(--elev-2); border-radius: 8px; overflow: hidden; }
  .chip.target { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .chip .lbl { background: none; border: 0; color: var(--text); font-size: 12.5px; padding: 3px 4px 3px 9px; box-shadow: none; cursor: pointer; }
  .chip .x { background: none; border: 0; color: var(--muted); font-size: 15px; padding: 0 7px 0 3px; box-shadow: none; cursor: pointer; }
  .chip .x:hover { color: #ff8a8a; }
  .empty { color: var(--faint); font-size: 12.5px; align-self: center; padding: 2px 4px; }
  .toolbar { display: flex; align-items: center; gap: 14px; margin-top: 12px; }
  .ctrls { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 6px 10px; flex: 1; max-width: 460px; }
  .slbl { font-size: 12px; color: var(--muted); white-space: nowrap; }
  .slbl b { color: var(--text); font-variant-numeric: tabular-nums; }
  .slider { width: 100%; accent-color: var(--accent); }
  .aibtn { margin-left: auto; font-size: 12.5px; padding: 6px 12px; border-radius: 8px; white-space: nowrap;
    background: rgba(109,140,255,.16); border: 1px solid rgba(109,140,255,.4); color: #cdd8ff; box-shadow: none; }
  .aibtn:hover:not(:disabled) { background: rgba(109,140,255,.28); filter: none; }
  .aibtn:disabled { opacity: .55; }
  .tabs { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
  .repl { font-size: 12px; color: var(--muted); }
  .repl b { color: var(--text); }
  .link { background: none; border: 0; color: var(--accent); box-shadow: none; cursor: pointer; font-size: 12px; padding: 0 2px; }
  .content { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; margin: 10px 0; }
  .cbody { flex: 1; min-height: 0; display: flex; }
  .hintbox { flex: 1; display: grid; place-items: center; color: var(--muted); font-size: 12.5px;
    border: 1px dashed var(--border); border-radius: 10px; }
  .acts { display: flex; align-items: center; gap: 10px; justify-content: flex-end; padding-top: 10px; border-top: 1px solid var(--border-soft); }
  .acts .lo { margin-right: auto; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

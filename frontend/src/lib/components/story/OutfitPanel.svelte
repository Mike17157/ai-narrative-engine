<script>
  // Outfits for ONE character: a series of full-body images generated from the character's
  // appearance + an outfit description (reuses the portrait-studio endpoints). Same idea as
  // fleshing out a character — describe, then render the look.
  import { get, post } from '$lib/api.js';

  let { charKey = '', charName = '' } = $props();

  let payload = $state(null);     // { appearance, outfits: [{id, name, ...}] }
  let busy = $state(false);
  let err = $state(null);
  let oName = $state('');
  let oInstr = $state('');
  let loaded = '';

  $effect(() => {
    if (!charKey || loaded === charKey) return;
    loaded = charKey;
    payload = null; err = null; oName = ''; oInstr = '';
    load();
  });

  async function load() {
    const r = await get(`/characters/${charKey}/portraits`);
    payload = r || { appearance: '', outfits: [] };
  }
  function imgUrl(oid) { return `/api/characters/${charKey}/portraits/img/${oid}/base.png`; }

  async function describe() {
    busy = true; err = null;
    const r = await post(`/characters/${charKey}/portraits/describe`, {});
    busy = false;
    if (!r.data || r.data.error) { err = r.data?.error || 'describe failed'; return; }
    payload = r.data;
  }

  async function addOutfit() {
    if (!oName.trim()) { err = 'Name the outfit.'; return; }
    busy = true; err = null;
    const r = await post(`/characters/${charKey}/portraits/outfit`, { name: oName, instruction: oInstr });
    busy = false;
    if (!r.data || r.data.error) { err = r.data?.error || 'render failed'; return; }
    payload = r.data; oName = ''; oInstr = '';
  }
</script>

<div class="op">
  {#if !payload}
    <div class="muted">Loading {charName}…</div>
  {:else if !payload.appearance}
    <div class="describe">
      <p>No appearance yet for <b>{charName}</b>.</p>
      <button onclick={describe} disabled={busy}>{busy ? 'Describing…' : 'Describe appearance'}</button>
    </div>
  {:else}
    <div class="add">
      <input bind:value={oName} placeholder="Outfit name — e.g. 'beach casual'" />
      <input bind:value={oInstr} placeholder="Outfit description (optional)" />
      <button onclick={addOutfit} disabled={busy}>{busy ? 'Rendering…' : '+ Outfit'}</button>
    </div>
    {#if err}<div class="err">{err}</div>{/if}
    <div class="grid">
      {#each payload.outfits || [] as o (o.id)}
        <div class="outfit">
          <img src={imgUrl(o.id)} alt={o.name} onerror={(e) => (e.target.style.opacity = '.2')} />
          <span class="oname">{o.name}</span>
        </div>
      {/each}
      {#if !(payload.outfits || []).length}<div class="muted">No outfits yet — add one above.</div>{/if}
    </div>
  {/if}
</div>

<style>
  .op { display: flex; flex-direction: column; gap: 12px; }
  .muted { font-size: 13px; }
  .describe { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; color: var(--muted); font-size: 13px; }
  .describe button, .add button { padding: 8px 14px; border-radius: 9px; background: var(--accent); color: #fff; border: 0; cursor: pointer; font-size: 13px; white-space: nowrap; }
  .describe button:disabled, .add button:disabled { opacity: .45; cursor: default; }
  .add { display: flex; gap: 8px; flex-wrap: wrap; }
  .add input { flex: 1; min-width: 160px; padding: 8px 11px; font-size: 13px; }
  .add input:focus { outline: none; border-color: var(--accent); }
  .err { font-size: 12.5px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
  .outfit { display: flex; flex-direction: column; gap: 5px; }
  .outfit img { width: 100%; aspect-ratio: 2/3; object-fit: cover; border-radius: 10px; background: var(--elev-2); border: 1px solid var(--border-soft); }
  .oname { font-size: 12px; color: var(--muted); text-align: center; }
</style>

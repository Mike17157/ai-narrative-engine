<script>
  import { goto } from '$app/navigation';
  import { post } from '$lib/api.js';
  import { startJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import GenStream from '$lib/components/GenStream.svelte';
  import CastHeightLineup from '$lib/components/CastHeightLineup.svelte';

  // cast: [{ character, name, role, hasRef, images:[url] }] · onChanged() reloads story+chars.
  let { storyKey, cast = [], onChanged = () => {} } = $props();
  const CANDIDATES = 3;
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);
  const baseKey = (k) => `dashbase:${k}`;

  // ---- whole-cast jobs (one GenStream at a time) ----------------------------
  let bulkJob = $state(null);     // backend job id (regenerate cast | plan all)
  let bulkTitle = $state('');
  let bulkErr = $state(null);

  async function regenCast() {
    if (!await askConfirm({
      title: 'Regenerate the whole cast?',
      message: 'Deletes every story-bound character and their portraits, then re-derives the cast (protagonist + supporting NPCs) from the storyboard. The imported source card is kept. This cannot be undone.',
      confirmLabel: 'Regenerate cast', danger: true })) return;
    await startBulk('Regenerating cast', `/stories/${storyKey}/regenerate-cast`, {});
  }
  async function planAll() {
    await startBulk('Planning all wardrobes', `/stories/${storyKey}/plan-wardrobe-all`, {});
  }
  let bulkFailed = false;
  async function startBulk(title, url, body) {
    bulkErr = null; bulkJob = null; bulkTitle = title; bulkFailed = false;
    const r = await post(url, body);
    if (r.ok && r.data?.job) bulkJob = r.data.job;
    else { bulkErr = r.data?.error || 'could not start (is the backend restarted?)'; bulkTitle = ''; }
  }
  async function onBulkDone() { bulkJob = null; bulkTitle = ''; if (!bulkFailed) await onChanged(); }

  // ---- bulk base-image candidates (review — never auto-saved) ----------------
  let genningAll = $state(false);
  async function genBase(charKey) {
    const k = baseKey(charKey); rensure(k).cands = []; rensure(k).busy = true; rensure(k).err = null;
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    const job = startJob('Base image render', nm, `stories/${storyKey}/cast`, CANDIDATES);
    for (let i = 0; i < CANDIDATES; i++) {
      const r = await post(`/characters/${charKey}/base-candidate`, {});
      if (r.ok && r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { rensure(k).err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    rensure(k).busy = false;
  }
  async function genBaseAll() {
    genningAll = true;
    for (const c of cast) await genBase(c.character);   // sequential — one GPU
    genningAll = false;
  }
  async function pickBase(charKey, dataUri) {
    const k = baseKey(charKey); rensure(k).busy = true;
    const r = await post(`/characters/${charKey}/reference/from-data`, { data: dataUri });
    rensure(k).busy = false;
    if (r.data?.ok) { rensure(k).cands = []; await onChanged(); }
    else rensure(k).err = r.data?.error || 'could not set base';
  }

  // ---- per-character full regenerate (persona+role+appearance, base image, wardrobe) --------
  let openRegen = $state('');        // which character's regen box is open
  let regenText = $state('');        // the change instruction
  let regenJob = $state(null);       // GenStream job id for the running per-char regen
  let regenFor = $state('');         // which character that job belongs to
  let regenErr = $state(null);

  function toggleRegen(charKey) { openRegen = openRegen === charKey ? '' : charKey; regenText = ''; regenErr = null; }
  async function runRegen(charKey) {
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    if (!await askConfirm({
      title: `Regenerate ${nm}?`,
      message: 'Rewrites this character’s description, role and appearance, re-renders their base image, and rebuilds their wardrobe (clearing the current outfits + sprites). This cannot be undone.',
      confirmLabel: 'Regenerate', danger: true })) return;
    regenErr = null; regenJob = null; regenFor = charKey; regenFailed = false;
    const r = await post(`/stories/${storyKey}/regenerate-character`, { character: charKey, instruction: regenText.trim() });
    if (r.ok && r.data?.job) { regenJob = r.data.job; openRegen = ''; }
    else { regenErr = r.data?.error || 'could not start (is the backend restarted?)'; regenFor = ''; }
  }
  // Keep the error visible after the stream closes; only reload on success.
  let regenFailed = false;
  function onRegenError(charKey, msg) { regenErr = `${cast.find((c) => c.character === charKey)?.name || charKey}: ${msg}`; regenFailed = true; }
  async function onRegenDone() { regenJob = null; regenFor = ''; if (!regenFailed) await onChanged(); }
</script>

<div class="dash">
  <div class="dhead">
    <h4>Cast &amp; wardrobe <span class="lo">— bulk tools for the whole story; open a character to fine-tune</span></h4>
    <div class="bulk">
      <button class="ghost sm dgr" onclick={regenCast} disabled={!!bulkJob}>↻ Regenerate whole cast</button>
      <button class="ghost sm" onclick={genBaseAll} disabled={genningAll || !!bulkJob}>{genningAll ? 'Generating…' : '🎨 Base images for all'}</button>
      <button class="ghost sm" onclick={planAll} disabled={!!bulkJob}>✨ Plan outfits for all</button>
    </div>
  </div>
  {#if bulkErr}<div class="err">⚠ {bulkErr}</div>{/if}
  {#if bulkJob}<GenStream jobId={bulkJob} title={bulkTitle} onError={(m) => { bulkErr = m; bulkFailed = true; }} onDone={onBulkDone} />{/if}
  {#if regenErr}<div class="err">⚠ {regenErr}</div>{/if}

  <CastHeightLineup {cast} />

  <div class="grid">
    {#each cast as c (c.character)}
      <div class="card" class:primary={c.primary}>
        <button class="thumb" onclick={() => goto(`/stories/${storyKey}/cast?c=${c.character}`)} title="Open {c.name}">
          {#if c.hasRef}
            <img src={abs(`/api/characters/${c.character}/reference`)} alt={c.name} />
          {:else}
            <div class="noimg">no base image</div>
          {/if}
        </button>
        <div class="meta">
          <div class="nm">{c.name}{#if c.primary}<span class="lead">★</span>{/if}</div>
          {#if c.role}<div class="role">{c.role}</div>{/if}
          <div class="cardacts">
            <button class="ghost xs" onclick={() => goto(`/stories/${storyKey}/cast?c=${c.character}`)}>Open</button>
            <button class="ghost xs" onclick={() => toggleRegen(c.character)}>↻ Regenerate</button>
          </div>
        </div>

        {#if openRegen === c.character}
          <div class="regenbox">
            <textarea class="fld" rows="2" bind:value={regenText}
              placeholder="what to change? (optional — e.g. 'make her older, a rival not a friend'; blank = faithful refresh)"></textarea>
            <button class="go" onclick={() => runRegen(c.character)}>Regenerate {c.name}</button>
          </div>
        {/if}
        {#if regenFor === c.character && regenJob}
          <GenStream jobId={regenJob} title="Regenerating {c.name}" onError={(m) => onRegenError(c.character, m)} onDone={onRegenDone} />
        {/if}

        {#if rget(baseKey(c.character)).err}<div class="err sm">⚠ {rget(baseKey(c.character)).err}</div>{/if}
        {#if rget(baseKey(c.character)).cands?.length}
          <div class="cands">
            {#each rget(baseKey(c.character)).cands as img, i (i)}
              <div class="cand">
                <ZoomImage src={img} caption={`${c.name} candidate ${i + 1}`} inline />
                <button class="use" onclick={() => pickBase(c.character, img)} disabled={rget(baseKey(c.character)).busy}>Use this</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .dash { display: flex; flex-direction: column; gap: 12px; }
  .dhead { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  h4 { margin: 4px 0; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .bulk { display: flex; gap: 6px; flex-wrap: wrap; }
  .dgr { color: var(--bad); border-color: rgba(255,122,122,.4); }
  .dgr:hover:not(:disabled) { background: rgba(255,122,122,.12); color: var(--bad); filter: none; }
  .err { font-size: 12.5px; color: var(--bad); } .err.sm { margin-top: 6px; }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
  .card { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
  .card.primary { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .thumb { padding: 0; border: 1px solid var(--border); border-radius: 9px; overflow: hidden; background: var(--bg); aspect-ratio: 3/4; box-shadow: none; cursor: pointer; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb:hover { border-color: var(--accent); filter: none; }
  .noimg { width: 100%; height: 100%; display: grid; place-items: center; font-size: 11.5px; color: var(--faint); }
  .meta { display: flex; flex-direction: column; gap: 3px; }
  .nm { font-size: 14px; font-weight: 650; display: flex; align-items: center; gap: 6px; }
  .lead { color: var(--accent); font-size: 11px; }
  .role { font-size: 11.5px; color: var(--muted); }
  .cardacts { display: flex; gap: 6px; margin-top: 4px; }
  .xs { font-size: 11.5px; padding: 3px 9px; border-radius: 7px; }

  .regenbox { display: flex; flex-direction: column; gap: 6px; }
  .fld { width: 100%; padding: 7px 9px; font-size: 12.5px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); resize: vertical; line-height: 1.45; font-family: inherit; }
  .fld:focus { border-color: var(--accent); outline: none; }
  .go { font-size: 12px; font-weight: 600; padding: 6px 10px; border-radius: 8px; background: var(--accent); color: #fff; border: 0; }
  .go:hover { filter: brightness(1.08); }

  .cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(80px, 1fr)); gap: 6px; }
  .cand { display: flex; flex-direction: column; gap: 4px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 7px; }
  .use { font-size: 10.5px; padding: 3px 5px; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); }
  .use:hover { border-color: var(--accent); color: var(--accent); filter: none; }
</style>

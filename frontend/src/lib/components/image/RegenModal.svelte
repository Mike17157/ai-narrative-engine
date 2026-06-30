<script>
  import { get, post } from '$lib/api.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import ZoomImage from '$lib/components/shared/ZoomImage.svelte';
  import GenStream from '$lib/components/shared/GenStream.svelte';
  import Sprites from '$lib/components/image/Sprites.svelte';
  import Modal from '$lib/components/shared/Modal.svelte';

  // Gated AI-regen cascade for ONE character, over the dependency hierarchy
  //   description → base image → outfit images → expressions.
  // The user picks an ENTRY stage; the modal runs it and every stage below, pausing at each image
  // stage so candidates can be reviewed before continuing. The text stage uses regenerate-character
  // `text_only` (no renders); image stages reuse the existing candidate endpoints.
  let { storyKey, character, name = '', hasRef = false, onClose = () => {}, onChanged = () => {} } = $props();
  const CANDIDATES = 3;
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);

  // The three actual screens. Entry options map onto these (outfit images + expressions share the
  // studio screen, where each outfit renders its image then its expression range).
  const SCREENS = ['Description', 'Base image', 'Outfits & expressions'];
  const ENTRIES = [
    { label: 'Description & name', step: 0, hint: 'rewrite persona, role & appearance — then re-derive the base, outfit and expression prompts' },
    { label: 'Base image', step: 1, hint: 'render base-image candidates from the current prompt and pick one' },
    { label: 'Outfit images', step: 2, hint: 'render the full-body image for each outfit' },
    { label: 'Expressions', step: 2, hint: 'render each outfit’s expression range' },
  ];

  let step = $state(-1);          // -1 = choosing an entry stage
  let instruction = $state('');
  let changed = false;            // anything applied → reload the pane on close

  function begin(startStep) { step = startStep; if (startStep === 0) startDesc(); else if (startStep === 1) loadBasePrompt(); }
  function goto(i) { step = i; if (i === 1) loadBasePrompt(); }
  function finish() { onChanged(); onClose(); }
  function close() { if (changed) onChanged(); onClose(); }

  // -- stage 1: description (text only, streamed; recomposes the downstream prompts) -------------
  let descJob = $state(null), descDone = $state(false), descErr = $state(null);
  async function startDesc() {
    descJob = null; descDone = false; descErr = null;
    const r = await post(`/stories/${storyKey}/regenerate-character`,
      { character, instruction: instruction.trim(), text_only: true });
    if (r.ok && r.data?.job) descJob = r.data.job;
    else descErr = r.data?.error || 'could not start (is the backend restarted?)';
  }

  // -- standalone: re-author just the personality-rooted emotion range (Tier-A, 1 cheap call) --
  // Distinct from the Description cascade — use when you only want to re-curate which emotions this
  // character expresses without rewriting the persona. Drives the carousel's X axis; the director
  // picks keys from this list directly at runtime.
  let affectBusy = $state(false), affectErr = $state(null), affectDone = $state(false);
  async function composeAffect() {
    affectBusy = true; affectErr = null; affectDone = false;
    const r = await post(`/characters/${character}/portraits/affect`, { compose: true });
    affectBusy = false;
    if (r.ok && r.data?.affect?.range) {
      affectDone = true; changed = true;
    } else {
      affectErr = r.data?.error || 'could not compose emotion range';
    }
  }

  // -- stage 2: base image (gate) ----------------------------------------------------------------
  const baseKey = `regenbase:${character}`;
  let basePrompt = $state(null), baseBusy = $state(false), baseErr = $state(null), basePicked = $state(false);
  async function loadBasePrompt() { const d = await get(`/characters/${character}/base-prompt`); basePrompt = d?.prompt ?? ''; }
  async function genBase() {
    baseBusy = true; baseErr = null; rensure(baseKey).cands = [];
    const job = startJob('Base image render', name, `stories/${storyKey}/cast`, CANDIDATES);
    for (let i = 0; i < CANDIDATES; i++) {
      const r = await limitedPost(`/characters/${character}/base-candidate`, {}, {}, job);
      if (r.ok && r.data?.image) { rensure(baseKey).cands = [...rensure(baseKey).cands, r.data.image]; job.done = i + 1; }
      else { baseErr = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    baseBusy = false;
  }
  async function pickBase(dataUri) {
    baseBusy = true; baseErr = null;
    const r = await post(`/characters/${character}/reference/from-data`, { data: dataUri });
    baseBusy = false;
    if (r.data?.ok) { basePicked = true; changed = true; rensure(baseKey).cands = []; }
    else baseErr = r.data?.error || 'could not set base';
  }
</script>

<Modal onClose={close} flush width="920px" maxHeight="90vh">
    <div class="dhead">
      <h3 class="title">Regenerate {name}</h3>
      <button class="x" onclick={close} aria-label="Close">✕</button>
    </div>

    {#if step >= 0}
      <div class="crumbs">
        {#each SCREENS as s, i (s)}
          <span class="crumb" class:on={i === step} class:done={i < step}>{i + 1}. {s}</span>
          {#if i < SCREENS.length - 1}<span class="sep">→</span>{/if}
        {/each}
      </div>
    {/if}

    <div class="body">
      {#if step === -1}
        <!-- entry chooser -->
        <p class="intro">Pick where to start. The cascade runs that stage and every stage below it,
          following <b>description → base image → outfit images → expressions</b>, pausing at each
          image stage so you can review and pick.</p>
        <label class="fl">Optional steer <span class="lo">— used when starting from the description</span></label>
        <textarea class="fld" rows="2" bind:value={instruction}
          placeholder="what to change? e.g. 'make her older, a rival not a friend' — blank = faithful refresh"></textarea>
        <div class="entries">
          {#each ENTRIES as e (e.label)}
            <button class="entry" onclick={() => begin(e.step)}>
              <span class="el">{e.label}</span><span class="eh">{e.hint}</span>
            </button>
          {/each}
        </div>

        <!-- standalone emotion-range re-author (Tier-A; doesn't enter the image cascade) -->
        <div class="quickact">
          <div class="qa-info">
            <span class="el">Emotion range</span>
            <span class="eh">re-curate which emotions this character can express — drives the carousel X axis and director key selection (1 cheap call)</span>
          </div>
          <button class="ghost sm" onclick={composeAffect} disabled={affectBusy}>
            {affectBusy ? 'Composing…' : (affectDone ? '↻ Re-compose' : '✨ Compose range')}
          </button>
        </div>
        {#if affectErr}<div class="err">⚠ {affectErr}</div>{/if}
        {#if affectDone}<div class="ok">✓ emotion range updated — close to see it in the carousel</div>{/if}

      {:else if step === 0}
        <p class="intro">Rewriting the description, role and appearance, then re-deriving the base-image,
          outfit and expression <b>prompts</b>. No images are rendered yet.</p>
        {#if descErr}<div class="err">⚠ {descErr}</div>{/if}
        {#if descJob}
          <GenStream jobId={descJob} title="Rewriting {name}"
            onError={(m) => (descErr = m)} onDone={() => { descDone = true; changed = true; }} />
        {/if}
        <div class="nav">
          <span class="sp"></span>
          <button class="go primary" onclick={() => goto(1)} disabled={!descDone}>Continue → Base image</button>
        </div>

      {:else if step === 1}
        <p class="intro">Render base-image candidates from the prompt and pick one. The chosen image
          becomes this character’s reference (used as the look anchor everywhere).</p>
        {#if basePrompt !== null}
          <details class="cin"><summary>Image prompt sent to the model</summary><pre class="pre">{basePrompt || '—'}</pre></details>
        {/if}
        <div class="genrow">
          <button class="ghost sm" onclick={genBase} disabled={baseBusy}>
            {baseBusy ? 'Rendering…' : (rget(baseKey).cands.length ? '↻ More candidates' : `🎨 Generate ${CANDIDATES} candidates`)}</button>
          {#if basePicked}<span class="ok">✓ base set</span>{/if}
        </div>
        {#if baseErr}<div class="err">⚠ {baseErr}</div>{/if}
        {#if rget(baseKey).cands.length}
          <div class="cands">
            {#each rget(baseKey).cands as img, i (i)}
              <div class="cand">
                <ZoomImage src={img} caption={`${name} candidate ${i + 1}`} inline />
                <button class="use" onclick={() => pickBase(img)} disabled={baseBusy}>Use this</button>
              </div>
            {/each}
          </div>
        {/if}
        <div class="nav">
          <button class="ghost sm" onclick={() => goto(2)}>Skip</button>
          <button class="go primary" onclick={() => goto(2)} disabled={baseBusy}>Continue → Outfits & expressions</button>
        </div>

      {:else if step === 2}
        <p class="intro">Render the full-body image for each outfit, then its expression range. Outfits
          are rows; expressions scroll across. Pick candidates as they render.</p>
        <Sprites charKey={character} charName={name} {hasRef} mode="generate"
          screen={`stories/${storyKey}/cast`} />
        <div class="nav">
          <span class="sp"></span>
          <button class="go primary" onclick={finish}>Done</button>
        </div>
      {/if}
    </div>
</Modal>

<style>
  .dhead { display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 16px 18px 10px; border-bottom: 1px solid var(--border-soft); }
  .title { margin: 0; font-size: 16px; font-weight: 680; color: var(--text); }
  .x { width: 30px; height: 30px; flex: none; padding: 0; border-radius: 8px;
    background: var(--elev); border: 1px solid var(--border); color: var(--muted); font-size: 12px; }
  .x:hover { color: var(--text); background: var(--elev-2); }
  .crumbs { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 10px 18px 0; }
  .crumb { font-size: 11.5px; font-weight: 600; color: var(--faint); padding: 3px 9px; border-radius: 999px;
    background: var(--elev); border: 1px solid var(--border-soft); }
  .crumb.on { color: #fff; background: rgba(109,140,255,.18); border-color: var(--accent); }
  .crumb.done { color: var(--good, #8fc7a0); }
  .sep { color: var(--faint); font-size: 11px; }
  .body { padding: 14px 18px 18px; overflow: auto; }
  .intro { font-size: 12.5px; color: var(--muted); line-height: 1.55; margin: 0 0 12px; }
  .fl { display: block; font-size: 11px; color: var(--muted); margin: 4px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg);
    border: 1px solid var(--border); color: var(--text); resize: vertical; line-height: 1.5; font-family: inherit; }
  .fld:focus { border-color: var(--accent); outline: none; }
  .entries { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
  /* standalone quick action (emotion-range re-author) — sits below the cascade entries */
  .quickact { display: flex; align-items: center; justify-content: space-between; gap: 12px;
    margin-top: 12px; padding: 11px 13px; border-radius: 10px; background: var(--bg);
    border: 1px dashed var(--border-soft); }
  .qa-info { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .entry { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; text-align: left;
    padding: 11px 13px; border-radius: 10px; background: var(--bg); border: 1px solid var(--border); }
  .entry:hover { border-color: var(--accent); background: var(--elev); }
  .el { font-size: 13px; font-weight: 650; color: var(--text); }
  .eh { font-size: 11px; color: var(--faint); line-height: 1.4; }
  .err { font-size: 12.5px; margin: 6px 0; }
  .ok { font-size: 12px; }
  .genrow { display: flex; align-items: center; gap: 12px; margin: 4px 0 8px; }
  .cin { margin: 0 0 10px; } .cin summary { font-size: 12px; color: var(--muted); cursor: pointer; }
  .pre { white-space: pre-wrap; word-break: break-word; font-size: 11.5px; color: var(--text); background: var(--bg);
    border: 1px solid var(--border-soft); border-radius: 8px; padding: 9px; margin: 8px 0 0; }
  .cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; margin: 6px 0; }
  .cand { display: flex; flex-direction: column; gap: 5px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 8px; }
  .use { font-size: 11px; padding: 4px 6px; border-radius: 6px; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); }
  .use:hover { border-color: var(--accent); color: var(--accent); }
  .nav { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 16px;
    padding-top: 12px; border-top: 1px solid var(--border-soft); }
  .nav .sp { flex: 1; }
  .go { font-size: 13px; font-weight: 600; padding: 8px 16px; border-radius: 9px; }
  .go:hover:not(:disabled) { filter: brightness(1.08); }
  .go:disabled { opacity: .45; }
</style>

<script>
  import { get, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';
  import { startJob } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';
  import { loadChars } from '$lib/characters.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import Sprites from '$lib/components/Sprites.svelte';
  import TagInput from '$lib/components/TagInput.svelte';
  import GenStream from '$lib/components/GenStream.svelte';

  // Base-image candidates live in the shared store (per character) so they survive
  // navigating away from the cast page and back.
  const baseKey = (k) => `base:${k}`;

  // cast: [{ character, name, hasRef, images:[url] }] · storyKey, primaryKey (style
  // anchor char), primaryImages (base-card image URLs to use as style sources).
  let { storyKey, primaryKey = '', primaryImages = [], cast = [] } = $props();
  const CANDIDATES = 3;
  const abs = (u) => (u && u.startsWith('/api') ? location.origin + u : u);

  // per-character state. NOTE: never create entries during render (Svelte 5
  // forbids mutating $state in templates) — `cs()`/`hasRef()` only READ; the
  // entry is created lazily by `ensure()`, which is called from event handlers.
  const EMPTY = { busy: false, error: null, plan: null, msg: null, saving: false,
                  baseBusy: false, candidates: [], picked: false, baseErr: null, spriteRefresh: 0,
                  prompt: null, promptLoaded: false, styleUrl: '', planJob: null, planMode: null,
                  name: '', desc: '', nameTouched: false, descTouched: false, tab: 'desc', refBust: 0 };
  // Per-character view splits into these sub-sections (in-page tabs).
  const TABS = [{ id: 'desc', label: 'Description' }, { id: 'base', label: 'Base image' },
                { id: 'outfits', label: 'Outfit images' }, { id: 'emotions', label: 'Emotions' }];
  let state = $state({});
  function ensure(key) {
    if (!state[key]) state[key] = { ...EMPTY, candidates: [] };
    return state[key];
  }
  function cs(key) { return state[key] ?? EMPTY; }                     // read-only (template)
  function hasRef(c) { return c.hasRef || (state[c.character]?.picked ?? false); }
  function styleUrlOf(c) { return state[c.character]?.styleUrl || ''; }   // '' = none (plain txt2img)
  function setStyle(charKey, url) { ensure(charKey).styleUrl = url; }

  // Fetch the EXACT image prompt the backend will send (appearance + framing) and seed the
  // editable name + description from the card — so every character shows both its persona and
  // its image prompt. Lazy + once, from an effect (never mutate $state during render).
  $effect(() => {
    for (const c of cast) {
      const s = ensure(c.character);
      // Mirror name + description from the card until the user edits them. (Seeding must be
      // reactive: chars.list often loads AFTER first render, so a one-shot seed would stick
      // on the empty initial value — leaving every description blank.)
      if (!s.nameTouched && s.name !== (c.name || '')) s.name = c.name || '';
      if (!s.descTouched && s.desc !== (c.desc || '')) s.desc = c.desc || '';
      if (s.promptLoaded) continue;              // image prompt — for ALL characters
      s.promptLoaded = true;
      get(`/characters/${c.character}/base-prompt`).then((d) => {
        if (d?.prompt && state[c.character] && state[c.character].prompt == null)
          state[c.character].prompt = d.prompt;
      });
    }
  });

  // Generate base-image candidates from the (editable) prompt. A style source is
  // OPT-IN: with none selected this is plain txt2img driven purely by the prompt —
  // so a male character isn't dragged female by a female style image's identity leak.
  async function genBase(charKey) {
    const s = ensure(charKey); s.baseBusy = true; s.baseErr = null;
    const k = baseKey(charKey); rensure(k).cands = [];
    const payload = { prompt: (s.prompt || '').trim() };
    if (s.styleUrl) payload.style_url = abs(s.styleUrl);
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    const job = startJob('Base image render', nm, storyKey ? `stories/${storyKey}/cast` : 'characters/search', CANDIDATES);
    const tok = { cancelled: false };
    job.onCancel = () => { tok.cancelled = true; s.baseBusy = false; };   // stop after the current render
    for (let i = 0; i < CANDIDATES; i++) {
      if (tok.cancelled) break;
      const r = await post(`/characters/${charKey}/base-candidate`, payload);
      if (tok.cancelled) break;
      if (r.ok && r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { s.baseErr = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = tok.cancelled ? 'cancelled' : 'done';
    s.baseBusy = false;
  }
  async function resetPrompt(charKey) {
    const s = ensure(charKey);
    const d = await get(`/characters/${charKey}/base-prompt`);
    if (d) s.prompt = d.default || d.prompt;   // reset = appearance-derived default
  }
  // Regenerate a deterministic, fully-slotted base prompt from the character's FULL
  // persona (structured feature schema, assembled server-side). Auto-saves.
  async function regenBase(charKey) {
    const s = ensure(charKey); const k = `${charKey}:gen`;
    if (tagging[k]) return;
    tagging[k] = true; s.baseErr = null;
    const r = await post(`/characters/${charKey}/base-prompt/generate`, {});
    tagging[k] = false;
    if (r.ok && r.data?.prompt) s.prompt = r.data.prompt;
    else s.baseErr = r.data?.error || 'generate failed';
  }

  // ---- auto-save (debounced, quiet) -----------------------------------------
  // Non-reactive bookkeeping: a snapshot per (char,field) to detect real changes,
  // and a debounce timer per key. The $effect below watches prompt + plan.
  const SAVE_DELAY = 700;
  const timers = {}, snaps = {};
  const debounce = (k, fn) => { clearTimeout(timers[k]); timers[k] = setTimeout(fn, SAVE_DELAY); };

  async function saveBasePrompt(charKey) {
    const s = cs(charKey);
    await post(`/characters/${charKey}/card`, { fields: { base_prompt: (s.prompt || '').trim() } });
  }
  async function saveName(charKey) { await post(`/characters/${charKey}/card`, { name: (cs(charKey).name || '').trim() }); }
  async function saveDesc(charKey) { await post(`/characters/${charKey}/card`, { system: cs(charKey).desc || '' }); }
  async function doSaveWardrobe(charKey, reloadSprites = false) {
    const s = state[charKey]; if (!s?.plan) return;
    // Emotions are the fixed canonical taxonomy (composed server-side) — we save outfits only.
    s.saving = true;
    const r = await post(`/characters/${charKey}/portraits/wardrobe`, { outfits: s.plan.outfits });
    s.saving = false;
    s.msg = r.data?.ok
      ? { ok: true, text: `✓ saved — ${r.data.outfits.length} outfit(s)` }
      : { text: r.data?.error || 'save failed' };
    if (r.data?.ok && reloadSprites) s.spriteRefresh = (s.spriteRefresh || 0) + 1;
  }

  $effect(() => {
    for (const c of cast) {
      const s = state[c.character];
      if (!s) continue;
      if (s.prompt != null) {                          // base prompt: prime silently, save on edit
        const k = `${c.character}:prompt`;
        if (snaps[k] === undefined) snaps[k] = s.prompt;
        else if (snaps[k] !== s.prompt) { snaps[k] = s.prompt; debounce(k, () => saveBasePrompt(c.character)); }
      }
      {                                                // name: track value; save only on user edit
        const k = `${c.character}:name`;
        if (snaps[k] === undefined) snaps[k] = s.name;
        else if (snaps[k] !== s.name) { if (s.nameTouched) debounce(k, () => saveName(c.character)); snaps[k] = s.name; }
      }
      {                                                // description (persona): same
        const k = `${c.character}:desc`;
        if (snaps[k] === undefined) snaps[k] = s.desc;
        else if (snaps[k] !== s.desc) { if (s.descTouched) debounce(k, () => saveDesc(c.character)); snaps[k] = s.desc; }
      }
      if (s.plan) {                                    // wardrobe: save on first appearance AND on edit
        const k = `${c.character}:plan`;
        const snap = JSON.stringify({ o: s.plan.outfits });
        if (snaps[k] !== snap) { snaps[k] = snap; debounce(k, () => doSaveWardrobe(c.character)); }
      }
    }
  });

  // Convert a prose prompt to Danbooru tags in place (for prompts authored before the
  // tag rule). read()/write() let one helper serve any field; tkey drives the spinner.
  let tagging = $state({});
  async function tagify(tkey, read, write, kind = 'character') {
    if (tagging[tkey]) return;
    const text = (read() || '').trim(); if (!text) return;
    tagging[tkey] = true;
    const r = await post('/tagify', { text, kind });
    tagging[tkey] = false;
    if (r.ok && r.data?.tags) write(r.data.tags);
  }
  // Use an existing card image directly as the base reference (no generation).
  async function useAsBase(charKey, url) {
    const s = ensure(charKey); s.baseBusy = true; s.baseErr = null;
    const r = await post(`/characters/${charKey}/reference/from-url`, { url: abs(url) });
    s.baseBusy = false;
    if (r.data?.ok) { s.picked = true; s.refBust = Date.now(); rensure(baseKey(charKey)).cands = []; loadChars(); afterBaseChanged(charKey); }
    else s.baseErr = r.data?.error || 'could not set base';
  }
  async function pickBase(charKey, dataUri) {
    const s = ensure(charKey); s.baseBusy = true;
    const r = await post(`/characters/${charKey}/reference/from-data`, { data: dataUri });
    s.baseBusy = false;
    if (r.data?.ok) { s.picked = true; s.refBust = Date.now(); rensure(baseKey(charKey)).cands = []; loadChars(); afterBaseChanged(charKey); }
    else s.baseErr = r.data?.error || 'could not set base';
  }

  // Start a streamed wardrobe plan. mode 'review' just shows the plan; 'regen' persists it
  // (replace) once it lands. The live text is shown by ONE GenStream component (s.planJob).
  async function startPlan(charKey, mode) {
    const s = ensure(charKey); s.busy = true; s.error = null; s.msg = null;
    s.planMode = mode; s.planJob = null;
    if (mode === 'regen') s.regenMsg = '↻ Regenerating wardrobe… (re-planning outfits)';
    const r = await post(`/stories/${storyKey}/plan-wardrobe`, { character: charKey });
    if (r.ok && r.data?.job) s.planJob = r.data.job;        // GenStream watches it from here
    else { s.busy = false; s.regenMsg = null; s.error = r.data?.error || 'planning failed'; }
  }
  function plan(charKey) { startPlan(charKey, 'review'); }
  // GenStream's final `result` event → apply the plan (and persist it in 'regen' mode).
  // Emotions are the fixed canonical taxonomy (server composes them); the plan is outfits only.
  async function onPlanResult(charKey, data) {
    const s = ensure(charKey);
    const outfits = data?.outfits || [];
    if (s.planMode === 'regen') {
      await post(`/characters/${charKey}/portraits/wardrobe`, { outfits, replace: true });
      s.spriteRefresh = (s.spriteRefresh || 0) + 1;
      s.msg = { ok: true, text: '✓ wardrobe regenerated' };
      s.regenMsg = '✓ Wardrobe re-planned — old sprites cleared. Render the new sprites below.';
    }
    s.plan = { outfits };
    snaps[`${charKey}:plan`] = JSON.stringify({ o: s.plan.outfits });  // skip redundant auto-save
  }
  function onPlanDone(charKey) { const s = ensure(charKey); s.planJob = null; s.busy = false; }

  // Destructive: replace this character's wardrobe entirely (delete outfits + sprites,
  // re-plan fresh). The core (no confirm) is reused by the base-image cascade.
  async function _regenWardrobe(charKey) {
    // Streamed: start a 'regen' plan; onPlanResult persists it (replace) when the job completes.
    await startPlan(charKey, 'regen');
  }
  async function regenWardrobe(charKey) {
    const nm = cast.find((c) => c.character === charKey)?.name || charKey;
    if (!await askConfirm({
      title: `Regenerate ${nm}'s wardrobe?`,
      message: 'Deletes the current outfits and their rendered sprites, then generates a fresh set from the base image. This cannot be undone.',
      confirmLabel: 'Regenerate', danger: true })) return;
    await _regenWardrobe(charKey);
  }
  // A new base only makes an EXISTING wardrobe stale. If the character has no wardrobe yet
  // (common right after a cast regenerate), there's nothing to rebuild — just set the base; the
  // user plans + renders next. Otherwise offer to rebuild the outfits and clear the stale sprites.
  async function afterBaseChanged(charKey) {
    let outfits = 0;
    try { const d = await get(`/characters/${charKey}/portraits`); outfits = (d?.outfits || []).length; }
    catch { /* ignore */ }
    if (outfits <= 0) return;   // nothing built on the old base — silently keep the new base
    if (await askConfirm({
      title: 'Regenerate wardrobe for the new base?',
      message: 'This wardrobe was built on the old base image. Rebuild the outfits and clear the stale sprites to match the new look? You then re-render the sprites below.',
      confirmLabel: 'Regenerate', danger: true })) await _regenWardrobe(charKey);
  }

  function addOutfit(s) { s.plan.outfits.push({ name: '', attire_prompt: '' }); }
  function rmOutfit(s, i) { s.plan.outfits.splice(i, 1); }
</script>

<div class="wardrobe">
  {#each cast as c (c.character)}
    {@const s = cs(c.character)}
    {@const tab = s.tab || 'desc'}
    <div class="cw" class:isprimary={c.character === primaryKey}>
      <div class="cwtop">
        <span class="nm">
          {#if c.character === primaryKey}<span class="lead">★ main character</span>{/if}
          <input class="fld nmedit" placeholder="character name" value={s.name}
            oninput={(e) => { const x = ensure(c.character); x.name = e.currentTarget.value; x.nameTouched = true; }} />
          {#if !hasRef(c)}<span class="noref">no base image</span>{/if}</span>
      </div>

      {#if s.regenMsg}<div class="cwstatus" class:busy={s.busy}>{s.regenMsg}</div>{/if}
      {#if s.planJob}<GenStream jobId={s.planJob} title="Planning {c.name}'s wardrobe" onResult={(d) => onPlanResult(c.character, d)} onDone={() => onPlanDone(c.character)} />{/if}

      <!-- sub-sections (in-page tabs) -->
      <div class="tabs">
        {#each TABS as t (t.id)}
          <button class="tab" class:on={tab === t.id} onclick={() => ensure(c.character).tab = t.id}>{t.label}</button>
        {/each}
      </div>

      {#if tab === 'desc'}
        <div class="sub">Description <span class="lo">— the persona sent to the chat model</span></div>
        <textarea class="fld ta descbox" use:autosize={s.desc} placeholder="character description / persona…"
          value={s.desc} oninput={(e) => { const x = ensure(c.character); x.desc = e.currentTarget.value; x.descTouched = true; }}></textarea>

      {:else if tab === 'base'}
        {#if hasRef(c)}
          <div class="sub">Current base image</div>
          <div class="curbase">
            <ZoomImage src={abs(`/api/characters/${c.character}/reference`) + `?b=${s.refBust}`} caption={`${c.name} — base image`} inline />
          </div>
        {/if}
        <!-- the exact image prompt -->
        <div class="sub">Image prompt <span class="lo">— exactly what's sent to the image model</span>
          <button class="ghost xs" onclick={() => regenBase(c.character)} disabled={tagging[`${c.character}:gen`]} title="generate the base-image prompt from this character's description (persona + appearance)">{tagging[`${c.character}:gen`] ? '…' : '✨ from description'}</button>
          <button class="ghost xs nomar" onclick={() => tagify(`${c.character}:base`, () => cs(c.character).prompt, (t) => ensure(c.character).prompt = t, 'base')} disabled={tagging[`${c.character}:base`]} title="clean current text → structured full-body swimwear base">{tagging[`${c.character}:base`] ? '…' : '⇥ clean'}</button>
          <button class="ghost xs nomar" onclick={() => resetPrompt(c.character)} title="reset to the character's appearance">↺ reset</button></div>
        <TagInput value={s.prompt ?? ''} placeholder={s.prompt == null ? 'loading prompt…' : 'type a booru tag…'}
          onchange={(v) => ensure(c.character).prompt = v} />

        <div class="base">
          <div class="sub">Base image <span class="lo">— {hasRef(c) ? 'a base is set; pick another image or regenerate to replace it' : 'pick a card image or generate one'}</span></div>
          {#if c.images?.length}
            <div class="sub">Use a card image directly</div>
            <div class="cands">
              {#each c.images as url, i (i)}
                <div class="cand">
                  <ZoomImage src={abs(url)} caption={`${c.name} image`} inline />
                  <button class="use" onclick={() => useAsBase(c.character, url)} disabled={s.baseBusy}>Use as base</button>
                </div>
              {/each}
            </div>
          {/if}

          {#if primaryImages.length}
            <div class="sub">Style source <span class="lo">— optional; translates a look (can leak the source's identity)</span></div>
            <div class="styles">
              <button class="sty none" class:on={!styleUrlOf(c)} onclick={() => setStyle(c.character, '')} title="no style — plain prompt only">None</button>
              {#each primaryImages as url, i (i)}
                <button class="sty" class:on={styleUrlOf(c) === url} onclick={() => setStyle(c.character, url)} title="use as style">
                  <img src={abs(url)} alt="" />
                </button>
              {/each}
            </div>
          {/if}

          <div class="sub">Generate <span class="lo">— {styleUrlOf(c) ? 'in the chosen style' : 'plain, from the prompt'}</span>
            <button class="ghost xs" onclick={() => genBase(c.character)} disabled={s.baseBusy || !(s.prompt || '').trim()}>{s.baseBusy ? 'Rendering…' : (rget(`base:${c.character}`).cands.length ? '↻ More' : '🎨 Generate candidates')}</button></div>
          {#if s.baseErr}<div class="err">⚠ {s.baseErr}</div>{/if}
          {#if rget(`base:${c.character}`).cands.length}
            <div class="cands">
              {#each rget(`base:${c.character}`).cands as img, i (i)}
                <div class="cand">
                  <ZoomImage src={img} caption={`${c.name} candidate ${i + 1}`} inline />
                  <button class="use" onclick={() => pickBase(c.character, img)} disabled={s.baseBusy}>Use this</button>
                </div>
              {/each}
            </div>
          {/if}
        </div>

      {:else if tab === 'outfits'}
        <!-- wardrobe controls live HERE, under Outfit images (not above the tab nav) -->
        <div class="owardctl">
          <button class="ghost sm" onclick={() => plan(c.character)} disabled={s.busy}>
            {s.busy ? 'Planning…' : (s.plan ? '↻ Re-plan' : '✨ Plan wardrobe')}
          </button>
          {#if s.plan}
            <button class="ghost sm dgr" onclick={() => regenWardrobe(c.character)} disabled={s.busy}
              title="Delete this character's outfits + sprites and generate a fresh wardrobe">↻ Regenerate wardrobe</button>
          {/if}
        </div>
        {#if s.plan}
          <div class="sub">Outfits <span class="lo">— attire prompts (full-body sprites, varying only the outfit)</span> <button class="ghost xs" onclick={() => addOutfit(s)}>＋</button></div>
          {#each s.plan.outfits as o, i (i)}
            <div class="item">
              <div class="itop"><input class="fld nm2" placeholder="outfit name" bind:value={o.name} />
                <button class="x" onclick={() => rmOutfit(s, i)}>✕</button></div>
              <TagInput value={o.attire_prompt ?? ''} placeholder="attire tags…" onchange={(v) => o.attire_prompt = v} />
            </div>
          {/each}
          <div class="saverow"><span class="pm" class:ok={s.msg?.ok}>{s.saving ? 'Saving…' : (s.msg?.text || 'Auto-saves as you edit')}</span></div>
        {:else}
          <p class="hint lo">No wardrobe yet — use <b>✨ Plan wardrobe</b> to generate outfits.</p>
        {/if}

        <!-- the rendered outfit × emotion sprites -->
        <Sprites charKey={c.character} charName={c.name} hasRef={hasRef(c)} refresh={s.spriteRefresh || 0}
          screen={storyKey ? `stories/${storyKey}/cast` : 'characters/selected'} />

      {:else if tab === 'emotions'}
        <div class="sub">Emotions <span class="lo">— fixed expression range</span></div>
        <p class="hint lo">Emotions are a <b>fixed canonical set</b> (Plutchik-based, ~32: the full
          range from serenity/joy/ecstasy through fear, anger, desire, greed…). They're composed
          once per character from the persona and shared by every outfit, so the sprite set is
          consistent and the runtime can switch by emotion key. Render and re-roll them per outfit in
          the <b>Outfit images</b> tab.</p>
      {/if}
      {#if s.error}<div class="err">⚠ {s.error}</div>{/if}
    </div>
  {/each}
</div>

<style>
  .wardrobe { display: flex; flex-direction: column; gap: 10px; }
  .cw { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 12px; }
  .cw.isprimary { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent-glow); }
  .lead { font-size: 10.5px; font-weight: 700; letter-spacing: .3px; text-transform: uppercase;
          color: var(--accent); background: rgba(109,140,255,.14); border-radius: 999px; padding: 1px 8px; margin-right: 6px; }
  .cwtop { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .owardctl { display: flex; gap: 6px; flex-wrap: wrap; margin: 4px 0 10px; }
  .tabs { display: flex; gap: 2px; margin: 12px 0 10px; border-bottom: 1px solid var(--border); }
  .tab { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 12.5px; font-weight: 600;
         padding: 7px 12px; border-radius: 8px 8px 0 0; border-bottom: 2px solid transparent; cursor: pointer; }
  .tab:hover { color: var(--text); background: var(--elev); filter: none; }
  .tab.on { color: #fff; border-bottom-color: var(--accent); }
  .cwstatus { margin-top: 8px; font-size: 12px; color: var(--good); background: rgba(120,200,140,.1);
              border: 1px solid var(--border-soft); border-radius: 8px; padding: 6px 10px; }
  .cwstatus.busy { color: var(--accent); background: rgba(109,140,255,.1); }
  .dgr { color: var(--bad); border-color: rgba(255,122,122,.4); }
  .dgr:hover:not(:disabled) { background: rgba(255,122,122,.12); color: var(--bad); filter: none; }
  .nm { font-size: 14px; font-weight: 650; display: inline-flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
  .nmedit { flex: 1; min-width: 0; font-size: 14px; font-weight: 650; padding: 5px 8px; background: transparent; border: 1px solid transparent; }
  .nmedit:hover { border-color: var(--border-soft); }
  .nmedit:focus { background: var(--bg); border-color: var(--accent); outline: none; }
  .descbox { font-size: 12.5px; line-height: 1.5; background: var(--bg); }
  .noref { font-size: 10.5px; font-weight: 600; color: #ffd479; background: rgba(255,212,121,.12); border-radius: 999px; padding: 1px 8px; }
  .err { font-size: 12px; color: var(--bad); margin-top: 6px; }
  .base { margin-top: 8px; }
  .cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 8px; margin-top: 6px; }
  .cand { display: flex; flex-direction: column; gap: 4px; }
  .cand :global(.zoom-inline), .cand :global(img) { border-radius: 8px; }
  .use { font-size: 11px; padding: 4px 6px; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); }
  .use:hover { border-color: var(--accent); color: var(--accent); filter: none; }
  .styles { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
  .sty { padding: 0; width: 54px; height: 54px; border-radius: 8px; overflow: hidden; box-shadow: none; border: 2px solid var(--border); background: none; }
  .sty img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .sty.on { border-color: var(--accent); }
  .sty:hover { filter: brightness(1.05); }
  .sty.none { width: auto; height: 54px; padding: 0 12px; font-size: 11.5px; color: var(--muted);
              display: inline-flex; align-items: center; background: var(--elev-2); }
  .sty.none.on { color: var(--accent); }
  .promptbox { font-size: 12.5px; line-height: 1.5; background: var(--bg); }
  .curbase { width: 160px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border-soft);
             background: var(--bg); margin-top: 4px; }
  .curbase :global(img), .curbase :global(.zoom-inline) { border-radius: 10px; }
  .sub { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; margin: 12px 0 4px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .item { background: var(--bg); border: 1px solid var(--border-soft); border-radius: 9px; padding: 8px; margin-top: 6px; display: flex; flex-direction: column; gap: 6px; }
  .itop { display: flex; gap: 8px; align-items: center; }
  .fld { width: 100%; padding: 7px 9px; font-size: 13px; border-radius: 7px; background: var(--panel); border: 1px solid var(--border); color: var(--text); }
  .item .fld { background: var(--bg); }
  .fld:focus { border-color: var(--accent); outline: none; }
  .nm2 { flex: 1; }
  .ta { line-height: 1.5; font-family: inherit; min-height: 34px; overflow: hidden; resize: none; }
  .x { width: 24px; height: 24px; flex: none; padding: 0; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); font-size: 10px; }
  .x:hover { color: var(--bad); filter: none; }
  .xs { font-size: 12px; padding: 1px 7px; border-radius: 6px; margin-left: auto; }
  .nomar { margin-left: 6px; }
  .tag { width: 24px; height: 24px; flex: none; padding: 0; border-radius: 6px; box-shadow: none;
         background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); font-size: 12px; }
  .tag:hover:not(:disabled) { color: var(--accent); border-color: var(--accent); filter: none; }
  .saverow { display: flex; align-items: center; gap: 10px; margin-top: 12px; }
  .pm { font-size: 12px; color: var(--muted); } .pm.ok { color: var(--good); }
  .hint { margin: 6px 0 0; font-size: 11.5px; }
</style>

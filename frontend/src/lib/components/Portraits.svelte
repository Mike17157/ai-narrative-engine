<script>
  import { get, post, put, del } from '$lib/api.js';
  import { limitedPost } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import GenStream from '$lib/components/GenStream.svelte';

  let { charKey, charName, imageModel = '' } = $props();

  let data = $state({ appearance: '', emotions: [], outfits: [] });
  let loaded = $state(null);     // which key we've loaded, so we reload on switch
  let busy = $state(null);       // human-readable label of the in-flight op
  let err = $state(null);
  let bust = $state(0);          // cache-bust images after a (re)render

  // add-outfit form
  let outfitName = $state('');
  let outfitInstr = $state('');

  async function load() {
    err = null;
    try { data = await get(`/characters/${charKey}/portraits`); }
    catch { data = { appearance: '', emotions: [], outfits: [] }; }
  }
  $effect(() => {
    if (charKey && charKey !== loaded) { loaded = charKey; load(); }
  });

  // headless end-to-end generation (flesh → base → outfit → poses/exprs → sprite set)
  let genJob = $state(null);
  async function generateAll() {
    busy = null; err = null; genJob = null;
    const r = await post(`/characters/${charKey}/generate-all`, {});
    if (r.ok && r.data?.job) genJob = r.data.job;
    else err = r.data?.error || 'could not start (is the backend restarted?)';
  }
  async function onGenDone() { genJob = null; await load(); bust++; }

  let fleshed = $state(null);   // the rewritten persona, shown after fleshing
  async function fleshOut() {
    busy = 'Fleshing out the persona…'; err = null; fleshed = null;
    const r = await post(`/characters/${charKey}/flesh`, {});
    busy = null;
    if (r.ok && r.data?.persona) fleshed = r.data.persona;
    else err = r.data?.error || 'flesh failed';
  }

  async function describe() {
    busy = 'Describing reference…'; err = null;
    const r = await post(`/characters/${charKey}/portraits/describe`);
    busy = null;
    if (r.ok && r.data?.appearance != null) data.appearance = r.data.appearance;
    else err = r.data?.error || 'describe failed';
  }

  async function saveAppearance() {
    await put(`/characters/${charKey}/portraits/appearance`, { appearance: data.appearance });
  }

  async function addOutfit() {
    if (!data.appearance) { err = 'Describe the appearance first.'; return; }
    busy = outfitInstr ? `Generating “${outfitName || 'outfit'}”…` : 'Rendering base outfit…';
    err = null;
    const r = await post(`/characters/${charKey}/portraits/outfit`,
      { name: outfitName || (outfitInstr ? outfitInstr.slice(0, 24) : 'Base'), instruction: outfitInstr, image_model: imageModel });
    busy = null;
    if (r.ok && r.data?.outfits) { data = r.data; bust++; outfitName = ''; outfitInstr = ''; }
    else err = r.data?.error || 'outfit generation failed';
  }

  async function removeOutfit(oid) {
    if (!await askConfirm({ title: 'Delete this outfit?', message: 'Removes the outfit and all its expressions.', confirmLabel: 'Delete', danger: true })) return;
    const r = await del(`/characters/${charKey}/portraits/outfit/${oid}`);
    if (r?.outfits) data = r;
  }

  async function genExpressions(outfit) {
    const todo = data.emotions.filter((e) => !outfit.expressions?.[e]);
    if (!todo.length) { err = 'All emotions already generated — delete one to redo it.'; return; }
    err = null;
    for (let i = 0; i < todo.length; i++) {
      busy = `Generating expression ${i + 1}/${todo.length}: ${todo[i]}…`;
      const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${outfit.id}/expression`,
        { emotion: todo[i], image_model: imageModel });
      if (r.ok && r.data?.url) {
        outfit.expressions = { ...outfit.expressions, [r.data.emotion]: r.data.url };
        bust++;
      } else { err = r.data?.error || `failed on ${todo[i]}`; break; }
    }
    busy = null;
  }

  async function genOneExpression(outfit, emotion) {
    busy = `Generating ${emotion}…`; err = null;
    const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${outfit.id}/expression`, { emotion, image_model: imageModel });
    busy = null;
    if (r.ok && r.data?.url) { outfit.expressions = { ...outfit.expressions, [r.data.emotion]: r.data.url }; bust++; }
    else err = r.data?.error || `failed on ${emotion}`;
  }

  async function removeExpression(outfit, emo) {
    const r = await del(`/characters/${charKey}/portraits/outfit/${outfit.id}/expression/${emo}`);
    if (r?.outfits) data = r;
  }

  const imgUrl = (u) => (u ? `${u}?b=${bust}` : null);
</script>

<section class="portraits">
  <h4>Portrait studio <span class="lo">— outfits & emotion expressions</span></h4>
  <p class="phint">
    The image-prompt model describes the reference into an appearance prompt, which seeds outfits;
    each outfit seeds a set of personalized emotion expressions. Renders use the workflow picked above
    (an img2img / IPAdapter workflow gives the best identity).
  </p>

  {#if busy}<div class="busy">⏳ {busy}</div>{/if}
  {#if err}<div class="err">⚠ {err}</div>{/if}

  <!-- one-click: run the whole chain headless on the backend -->
  <div class="block genall">
    <div class="row">
      <button class="sm primary" onclick={generateAll} disabled={!!busy || !!genJob}>⚡ Generate everything</button>
      <span class="lo">flesh → base prompt → base image → one outfit → poses + expressions → full sprite set (saved to disk)</span>
    </div>
    {#if genJob}<GenStream jobId={genJob} title="Generating {charName}" onError={(m) => (err = m)} onDone={onGenDone} />{/if}
  </div>

  <!-- 0. Flesh out — thin seed → disciplined prose (feeds appearance, poses, expressions) -->
  <div class="block">
    <div class="blab">0 · Flesh out the character</div>
    <p class="phint" style="margin:0 0 8px">Rewrites a thin description into a thorough, disciplined-prose
      persona (look + personality + how they carry &amp; express themselves) — the source every section
      parses into tags. Also runs automatically the first time you generate on a thin character.</p>
    <div class="row"><button class="sm" onclick={fleshOut} disabled={!!busy}>✨ Flesh out persona</button></div>
    {#if fleshed}<pre class="fleshed">{fleshed}</pre>{/if}
  </div>

  <!-- 1. Appearance -->
  <div class="block">
    <div class="blab">1 · Appearance prompt</div>
    <textarea class="appear" rows="3" bind:value={data.appearance} onblur={saveAppearance}
      placeholder="booru tags describing the character's canonical look…"></textarea>
    <div class="row">
      <button class="sm" onclick={describe} disabled={!!busy}>✨ Describe from reference</button>
    </div>
  </div>

  <!-- 2. Add outfit -->
  <div class="block">
    <div class="blab">2 · Add outfit</div>
    <div class="orow">
      <input class="oname" bind:value={outfitName} placeholder="name (e.g. Swimsuit)" />
      <input class="oinstr" bind:value={outfitInstr}
        placeholder="instruction — leave blank for the canonical base look" />
      <button class="sm" onclick={addOutfit} disabled={!!busy || !data.appearance}>＋ Generate</button>
    </div>
  </div>

  <!-- 3. Outfits + expressions -->
  {#if data.outfits.length}
    <div class="outfits">
      {#each data.outfits as o (o.id)}
        <div class="outfit">
          <div class="ohead">
            <div class="obase">
              {#if o.base}<ZoomImage src={imgUrl(o.base)} caption={`${charName} — ${o.name}`} inline />{/if}
            </div>
            <div class="ometa">
              <div class="oname-lbl">{o.name}</div>
              {#if o.instruction}<div class="oinstr-lbl">{o.instruction}</div>{/if}
              <div class="oprompt" title={o.prompt}>{o.prompt}</div>
              <div class="oactions">
                <button class="sm" onclick={() => genExpressions(o)} disabled={!!busy}>
                  ✨ Generate expressions
                </button>
                <button class="ghost sm danger" onclick={() => removeOutfit(o.id)} disabled={!!busy}>Delete outfit</button>
              </div>
            </div>
          </div>

          <div class="exprs">
            {#each data.emotions as emo (emo)}
              {@const url = o.expressions?.[emo]}
              <div class="expr" class:empty={!url}>
                {#if url}
                  <ZoomImage src={imgUrl(url)} caption={`${charName} — ${o.name} — ${emo}`} inline />
                  <button class="exdel" title="Delete & redo" onclick={() => removeExpression(o, emo)}>✕</button>
                {:else}
                  <button class="exgen" onclick={() => genOneExpression(o, emo)} disabled={!!busy} title="Generate {emo}">＋</button>
                {/if}
                <span class="exlbl">{emo}</span>
              </div>
            {/each}
          </div>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .portraits { margin-top: 16px; padding: 14px; background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px; }
  .portraits h4 { margin: 0 0 4px; font-size: 12.5px; font-weight: 650; color: var(--text); }
  .phint { margin: 0 0 12px; font-size: 12px; color: var(--muted); }
  .phint a { color: var(--accent); }
  .lo { color: var(--faint); font-weight: 400; }
  .busy { font-size: 12.5px; color: var(--accent); background: rgba(124,109,255,.12); border: 1px solid var(--border-soft); border-radius: 8px; padding: 7px 11px; margin-bottom: 10px; }
  .err { font-size: 12.5px; color: var(--bad); background: rgba(255,122,122,.1); border: 1px solid var(--border-soft); border-radius: 8px; padding: 7px 11px; margin-bottom: 10px; }

  .block { margin-bottom: 14px; }
  .genall { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; background: var(--panel); }
  .genall .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .genall .lo { font-size: 11.5px; color: var(--faint); }
  :global(.portraits) .sm.primary { background: var(--accent); border-color: transparent; color: #fff; }
  :global(.portraits) .sm.primary:hover:not(:disabled) { filter: brightness(1.08); }
  .fleshed { white-space: pre-wrap; word-break: break-word; font-size: 12px; line-height: 1.5;
    color: var(--text); background: var(--bg); border: 1px solid var(--border-soft); border-radius: 8px;
    padding: 10px; margin: 8px 0 0; max-height: 300px; overflow: auto; }
  .blab { font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; margin-bottom: 6px; }
  .appear {
    width: 100%; resize: vertical; min-height: 56px; padding: 8px 10px; font-size: 13px; border-radius: 8px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text); font-family: ui-monospace, monospace; line-height: 1.5;
  }
  .appear:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .row { margin-top: 8px; }
  .orow { display: flex; gap: 8px; align-items: center; }
  .oname { width: 150px; flex: none; }
  .oinstr { flex: 1; min-width: 0; }
  .orow input { padding: 7px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .orow input:focus { border-color: var(--accent); outline: none; }

  button.sm { font-size: 12.5px; padding: 6px 12px; border-radius: 8px; }
  button.danger:hover { color: var(--bad); border-color: rgba(255,122,122,.5); }

  .outfits { display: flex; flex-direction: column; gap: 14px; }
  .outfit { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 12px; }
  .ohead { display: flex; gap: 14px; }
  .obase { width: 140px; flex: none; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--bg); }
  .ometa { min-width: 0; flex: 1; }
  .oname-lbl { font-size: 14px; font-weight: 650; color: var(--text); }
  .oinstr-lbl { font-size: 12px; color: var(--muted); margin-top: 2px; }
  .oprompt {
    font-size: 11.5px; color: var(--faint); font-family: ui-monospace, monospace; margin-top: 6px;
    max-height: 48px; overflow: hidden; line-height: 1.45;
  }
  .oactions { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }

  .exprs { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; margin-top: 12px; }
  .expr { position: relative; display: flex; flex-direction: column; align-items: center; gap: 4px; }
  .expr .exlbl { font-size: 10.5px; color: var(--muted); text-transform: capitalize; }
  .expr.empty {
    border: 1px dashed var(--border); border-radius: 10px; padding: 6px; aspect-ratio: 1 / 1.1;
    justify-content: center; background: var(--bg);
  }
  .exgen {
    width: 38px; height: 38px; border-radius: 10px; font-size: 18px; padding: 0; display: grid; place-items: center;
    background: var(--elev-2); border: 1px solid var(--border); color: var(--muted); box-shadow: none;
  }
  .exgen:hover { color: var(--accent); border-color: var(--accent); filter: none; }
  .exdel {
    position: absolute; top: 3px; right: 3px; z-index: 3; width: 20px; height: 20px; padding: 0; font-size: 11px;
    display: grid; place-items: center; border-radius: 6px; background: rgba(10,12,18,.7); color: #fff;
    border: 1px solid rgba(255,255,255,.18); box-shadow: none;
  }
  .exdel:hover { color: var(--bad); background: rgba(255,122,122,.18); filter: none; }
</style>

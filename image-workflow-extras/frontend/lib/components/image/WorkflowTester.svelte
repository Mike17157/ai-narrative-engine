<script>
  // Single-render workflow tester — the clean "Wan-style" replacement for the old
  // 4-mode grid modal. One form → one streaming render → inline result (image OR
  // video). Generalized from the former WanStudio: works for ANY image workflow.
  //
  //   • wan (video-capable)  → POST /api/wan/render  (its tuned graph + frame logic)
  //   • everything else      → POST /api/workflow/test  (generic; passes a json
  //                            snapshot with negative/steps applied client-side)
  //
  // `json` (optional prop) lets the Graph editor test its UNSAVED in-editor graph;
  // when omitted we snapshot the loaded img.workflow for the active model.
  import { img, workflowNeedsInit } from '$lib/images.svelte.js';
  import { app } from '$lib/app.svelte.js';
  import { consumeSse } from '$lib/sse.js';
  import ZoomImage from '$lib/components/shared/ZoomImage.svelte';

  let { json = null } = $props();

  // The active workflow key. Wan's t2v graph is the only video-capable one the
  // /api/wan/render endpoint supports (its node ids are hardcoded there).
  let model = $derived(app.activeImage);
  let videoCapable = $derived(model === 'wan');
  let needsInit = $derived(workflowNeedsInit());

  let mode = $state('image');          // 'image' | 'video' (wan only)
  let prompt = $state(img.testPrompt || '');
  let negative = $state('');
  let width = $state(1024);
  let height = $state(1024);
  let steps = $state(24);
  let numFrames = $state(25);          // video only (forced to 4n+1 server-side)
  let fps = $state(16);

  let running = $state(false);
  let pct = $state(0);
  let stage = $state('');
  let result = $state(null);           // { kind:'image'|'video', src }
  let err = $state(null);
  let _ctrl = null;

  // Source image (img2img) — mirror the +layout uploader helpers.
  function pickSource(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => { img.testInitImage = r.result; };
    r.readAsDataURL(f);
    e.target.value = '';
  }
  async function useCharRef() {
    if (!app.activeChar) return;
    const res = await fetch(`/api/characters/${app.activeChar}/reference`);
    if (!res.ok) { err = 'active character has no reference image'; return; }
    const blob = await res.blob();
    const r = new FileReader();
    r.onload = () => { img.testInitImage = r.result; };
    r.readAsDataURL(blob);
  }

  // A render-ready snapshot of the workflow with the negative prompt + steps applied,
  // so the generic /api/workflow/test honours those fields without a backend change.
  function tunedGraph() {
    const src = json || img.workflow;
    if (!src) return undefined;
    const g = structuredClone($state.snapshot(src));
    for (const n of Object.values(g)) {
      if (!n?.inputs) continue;
      if (negative && n.class_type === 'CLIPTextEncode' && /negative/i.test(n._meta?.title || ''))
        n.inputs.text = negative;
      if (steps && typeof n.inputs.steps === 'number') n.inputs.steps = +steps;
    }
    return g;
  }

  function onEvent(ev) {
    if (ev.type === 'progress') { pct = ev.max ? Math.round((ev.value / ev.max) * 100) : pct; stage = mode === 'video' ? 'sampling frames…' : 'sampling…'; }
    else if (ev.type === 'node') { stage = 'running…'; }
    else if (ev.type === 'image') {
      if ((ev.videos || []).length) result = { kind: 'video', src: ev.videos[0] };
      else if ((ev.images || []).length) result = { kind: 'image', src: ev.images[0] };
    }
    else if (ev.type === 'error') { err = ev.error; }
  }

  async function render() {
    if (running) { _ctrl?.abort(); return; }
    if (!model) { err = 'pick a workflow first'; return; }
    if (!prompt.trim()) { err = 'enter a prompt'; return; }
    if (needsInit && !img.testInitImage) { err = 'this img2img workflow needs a source image'; return; }
    running = true; pct = 0; stage = 'queued…'; err = null; result = null;
    _ctrl = new AbortController();
    try {
      let res;
      if (videoCapable) {
        res = await fetch('/api/wan/render', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: _ctrl.signal,
          body: JSON.stringify({ prompt, negative, mode, width, height, steps, num_frames: numFrames, fps }),
        });
      } else {
        res = await fetch('/api/workflow/test', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: _ctrl.signal,
          body: JSON.stringify({
            model, prompt, json: tunedGraph(),
            init_image: img.testInitImage || undefined,
            width: width || undefined, height: height || undefined,
          }),
        });
      }
      await consumeSse(res, onEvent);
      if (!result && !err) err = 'no image returned';
    } catch (e) { if (e.name !== 'AbortError') err = String(e); }
    running = false; stage = ''; _ctrl = null;
  }
</script>

<div class="wt">
  <div class="panel">
    <div class="who">
      <strong>{model || 'no workflow selected'}</strong>
      {#if needsInit}<span class="tag">img2img</span>{/if}
      {#if videoCapable}<span class="tag">🎞 video</span>{/if}
    </div>

    {#if videoCapable}
      <div class="modeseg">
        <button class:on={mode === 'image'} onclick={() => (mode = 'image')} disabled={running}>🖼 Image</button>
        <button class:on={mode === 'video'} onclick={() => (mode = 'video')} disabled={running}>🎞 Video</button>
      </div>
    {/if}

    <label class="fld">Prompt
      <textarea rows="3" bind:value={prompt} placeholder="1girl, solo, garden, masterpiece"></textarea>
    </label>
    <label class="fld">Negative
      <input bind:value={negative} placeholder="(uses the workflow's baked negative if blank)" />
    </label>

    {#if needsInit}
      <div class="fld">Source image
        <div class="srcrow">
          <div class="srcimg">
            {#if img.testInitImage}<ZoomImage src={img.testInitImage} caption="source" inline />
            {:else}<div class="srcph">no source</div>{/if}
          </div>
          <div class="srcctl">
            <label class="upbtn">Upload…<input type="file" accept="image/*" onchange={pickSource} hidden /></label>
            <button class="ghost sm" onclick={useCharRef} disabled={!app.activeChar}>Use character ref</button>
            {#if img.testInitImage}<button class="ghost sm" onclick={() => (img.testInitImage = null)}>Clear</button>{/if}
          </div>
        </div>
      </div>
    {/if}

    <div class="grid">
      <label>Width<input type="number" step="16" min="64" bind:value={width} /></label>
      <label>Height<input type="number" step="16" min="64" bind:value={height} /></label>
      <label>Steps<input type="number" min="1" max="60" bind:value={steps} /></label>
      {#if videoCapable && mode === 'video'}
        <label title="Forced to the nearest 4n+1 server-side">Frames<input type="number" min="5" step="4" bind:value={numFrames} /></label>
        <label>FPS<input type="number" min="1" max="30" bind:value={fps} /></label>
      {/if}
    </div>

    <button class="go" onclick={render} disabled={!model}>
      {running ? '■ Stop' : (videoCapable && mode === 'video' ? 'Generate clip' : 'Generate')}
    </button>
    {#if running}
      <div class="bar"><div class="fill" style="width:{pct}%"></div></div>
      <div class="stage">{stage} {pct ? pct + '%' : ''}</div>
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
  </div>

  <div class="stage-out">
    {#if result?.kind === 'image'}
      <ZoomImage src={result.src} caption={model} />
    {:else if result?.kind === 'video'}
      <video src={result.src} controls autoplay loop muted></video>
    {:else}
      <div class="ph">{running ? 'Rendering…' : 'Your result appears here'}</div>
    {/if}
  </div>
</div>

<style>
  .wt { display: flex; gap: 18px; align-items: flex-start; }
  .panel { width: 340px; flex: none; display: flex; flex-direction: column; gap: 12px; }
  .who { display: flex; align-items: center; gap: 8px; font-size: 14px; }
  .who strong { font-family: var(--mono, monospace); }
  .tag { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); background: var(--elev); border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; }
  .modeseg { display: inline-flex; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .modeseg button { flex: 1; border: 0; border-radius: 0; background: var(--elev); color: var(--muted); padding: 9px 0; font-size: 13px; font-weight: 600; }
  .modeseg button.on { color: #fff; background: var(--accent); }
  .fld { display: flex; flex-direction: column; gap: 5px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .fld textarea, .fld input { text-transform: none; letter-spacing: 0; font-weight: 400; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); }
  .fld textarea { resize: vertical; line-height: 1.45; font-family: inherit; }
  .srcrow { display: flex; gap: 12px; align-items: flex-start; }
  .srcimg { width: 110px; flex: none; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--elev); }
  .srcph { aspect-ratio: 1; display: grid; place-items: center; font-size: 11px; color: var(--faint); }
  .srcctl { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .upbtn { display: inline-block; cursor: pointer; font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 8px; border: 1px solid var(--border); background: var(--accent); color: #fff; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .grid label { display: flex; flex-direction: column; gap: 4px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .grid input { padding: 6px 8px; font-size: 12.5px; border-radius: 7px; background: var(--bg); }
  .go { padding: 11px; font-size: 14px; font-weight: 700; }
  .bar { height: 6px; border-radius: 999px; background: var(--elev); overflow: hidden; }
  .fill { height: 100%; background: var(--accent); transition: width .2s; }
  .stage { font-size: 12px; color: var(--accent); }
  .err { font-size: 12.5px; white-space: pre-wrap; }
  .stage-out { flex: 1; min-width: 0; aspect-ratio: 1; max-height: 70vh; border: 1px solid var(--border-soft); border-radius: 14px; background: var(--panel); display: grid; place-items: center; overflow: hidden; }
  .stage-out video { max-width: 100%; max-height: 100%; border-radius: 12px; }
  .ph { color: var(--faint); font-size: 13px; }
  @media (max-width: 720px) { .wt { flex-direction: column; } .panel { width: 100%; } }
</style>

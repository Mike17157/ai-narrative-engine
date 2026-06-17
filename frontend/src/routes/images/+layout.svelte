<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { img, loadWorkflow, loadChoices, runTest, cancelTest, saveTestPrompt, TEST_COUNT,
           workflowNeedsInit, sweepParams, sweepValues, runSweep, composeTestCells } from '$lib/images.svelte.js';
  import { app, clearSubnav } from '$lib/app.svelte.js';
  import { chars, loadChars } from '$lib/characters.svelte.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import Combobox from '$lib/components/Combobox.svelte';

  let { children } = $props();

  // Persist the test prompt globally on any change (typed or preset-picked).
  $effect(() => { saveTestPrompt(img.testPrompt); });

  // img2img workflows need a source image for the test grid to run against.
  let needsInit = $derived(workflowNeedsInit());

  // --- test modes: sprite/scene mirror real production renders; tags/sweep are raw experimentation ---
  let testMode = $state('sprite'); // sprite | scene | tags | sweep
  // Subject source for the parity modes (sprite/scene): typed prompt, or a picked character's appearance.
  let subjectMode = $state('typed'); // typed | char
  let testChar = $state('');
  let charItems = $derived((chars.list || []).map((c) => ({ value: c.key, label: c.name || c.key })));
  const REP_EMOTIONS = ['Neutral', 'Joy', 'Sadness', 'Anger', 'Fear', 'Surprise', 'Desire', 'Disgust'];
  const runParity = (mode) => composeTestCells(mode, { subjectMode, subject: img.testPrompt, character: testChar });
  let params = $derived(testMode === 'sweep' && img.test ? sweepParams() : []);
  let paramItems = $derived(params.map((p) => ({ value: `${p.id}.${p.field}`, label: p.label })));
  let sel = $state('');
  let sweepParam = $derived(params.find((p) => `${p.id}.${p.field}` === sel) || null);
  let smin = $state(0), smax = $state(1), scount = $state(6);
  let comboPick = $state({});
  let previewVals = $derived(sweepParam?.kind === 'number' ? sweepValues(smin, smax, scount) : []);

  function pickParam(v) {
    sel = v;
    const p = params.find((x) => `${x.id}.${x.field}` === v);
    if (!p) return;
    if (p.kind === 'number') {
      const cur = +p.value || 0;
      smin = 0; smax = cur > 0 ? +(cur * 2).toFixed(2) : 1; scount = 6;
    } else {
      comboPick = Object.fromEntries(p.options.map((o) => [o, true]));
    }
  }
  function runSweepClick() {
    if (!sweepParam) return;
    const values = sweepParam.kind === 'number'
      ? sweepValues(smin, smax, scount)
      : sweepParam.options.filter((o) => comboPick[o]);
    if (values.length) runSweep(sweepParam, values);
  }
  function rerun() {
    const m = img.test?.mode;
    if (m === 'sweep') runSweepClick();
    else if (m === 'sprite' || m === 'scene') runParity(m);
    else runTest();
  }

  function pickTestSource(e) {
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
    if (!res.ok) { img.msg = { err: true, text: 'active character has no reference image' }; return; }
    const blob = await res.blob();
    const r = new FileReader();
    r.onload = () => { img.testInitImage = r.result; };
    r.readAsDataURL(blob);
  }

  const tabs = [
    { id: 'models', label: 'Models' },
    { id: 'graph', label: 'Graph' },
    { id: 'roles', label: 'Roles' },
    { id: 'poses', label: 'Poses' },
    { id: 'lora', label: 'LoRA' },
    { id: 'connection', label: 'Connection' }
  ];
  const LORA_TABS = [
    { id: 'prompts', label: 'Prompts' }, { id: 'generation', label: 'Generation' },
    { id: 'dataset', label: 'Dataset' }, { id: 'train', label: 'Train' },
    { id: 'library', label: 'Library' }, { id: 'network', label: 'Network' }
  ];
  let path = $derived($page.url.pathname);
  const active = (id) => path === `/images/${id}` || path.startsWith(`/images/${id}/`);
  let curTab = $derived(tabs.find((t) => active(t.id))?.id || 'models');
  let curLora = $derived(LORA_TABS.find((t) => path === `/images/lora/${t.id}`)?.id || 'prompts');
  // Images navigates via a HORIZONTAL header (below) instead of the left panel — the graph
  // editor needs the full width. Keep the global sub-nav cleared so no left panel renders here.
  $effect(() => { clearSubnav(); });

  onMount(() => { loadChoices(); loadChars(); });
  // Load the workflow when the active image model becomes available / changes
  // (avoids a race with refreshAll() setting app.activeImage on first paint).
  let loadedFor = $state(null);
  $effect(() => {
    const key = app.activeImage;
    if (key && key !== loadedFor) { loadedFor = key; loadWorkflow(); }
  });

  // Honor a deep-link target (e.g. Settings → "set up trainer" lands on LoRA).
  $effect(() => { if (app.nav?.imageTab) { const t = app.nav.imageTab; app.nav.imageTab = null; goto(`/images/${t}`); } });

</script>

<div class="page">
  <div class="col" class:wide={path !== '/images/graph'} class:full={path === '/images/graph'}>
    <nav class="ihead">
      <div class="itabs">
        {#each tabs as t (t.id)}
          <button class="itab" class:on={curTab === t.id}
            onclick={() => goto(t.id === 'lora' ? '/images/lora/prompts' : `/images/${t.id}`)}>{t.label}</button>
        {/each}
      </div>
      {#if curTab === 'lora'}
        <div class="itabs sub">
          {#each LORA_TABS as t (t.id)}
            <button class="itab deep" class:on={curLora === t.id} onclick={() => goto(`/images/lora/${t.id}`)}>{t.label}</button>
          {/each}
        </div>
      {/if}
    </nav>
    {#if img.msg}<div class="status" class:ok={img.msg.ok} class:err={img.msg.err}>{img.msg.text}</div>{/if}
    {@render children()}
  </div>
</div>

{#if img.test}
  <div class="overlay" onclick={() => img.test.phase !== 'running' && (img.test = null)}>
    <div class="modal" onclick={(e) => e.stopPropagation()}>
      <div class="mhead">
        <strong>
          {img.test.mode === 'sweep' ? `Parameter sweep — ${img.test.param}`
            : img.test.mode === 'sprite' ? 'Sprite test — full body × emotions'
            : img.test.mode === 'scene' ? 'Scene test'
            : 'Test render — random tags'}{img.test.phase === 'running' ? ` (${img.test.cells.filter((c) => c.image || c.error).length}/${img.test.cells.length})` : ''}
        </strong>
        <button class="icon" title={img.test.phase === 'running' ? 'Cancel & close' : 'Close'}
          onclick={() => { if (img.test.phase === 'running') cancelTest(); img.test = null; }}>✕</button>
      </div>

      {#if img.test.phase === 'input'}
        <div class="modeseg">
          <button class:on={testMode === 'sprite'} onclick={() => (testMode = 'sprite')}>Sprite</button>
          <button class:on={testMode === 'scene'} onclick={() => (testMode = 'scene')}>Scene</button>
          <button class:on={testMode === 'tags'} onclick={() => (testMode = 'tags')}>Random tags</button>
          <button class:on={testMode === 'sweep'} onclick={() => (testMode = 'sweep')}>Parameter sweep</button>
        </div>

        {#if testMode === 'sprite' || testMode === 'scene'}
          <p class="lo" style="margin:0 0 8px">
            {testMode === 'sprite'
              ? 'Renders the REAL sprite prompt — subject + expression + pose + full-body framing — so framing/pose match production.'
              : 'Renders the subject through the scene workflow’s own framing.'}
          </p>
          <div class="modeseg" style="margin-bottom:10px">
            <button class:on={subjectMode === 'typed'} onclick={() => (subjectMode = 'typed')}>Typed subject</button>
            <button class:on={subjectMode === 'char'} onclick={() => (subjectMode = 'char')}>From character</button>
          </div>
          {#if subjectMode === 'char'}
            <label>Character <span class="lo">— uses its real appearance tags</span></label>
            <Combobox items={charItems} value={testChar} placeholder="pick a character…" onpick={(v) => (testChar = v)} />
          {:else}
            <label for="tprompt">Subject <span class="lo">— appearance tags; framing &amp; pose are added automatically</span></label>
            <textarea id="tprompt" rows="2" bind:value={img.testPrompt} placeholder="e.g. 1girl, long blue hair, red eyes, school uniform"></textarea>
          {/if}
          {#if testMode === 'sprite'}
            <p class="lo" style="margin:8px 0 0">Emotions: {REP_EMOTIONS.join(' · ')}</p>
          {/if}
        {:else}
          <label for="tprompt">Subject / prompt {#if testMode === 'tags'}<span class="lo">— a random booru tag is appended per sample</span>{:else}<span class="lo">— held fixed across the sweep</span>{/if}</label>
          <textarea id="tprompt" rows="3" bind:value={img.testPrompt} placeholder="e.g. rio \(blue archive\), 1girl, safe, masterpiece"></textarea>
        {/if}

        {#if needsInit}
          <label for="tsrc">Source image <span class="lo">— this is an img2img workflow; every sample starts from it</span></label>
          <div class="srcrow">
            <div class="srcimg">
              {#if img.testInitImage}<ZoomImage src={img.testInitImage} caption="source" inline />
              {:else}<div class="srcph">no source</div>{/if}
            </div>
            <div class="srcctl">
              <label class="upbtn">Upload…<input id="tsrc" type="file" accept="image/*" onchange={pickTestSource} hidden /></label>
              <button class="ghost sm" onclick={useCharRef} disabled={!app.activeChar}>Use character reference</button>
              {#if img.testInitImage}<button class="ghost sm" onclick={() => (img.testInitImage = null)}>Clear</button>{/if}
            </div>
          </div>
        {/if}

        {#if testMode === 'sweep'}
          <label>Parameter to sweep</label>
          <Combobox items={paramItems} value={sel} placeholder="pick a workflow parameter…" onpick={pickParam} />
          {#if sweepParam?.kind === 'number'}
            <div class="srow">
              <label class="nf">min<input type="number" step="0.05" bind:value={smin} /></label>
              <label class="nf">max<input type="number" step="0.05" bind:value={smax} /></label>
              <label class="nf">count<input type="number" min="2" max="24" bind:value={scount} /></label>
            </div>
            {#if previewVals.length}<p class="lo">values: {previewVals.join(', ')}</p>{/if}
          {:else if sweepParam?.kind === 'combo'}
            <div class="copts">
              {#each sweepParam.options as o}
                <label class="cb"><input type="checkbox" bind:checked={comboPick[o]} /> {o}</label>
              {/each}
            </div>
          {/if}
        {/if}

        <div class="row" style="margin-top:12px">
          {#if testMode === 'sprite'}
            <button onclick={() => runParity('sprite')} disabled={subjectMode === 'char' && !testChar}>Generate sprite set</button>
          {:else if testMode === 'scene'}
            <button onclick={() => runParity('scene')} disabled={subjectMode === 'char' && !testChar}>Render scene</button>
          {:else if testMode === 'tags'}
            <button onclick={runTest} disabled={needsInit && !img.testInitImage}>Generate {TEST_COUNT} samples</button>
          {:else}
            <button onclick={runSweepClick} disabled={!sweepParam || (needsInit && !img.testInitImage)}>Run sweep</button>
          {/if}
          {#if needsInit && !img.testInitImage}<span class="lo">add a source image first</span>{/if}
        </div>
      {:else}
        <div class="agrid">
          {#each img.test.cells as c (c.label)}
            <figure class="acell">
              <div class="athumb">
                {#if c.image}<ZoomImage src={c.image} caption={c.label} />
                {:else if c.error}<div class="aph err" title={c.error}>failed</div>
                {:else if c.pct !== null}<div class="aph">{c.pct ? c.pct + '%' : '…'}</div>
                {:else}<div class="aph">queued</div>{/if}
              </div>
              <figcaption>{c.label}</figcaption>
            </figure>
          {/each}
        </div>
        {#if img.test.phase === 'running'}
          <div class="row" style="margin-top:12px">
            <button class="ghost" onclick={cancelTest}>Stop</button>
          </div>
        {:else}
          <div class="row" style="margin-top:12px">
            <button onclick={rerun}>{img.test.mode === 'sweep' ? 'Run again' : 'Generate again'}</button>
            <button class="ghost" onclick={() => (img.test = { phase: 'input', cells: img.test.cells, mode: img.test.mode })}>Edit</button>
          </div>
        {/if}
      {/if}
    </div>
  </div>
{/if}

<style>
  .col.full { max-width: none; }
  .status { font-size: 13px; margin: 0 0 12px; }

  /* Horizontal section nav (Images only) — frees the left panel's width for the graph. */
  .ihead { display: flex; flex-direction: column; gap: 4px; margin: -2px 0 16px; border-bottom: 1px solid var(--border); }
  .itabs { display: flex; gap: 2px; flex-wrap: wrap; }
  .itabs.sub { gap: 1px; padding-bottom: 7px; }
  .itab { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 13.5px; font-weight: 600;
          padding: 9px 15px; border-radius: 8px 8px 0 0; border-bottom: 2px solid transparent; cursor: pointer; }
  .itab:hover { color: var(--text); background: var(--elev); filter: none; }
  .itab.on { color: #fff; border-bottom-color: var(--accent); }
  .itab.deep { font-size: 12.5px; padding: 5px 11px; border-radius: 7px; border-bottom: 0; }
  .itab.deep:hover { background: var(--elev); }
  .itab.deep.on { color: #fff; background: var(--elev); box-shadow: inset 0 0 0 1px var(--accent); border-bottom: 0; }

  .overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, .6); display: grid; place-items: center; z-index: 50; padding: 24px; }
  .modal { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg);
           box-shadow: var(--shadow); width: min(92vw, 760px); max-height: 88vh; overflow: auto; padding: 16px; }
  .mhead { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
  textarea { width: 100%; resize: vertical; }
  .lo { color: var(--faint); font-weight: 400; }

  .srcrow { display: flex; gap: 14px; align-items: flex-start; margin-top: 6px; }
  .srcimg { width: 140px; flex: none; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--elev); }
  .srcph { aspect-ratio: 1; display: grid; place-items: center; font-size: 12px; color: var(--faint); }
  .srcctl { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .upbtn {
    display: inline-block; cursor: pointer; font-size: 12.5px; font-weight: 600; padding: 6px 12px;
    border-radius: 8px; border: 1px solid var(--border); background: var(--accent); color: #fff;
  }
  .upbtn:hover { filter: brightness(1.08); }

  .modeseg { display: inline-flex; border: 1px solid var(--border); border-radius: 9px; overflow: hidden; margin-bottom: 14px; }
  .modeseg button { border: 0; border-radius: 0; box-shadow: none; background: var(--elev); color: var(--muted); padding: 7px 16px; font-size: 13px; font-weight: 600; }
  .modeseg button:hover { color: var(--text); background: var(--elev-2); filter: none; }
  .modeseg button.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1.5px var(--accent); }
  .srow { display: flex; gap: 10px; margin-top: 6px; }
  .nf { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--muted); }
  .nf input { width: 90px; }
  .copts { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 6px; }
  .cb { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text); }
  .cb input { width: auto; }

  .agrid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .acell { margin: 0; }
  .athumb { aspect-ratio: 1; border-radius: 10px; overflow: hidden; background: var(--elev); border: 1px solid var(--border-soft); display: grid; place-items: center; }
  .aph { font-size: 12px; color: var(--muted); } .aph.err { color: var(--bad); }
  .acell figcaption { font-size: 11.5px; color: var(--muted); text-align: center; margin-top: 5px; }
  @media (max-width: 620px) { .agrid { grid-template-columns: repeat(2, 1fr); } }
</style>

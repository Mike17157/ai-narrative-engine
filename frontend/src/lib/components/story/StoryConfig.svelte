<script>
  import { onMount } from 'svelte';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import { get, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  // Global Story Builder configuration — the model + system prompt driving each stage
  // of story consolidation and generation. Persisted to configs/story_builder.json.
  // `section` is driven by the left side menu (?section=…); the in-page stage pills are gone.
  let { section = 'storyboard', charItems = [], modelItems = [] } = $props();

  const STAGES = [
    { id: 'storyboard', label: 'Storyboard', note: 'generation — chapters from the card' },
    { id: 'locations', label: 'Scenes / locations', note: 'consolidation — distinct neutral places' },
    { id: 'characters', label: 'Characters', note: 'consolidation — supporting cast backgrounds' },
    { id: 'wardrobe', label: 'Wardrobe', note: 'generation — outfits + expressions' },
    { id: 'base_image', label: 'Base image', note: 'character base reference — fills the physical-feature schema' }
  ];
  let cfg = $state(null);          // cached story-builder config
  let stage = $state('storyboard');
  let model = $state('');          // this stage's own model (a concrete id)
  let invention = $state('balanced'); // none | balanced | inventive
  let data = $state(null);         // { base, default, system, prompt?, model }
  let previewChar = $state('');    // character used to preview / test the stage
  let active = $state('');         // active chat model (only used to seed an unset stage)
  let saving = $state(false);
  let msg = $state(null);
  let testing = $state(false);
  let testResult = $state(null);   // { summary, output } | { err }
  let loading = $state(false);     // true while load() runs (suppresses auto-save)
  let snap = $state(null);         // signature of the last loaded/saved state
  let saveTimer = null;
  const sig = () => JSON.stringify([stage, model, data?.base, invention]);

  async function test() {
    if (!previewChar) { testResult = { err: 'pick a character to test with' }; return; }
    testing = true; testResult = null;
    const r = await post('/stories/builder/test',
      { stage, character: previewChar, model, system: data?.base });
    testing = false;
    testResult = r.ok ? r.data : { err: r.data?.error || 'test failed' };
  }

  // Always re-fetch the config so the saved model/prompt load correctly.
  async function load() {
    loading = true; data = null; msg = null;
    cfg = await get('/story-builder');
    model = (cfg.models || {})[stage] || active;
    invention = (cfg.inventions || {})[stage] || 'balanced';
    const r = await post('/stories/builder/prompt', { stage, character: previewChar, model });
    data = r.data;
    loading = false;
    snap = sig();   // baseline; auto-save only fires on changes after this
  }

  // One-time freeze: give every stage a concrete model so changing the active chat model
  // never moves a stage. Additive only — fills unset stages, preserves systems/inventions.
  onMount(async () => {
    const c = await get('/story-builder');
    try { active = (await get('/text-models?kind=text')).active || ''; } catch { /* offline */ }
    if (active) {
      const models = { ...(c.models || {}) };
      let changed = false;
      for (const s of STAGES) if (!models[s.id]) { models[s.id] = active; changed = true; }
      if (changed) await post('/story-builder', { ...c, models });
    }
    load();
  });

  // Image-input stages need a vision model — filter the picker to those.
  let usableModels = $derived(data?.requires_image ? modelItems.filter((m) => m.vision) : modelItems);

  function changeStage(s) { stage = s; load(); }
  function resetPrompt() { if (data) data.base = data.default; }   // auto-saves via the effect
  async function save() {
    if (!data) return;
    saving = true; msg = null;
    cfg = await get('/story-builder');                 // fresh + merge, so other stages are preserved
    cfg.systems = { ...(cfg.systems || {}), [stage]: data.base };
    cfg.models = { ...(cfg.models || {}), [stage]: model || '' };
    cfg.inventions = { ...(cfg.inventions || {}), [stage]: invention };
    const r = await post('/story-builder', cfg);
    saving = false;
    msg = r.data?.ok ? { ok: true, text: '✓ saved' } : { err: true, text: r.data?.error || 'save failed' };
    snap = sig();
  }

  // Auto-save: debounce any change to this stage's model / prompt.
  $effect(() => {
    const cur = JSON.stringify([stage, model, data?.base, invention]);
    if (loading || snap === null || cur === snap) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  });
  // Follow the side-menu section into this component's stage state.
  $effect(() => { if (section && section !== stage) changeStage(section); });
  const curStage = $derived(STAGES.find((s) => s.id === stage));
</script>

<div class="cfg">
  <p class="intro">Each stage of story consolidation and generation has its own model and system prompt.
    Choices are saved for every story; leave a model blank to use the active chat model.</p>

  {#if curStage}<h3 class="shead">{curStage.label}</h3><p class="note">{curStage.note}</p>{/if}

  {#if data}
    <label>Model <span class="lo">— this stage's own model{data.requires_image ? '; needs a 👁 vision model (reads the reference image)' : ''}</span></label>
    <Combobox items={usableModels} bind:value={model} placeholder="pick a model…" />
    {#if data.requires_image && model && !usableModels.some((m) => m.value === model)}
      <p class="note" style="color:var(--bad)">⚠ the saved model isn't vision-capable — pick one that reads images</p>
    {/if}

    <label>Creativity <span class="lo">— how much the model invents vs. follows the source</span></label>
    <div class="seg">
      {#each ['none', 'balanced', 'inventive'] as lvl}
        <button class="segbtn" class:on={invention === lvl} onclick={() => (invention = lvl)}>{lvl}</button>
      {/each}
    </div>

    <label>System prompt <span class="lo">— editable; saved for this stage</span></label>
    <textarea class="fld ta mono" use:autosize={data.base} bind:value={data.base}></textarea>

    <div class="prow">
      <button class="ghost sm" onclick={resetPrompt}>Reset to default</button>
      <span class="pm" class:ok={msg?.ok} class:err={msg?.err}>{saving ? 'Saving…' : (msg?.text || 'Auto-saves')}</span>
    </div>

    <div class="preview">
      <label style="margin-top:4px">Test this stage <span class="lo">— runs it for one character with the settings above (prerequisites use saved config)</span></label>
      <div class="testrow">
        <Combobox items={charItems} bind:value={previewChar} placeholder="character…" onpick={load} />
        <button class="sm" onclick={test} disabled={testing || !previewChar}>{testing ? 'Testing…' : '▶ Test'}</button>
      </div>
      {#if testResult?.err}<div class="terr">⚠ {testResult.err}</div>{/if}
      {#if testResult?.output}
        <div class="tsum">{testResult.summary}</div>
        <pre class="composed">{testResult.output}</pre>
      {/if}
      {#if data.prompt}
        <details class="cin"><summary>Composed input sent to the model (read-only)</summary><pre class="composed">{data.prompt}</pre></details>
      {/if}
    </div>
  {:else}<p class="lo">Loading…</p>{/if}
</div>

<style>
  .cfg { max-width: 760px; }
  .intro { font-size: 12.5px; color: var(--muted); margin: 0 0 6px; }
  label { display: block; font-size: 11px; color: var(--muted); margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fld { width: 100%; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .ta { line-height: 1.5; font-family: inherit; min-height: 38px; overflow: hidden; resize: none; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .shead { margin: 0 0 2px; font-size: 16px; font-weight: 700; color: var(--text); }
  .note { font-size: 12px; color: var(--faint); margin: 6px 0 0; }
  .prow { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  .pm { font-size: 12px; color: var(--muted); } .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }
  .preview { margin-top: 16px; border-top: 1px solid var(--border-soft); padding-top: 8px; }
  .testrow { display: flex; gap: 8px; align-items: center; }
  .testrow :global(.cb) { flex: 1; }
  .terr { font-size: 12.5px; color: var(--bad); margin-top: 8px; }
  .tsum { font-size: 12px; color: var(--accent); font-weight: 600; margin-top: 10px; }
  .cin { margin-top: 10px; } .cin summary { font-size: 12px; color: var(--muted); cursor: pointer; }
  .composed { white-space: pre-wrap; word-break: break-word; font-size: 11.5px; color: var(--text);
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: 8px; padding: 10px; margin: 8px 0 0; max-height: 340px; overflow: auto; }
  .seg { display: flex; gap: 4px; }
  .segbtn { padding: 5px 14px; font-size: 12px; border-radius: 6px; border: 1px solid var(--border);
    background: var(--bg); color: var(--muted); cursor: pointer; text-transform: capitalize; }
  .segbtn.on { background: var(--accent); color: #fff; border-color: var(--accent); }
</style>

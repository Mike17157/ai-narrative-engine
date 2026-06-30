<script>
  // Test a stack: pick stack + theme + workflow, turn a natural-language scene
  // into tags (tagify), resolve the stack (keyword routing) and render one
  // sample. The prompt field binds the shared global test prompt (img.testPrompt),
  // so the subject is the same one Classify and the Images test modal use.
  import { onDestroy } from 'svelte';
  import { post } from '$lib/api.js';
  import { jobStream } from '$lib/sse.js';
  import { app } from '$lib/app.svelte.js';
  import { img } from '$lib/images.svelte.js';
  import Combobox from '$lib/components/shared/Combobox.svelte';
  import ImgCard from '$lib/components/image/ImgCard.svelte';
  import ProgressBar from '$lib/components/shared/ProgressBar.svelte';
  import { loraLib, famLabel, baseFamByKey } from '$lib/lora-library.svelte.js';

  let { stack = '' } = $props();

  let testStack = $state('');
  let testTheme = $state('');
  let testModel = $state('anima');

  // Sync to the externally selected stack (from StackBuilder on the Stacks tab).
  $effect(() => { if (stack && stack !== testStack) testStack = stack; });
  let resolved = $state(null);
  let sceneNL = $state('');
  let tagifying = $state(false);
  let rendering = $state(false);
  let renderImg = $state(null);
  let renderPct = $state(null);
  let renderErr = $state(null);
  let _job = null;

  onDestroy(() => { _job?.cancel(); _job = null; });

  let imgModelItems = $derived((app.models?.image || []).map((m) => ({ value: m.key, label: m.key, group: famLabel()[baseFamByKey()[m.key]] || 'Other' })));
  let stackItems = $derived(loraLib.cfg.stacks.filter((s) => s.name).map((s) => ({ value: s.name, label: s.name })));
  let themeItems = $derived([{ value: '', label: '— none —' }, ...loraLib.cfg.library.filter((l) => l.type === 'theme' && l.name).map((l) => ({ value: l.name, label: l.name }))]);

  async function tagify() {
    if (!sceneNL.trim() || tagifying) return;
    tagifying = true; loraLib.msg = { text: 'Generating tags…' };
    const r = await post('/loras/tagify', { scene: sceneNL });
    if (r.data?.text != null) { img.testPrompt = r.data.text; loraLib.msg = { ok: true, text: '✓ tags generated' }; }
    else loraLib.msg = { err: true, text: r.data?.error || 'tagify failed' };
    tagifying = false;
  }

  async function resolve() {
    renderImg = null; renderErr = null;
    const r = await post('/loras/resolve', { stack: testStack, text: img.testPrompt, theme: testTheme || null });
    resolved = r.data?.loras ? r.data : null;
    if (!resolved) loraLib.msg = { err: true, text: r.data?.error || 'resolve failed' };
  }

  async function render() {
    if (!resolved || rendering) return;
    rendering = true; renderErr = null; renderImg = null; renderPct = null;
    try {
      const res = await fetch('/api/loras/grid-render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cells: [{ key: 'testbench', model: testModel,
                    checkpoint_override: resolved.checkpoint || null,
                    loras: resolved.loras || [], prompt: img.testPrompt }],
        }),
      });
      const data = await res.json();
      if (!data.ok || !data.id) { renderErr = data.error || 'failed to start render'; rendering = false; return; }
      await new Promise((done) => {
        _job = jobStream(data.id, (ev) => {
          if (ev.type === 'cell_progress') renderPct = ev.max ? Math.round((ev.value / ev.max) * 100) : null;
          else if (ev.type === 'cell_image') renderImg = (ev.images || [])[0] || null;
          else if (ev.type === 'cell_error') renderErr = ev.error;
        }, done);
      });
    } catch (e) { renderErr = String(e); }
    rendering = false;
  }
</script>

<section class="card">
  <h3>Test a stack</h3>
  <div class="trow">
    <div class="tf"><label>Stack</label><Combobox items={stackItems} value={testStack} placeholder="stack…" onpick={(v) => (testStack = v)} /></div>
    <div class="tf"><label>Theme (optional)</label><Combobox items={themeItems} value={testTheme} placeholder="— none —" onpick={(v) => (testTheme = v)} /></div>
    <div class="tf"><label>Workflow</label><Combobox items={imgModelItems} value={testModel} placeholder="workflow…" onpick={(v) => (testModel = v)} /></div>
  </div>
  <label>Scene <span class="lo">— natural language; the Prompt Gen LLM turns it into tags</span></label>
  <div class="scenerow">
    <input bind:value={sceneNL} placeholder="a knight in gleaming armor under moonlight, looking tense" />
    <button class="ghost" onclick={tagify} disabled={tagifying || !sceneNL.trim()}>{tagifying ? 'Generating…' : 'Scene → tags'}</button>
  </div>
  <label>Prompt / tags <span class="lo">— shared with the test grid above; routing matches it and the image renders it</span></label>
  <textarea class="tp" rows="3" bind:value={img.testPrompt} placeholder="1girl, armor, moonlight, … (or generate from a scene above)"></textarea>
  <div class="trow2">
    <button class="ghost" onclick={resolve} disabled={!testStack}>Resolve</button>
    <button onclick={render} disabled={!resolved || rendering}>{rendering ? 'Rendering…' : 'Render'}</button>
    {#if renderErr}<span class="err">{renderErr}</span>{/if}
  </div>

  {#if resolved}
    {#if resolved.routed?.length}
      <div class="routing">
        <div class="rlbl">State routing</div>
        {#each resolved.routed as r}
          <div class="rrow" class:on={r.fired}>
            <span class="rdot"></span>
            <span class="rname">{r.name}</span>
            <span class="rscore">{r.score} / {r.threshold}</span>
            <span class="rwhy">{r.matched?.length ? r.matched.join(', ') : (r.why || '—')}</span>
          </div>
        {/each}
      </div>
    {/if}
    <div class="resolved">
      {#each resolved.loras as l}<span class="chip {l.type}" title={l.why}>{l.type}: {l.name} @{l.weight}</span>{/each}
      {#if !resolved.loras.length}<span class="m">empty stack — nothing resolved</span>{/if}
    </div>
  {/if}
  {#if rendering || renderImg}
    <div class="rout">
      {#if rendering}<ProgressBar value={renderPct} max={100} height="9px" margin="0 0 10px" />{/if}
      <div class="render-slot">
        <ImgCard src={renderImg} caption={img.testPrompt} onRegen={resolved ? render : null} busy={rendering} />
      </div>
    </div>
  {/if}
</section>

<style>
  .card { margin-bottom: 16px; }
  .card h3 { margin: 0 0 12px; font-size: 15px; }
  .lo { color: var(--faint); text-transform: none; letter-spacing: 0; }
  .trow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 10px; }
  .tf label, .card > label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }
  .card > .tp { width: 100%; resize: vertical; font: inherit; line-height: 1.45; margin-bottom: 2px; }
  .scenerow { display: flex; gap: 10px; align-items: center; }
  .scenerow input { flex: 1; min-width: 0; }
  .trow2 { display: flex; gap: 10px; align-items: center; margin-top: 10px; }
  .m { font-size: 12.5px; color: var(--muted); }
  .err { font-size: 12.5px; }
  .resolved { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .chip { font-size: 11.5px; border-radius: 999px; padding: 2px 9px; border: 1px solid var(--border-soft); background: var(--elev); color: var(--muted); }
  .chip.detail { color: #8fcaff; } .chip.theme { color: #c9a6ff; } .chip.identity { color: var(--good); } .chip.state { color: var(--accent); }
  .routing { margin-top: 12px; border: 1px solid var(--border-soft); border-radius: 9px; padding: 8px 10px; background: var(--elev); }
  .rlbl { font-size: 10.5px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); margin-bottom: 6px; }
  .rrow { display: grid; grid-template-columns: 12px 1.4fr 80px 2fr; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); padding: 2px 0; }
  .rdot { width: 8px; height: 8px; border-radius: 50%; background: var(--border); }
  .rrow.on .rdot { background: var(--accent); box-shadow: 0 0 7px var(--accent-glow); }
  .rrow.on .rname { color: var(--text); }
  .rscore { font-family: ui-monospace, monospace; font-size: 11px; }
  .rwhy { color: var(--faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rout { margin-top: 14px; }
  .render-slot { width: 100%; max-width: 512px; aspect-ratio: 1; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); }
</style>

<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import Combobox from '$lib/components/Combobox.svelte';

  // Trains a LoRA from a saved dataset using kohya sd-scripts. The trainer venv
  // itself is installed/repaired in Settings → Trainer; this tab just trains.
  let trainer = $state(null);
  let datasets = $state([]);
  let scan = $state({ items: [] });   // signature-classified model index

  let dataset = $state('');
  let baseModel = $state('');         // checkpoint rel within checkpoints/
  let outputName = $state('');
  let p = $state(null);               // hyperparams (seeded from trainer.defaults)

  // run state
  let busy = $state(false);
  let cancelling = $state(false);
  let step = $state(0);
  let total = $state(0);
  let log = $state([]);
  let jobStatus = $state(null);       // done | cancelled | error
  let artifact = $state(null);
  let startMsg = $state(null);
  let logEl;

  let dsItems = $derived(datasets.map((d) => ({ value: d.name, label: `${d.name} — ${d.count} img${d.captions < d.count ? ` (${d.captions} captioned)` : ''}` })));
  // One base picker: trainable full checkpoints (kohya, run natively) AND Anima
  // DiT models (config-gen for the external Anima trainer). The base's detected
  // architecture decides the mode, so it's one unified form.
  let bases = $derived([
    ...(scan.items || []).filter((i) => i.kind === 'checkpoint').map((i) => ({ kind: 'checkpoint', rel: i.rel, arch: i.arch })),
    ...(scan.items || []).filter((i) => i.kind === 'diffusion' && i.arch === 'dit').map((i) => ({ kind: 'diffusion', rel: i.rel, arch: 'dit' }))
  ]);
  let baseItems = $derived(bases.map((b) => ({ value: b.rel, label: `${b.rel}  ·  ${b.arch}` })));
  let sel = $derived(bases.find((b) => b.rel === baseModel) || null);
  let baseArch = $derived(sel?.arch || '');
  let mode = $derived(baseArch === 'sdxl' || baseArch === 'sd15' ? 'kohya' : baseArch === 'dit' ? 'anima' : null);
  let curDs = $derived(datasets.find((d) => d.name === dataset));
  let pct = $derived(total ? Math.round((step / total) * 100) : null);

  // Anima (DiT) extras — it loads a separate text encoder + VAE.
  let teItems = $derived((scan.items || []).filter((i) => i.kind === 'clip').map((i) => ({ value: i.rel, label: i.rel })));
  let vaeItems = $derived((scan.items || []).filter((i) => i.kind === 'vae').map((i) => ({ value: i.rel, label: i.rel })));
  let animaTE = $state('qwen_3_06b_base.safetensors');
  let animaVae = $state('qwen_image_vae.safetensors');
  let animaGen = $state(false);
  let animaResult = $state(null);

  async function genAnima() {
    if (animaGen || !p) return;
    animaGen = true; animaResult = null;
    const r = await post('/train/anima-config', {
      dataset, output_name: outputName, dit: baseModel, text_encoder: animaTE, vae: animaVae,
      network_dim: +p.network_dim, network_alpha: +p.network_alpha,
      learning_rate: +p.learning_rate, text_encoder_lr: +p.text_encoder_lr,
      optimizer_type: p.optimizer, lr_scheduler: p.lr_scheduler,
      max_train_epochs: +p.max_train_epochs, save_every_n_epochs: +p.save_every_n_epochs,
      train_batch_size: +p.train_batch_size, resolution: +p.resolution, num_repeats: +p.num_repeats
    });
    animaResult = r.data?.ok ? r.data : { error: r.data?.error || 'failed' };
    animaGen = false;
  }

  async function loadTrainer() {
    trainer = await get('/trainer');
    if (!p) p = { ...trainer.defaults };
  }

  onMount(async () => {
    await loadTrainer();
    datasets = (await get('/lora/datasets')).datasets || [];
    try { scan = await get('/comfy/models'); } catch { /* comfy off */ }
    if (!dataset && datasets[0]) dataset = datasets[0].name;
    // Reattach only to an actual training run — not a trainer-setup job.
    const j = await get('/train/job');
    if (j && j.status === 'running' && j.kind !== 'Trainer setup') attach();
  });

  $effect(() => { if (dataset && !outputName) outputName = dataset; });
  // Architecture drives the training mode — no more manually guessing the flag.
  $effect(() => { if (p && baseArch) p.sdxl = baseArch === 'sdxl'; });

  function gotoSettings() { app.nav.screen = 'settings'; }

  async function startTrain() {
    startMsg = null;
    const r = await post('/train/start', { dataset, base_model: baseModel, output_name: outputName, ...p });
    if (r.status === 409) { startMsg = { err: true, text: 'a run is already going' }; attach(); return; }
    if (r.data?.error) { startMsg = { err: true, text: r.data.error }; return; }
    log = []; jobStatus = null; artifact = null; step = 0; total = 0;
    attach();
  }

  async function attach() {
    busy = true; cancelling = false;
    try {
      const res = await fetch('/api/train/stream');
      if (res.status === 204 || !res.body) { busy = false; return; }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2);
          if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'log') {
            log = [...log.slice(-400), ev.line];
            queueMicrotask(() => logEl && (logEl.scrollTop = logEl.scrollHeight));
          } else if (ev.type === 'progress') { step = ev.done; total = ev.total; }
          else if (ev.type === 'done') { jobStatus = ev.status; artifact = ev.artifact; }
        }
      }
    } catch (e) { startMsg = { err: true, text: String(e) }; }
    busy = false; cancelling = false;
  }

  async function cancelTrain() { cancelling = true; await post('/train/cancel'); }

  const NUMS = [
    ['max_train_epochs', 'Epochs'], ['learning_rate', 'Learning rate'],
    ['network_dim', 'Network dim'], ['network_alpha', 'Network alpha'],
    ['resolution', 'Resolution'], ['train_batch_size', 'Batch size'],
    ['num_repeats', 'Repeats / image'], ['save_every_n_epochs', 'Save every N epochs']
  ];
</script>

{#if !trainer}
  <div class="hint">Loading…</div>
{:else if !trainer.installed}
  <div class="setup">
    <div class="warn">⚙ Trainer not ready</div>
    {#if trainer.issues?.length}
      <ul class="issues">{#each trainer.issues as it}<li>{it}</li>{/each}</ul>
    {/if}
    <p class="hint">Set it up (or repair it) in <button class="linkbtn" onclick={gotoSettings}>Settings → Trainer</button>, then come back here to train.</p>
  </div>
{:else}
  <div class="hint">Trains a LoRA with kohya sd-scripts. Output goes to <code>{trainer.loras_out_dir}</code> (your ComfyUI LoRA folder, so it's instantly usable). Manage the trainer install in <button class="linkbtn" onclick={gotoSettings}>Settings</button>.</div>

  <div class="grid2" style="margin-top:14px">
    <div>
      <label>Dataset</label>
      <Combobox items={dsItems} value={dataset} placeholder="saved image set…" onpick={(v) => (dataset = v)} />
      {#if curDs && curDs.captions < curDs.count}<div class="warnsm">⚠ {curDs.count - curDs.captions} image(s) have no caption .txt — they'll train uncaptioned.</div>{/if}
    </div>
    <div>
      <label>Base model <span class="lo">— checkpoint, or an Anima DiT</span></label>
      <Combobox items={baseItems} value={baseModel} placeholder="model to train on…" onpick={(v) => (baseModel = v)} />
      {#if mode === 'kohya'}
        <div class="archnote">architecture <strong>{baseArch}</strong> → trains here with kohya as {baseArch === 'sdxl' ? 'SDXL' : 'SD 1.5'}</div>
      {:else if mode === 'anima'}
        <div class="archnote">architecture <strong>dit</strong> → Anima; Loom writes the config for the <a href="https://github.com/gazingstars123/Anima-Standalone-Trainer" target="_blank" rel="noreferrer">native Anima trainer</a> (run it there)</div>
      {:else if baseModel}
        <div class="warnsm">⚠ <strong>{baseArch || 'unknown'}</strong> isn't trainable (kohya does SDXL/SD 1.5; Anima is DiT).</div>
      {:else if !bases.length}
        <div class="warnsm">No trainable models found{scan.items?.length ? '' : ' — is ComfyUI installed?'}.</div>
      {/if}
    </div>
  </div>

  {#if mode === 'anima'}
    <div class="grid2" style="margin-top:10px">
      <div><label>Text encoder (Qwen)</label><Combobox items={teItems} value={animaTE} placeholder="qwen…" onpick={(v) => (animaTE = v)} /></div>
      <div><label>VAE</label><Combobox items={vaeItems} value={animaVae} placeholder="vae…" onpick={(v) => (animaVae = v)} /></div>
    </div>
  {/if}

  <label>Output LoRA name</label>
  <input bind:value={outputName} placeholder="my-style-lora" />

  <div class="params">
    {#each NUMS as [key, lbl] (key)}
      <div class="pf"><label>{lbl}</label><input type="number" step="any" bind:value={p[key]} /></div>
    {/each}
    <div class="pf"><label>Optimizer</label><input bind:value={p.optimizer} /></div>
    <div class="pf"><label>LR scheduler</label><input bind:value={p.lr_scheduler} /></div>
  </div>

  <div class="row" style="margin-top:16px; align-items:center; gap:10px">
    {#if mode === 'anima'}
      <button onclick={genAnima} disabled={animaGen || !dataset || !baseModel || !outputName}>{animaGen ? 'Generating…' : 'Generate Anima config'}</button>
      {#if animaResult?.error}<span class="err">{animaResult.error}</span>{/if}
    {:else}
      <button onclick={startTrain} disabled={busy || !dataset || !baseModel || !outputName || mode !== 'kohya'}>Start training</button>
      {#if busy}<button class="ghost sm" onclick={cancelTrain} disabled={cancelling}>{cancelling ? 'Cancelling…' : 'Cancel'}</button>{/if}
      {#if jobStatus === 'done'}<span class="ok">✓ done{artifact ? ` — saved ${artifact}` : ''}</span>
      {:else if jobStatus === 'cancelled'}<span class="err">cancelled</span>
      {:else if jobStatus === 'error'}<span class="err">errored — see log</span>{/if}
    {/if}
  </div>
  {#if startMsg}<div class:err={startMsg.err} class="msg">{startMsg.text}</div>{/if}

  {#if mode === 'anima' && animaResult?.ok}
    <div class="amsg">✓ wrote <code>{animaResult.config_path}</code> — run it in your Anima trainer (its own venv):</div>
    <pre class="acmd">{animaResult.command}</pre>
    <details><summary>view config</summary><pre class="acmd">{animaResult.config_text}</pre></details>
  {/if}
{/if}

{#if busy || log.length}
  <div class="run">
    <div class="prow">
      <div class="bar" class:indet={pct === null}><span style={pct !== null ? `width:${pct}%` : ''}></span></div>
      <div class="pmeta">{pct !== null ? `${pct}% · step ${step}/${total}` : (busy ? 'working…' : '')}</div>
    </div>
    <pre class="log" bind:this={logEl}>{log.join('\n')}</pre>
  </div>
{/if}

<style>
  .hint { font-size: 12.5px; color: var(--muted); }
  .hint code, code { background: var(--elev); padding: 1px 5px; border-radius: 5px; font-size: 12px; }
  .lo { color: var(--faint); }
  .row { display: flex; gap: 10px; align-items: center; }
  .ok { color: var(--good); font-size: 12.5px; } .err { color: var(--bad); }
  .msg { font-size: 12.5px; margin-top: 8px; }

  .setup { max-width: 580px; }
  .warn { color: var(--bad); font-weight: 600; margin-bottom: 8px; }
  .issues { margin: 0 0 12px; padding-left: 18px; }
  .issues li { font-size: 12.5px; color: var(--bad); margin: 2px 0; }
  .linkbtn { background: none; box-shadow: none; padding: 0; color: var(--accent); font: inherit; text-decoration: underline; }
  .linkbtn:hover { filter: brightness(1.2); }

  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .warnsm { font-size: 11.5px; color: var(--warn, #e6b800); margin-top: 5px; }
  .archnote { font-size: 11.5px; color: var(--muted); margin-top: 5px; }
  .archnote strong { color: #8fcaff; }

  .params { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px 12px; margin-top: 14px; }
  .pf { min-width: 0; }
  .pf label { display: block; font-size: 11px; color: var(--muted); margin: 0 0 4px; }

  .run { margin-top: 16px; }
  .prow { margin-bottom: 8px; }
  .bar { height: 9px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .bar.indet span { width: 30%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
  .pmeta { margin-top: 6px; font-size: 12px; color: var(--muted); }
  .log {
    height: 280px; overflow: auto; background: #0b0e14; border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 12px; font: 11.5px/1.5 ui-monospace, monospace;
    color: #b9c2d0; white-space: pre-wrap; word-break: break-word; margin: 0;
  }

  .archnote a { color: var(--accent); }
  .amsg { font-size: 12.5px; color: var(--good); margin-top: 10px; }
  .amsg code { background: var(--elev); padding: 1px 5px; border-radius: 5px; }
  .acmd {
    background: #0b0e14; border: 1px solid var(--border); border-radius: 8px; padding: 9px 11px;
    font: 11.5px/1.5 ui-monospace, monospace; color: #b9c2d0; white-space: pre-wrap; word-break: break-word; margin: 4px 0 0;
  }
  details summary { font-size: 12px; color: var(--muted); cursor: pointer; margin-top: 8px; }
</style>

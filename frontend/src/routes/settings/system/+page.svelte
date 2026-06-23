<script>
  import { onMount } from 'svelte';
  import { get, post, put } from '$lib/api.js';
  import { app, refreshHealth, refreshFlags, setAllowNsfw, setAppFlag } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';

  // ── System / Backend ────────────────────────────────────────────
  let srv = $state(null);
  let restarting = $state(false);
  let profile = $derived(app.health?.profile || {});

  async function loadSrv() { try { srv = await get('/server/info'); } catch { srv = null; } }
  function fmtUptime(s) {
    if (s == null) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
  }
  async function restartServer() {
    if (!await askConfirm({ title: 'Restart the backend?', message: 'Re-reads config (new workflows/models) and interrupts any running training or generation. Managed ComfyUI keeps running.', confirmLabel: 'Restart' })) return;
    restarting = true;
    try { await post('/server/restart'); } catch { /* drops as it re-execs */ }
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { const r = await fetch('/api/health'); if (r.ok) { location.reload(); return; } } catch { /* still down */ }
    }
    restarting = false;
  }

  // ── ComfyUI ─────────────────────────────────────────────────────
  let comfy = $derived(app.health?.comfyui || {});
  let starting = $state(false);
  async function launchComfy() {
    if (comfy.up || starting) return;
    starting = true; await post('/comfy/up'); starting = false; await refreshHealth();
  }

  // ── Trainer ─────────────────────────────────────────────────────
  let gpu = $state(null);
  let trainer = $state(null);
  let tcfg = $state({ sd_scripts_dir: '', python: '', checkpoints_dir: '', loras_dir: '' });
  let tmsg = $state(null);
  let tcfgSnap = $state(null);
  let tTimer = null;

  let cuda = $state('auto');
  let sbusy = $state(false);
  let scancelling = $state(false);
  let sstep = $state(0);
  let stotal = $state(0);
  let slog = $state([]);
  let sstatus = $state(null);
  let smsg = $state(null);
  let slogEl;
  let spct = $derived(stotal ? Math.round((sstep / stotal) * 100) : null);

  async function loadTrainer() {
    trainer = await get('/trainer');
    tcfg = {
      sd_scripts_dir: trainer.sd_scripts_dir || '',
      python: trainer.python || '',
      checkpoints_dir: trainer.checkpoints_dir || '',
      loras_dir: trainer.loras_dir || ''
    };
    tcfgSnap = JSON.stringify(tcfg);
  }

  $effect(() => {
    const cur = JSON.stringify(tcfg);
    if (tcfgSnap === null || cur === tcfgSnap) return;
    clearTimeout(tTimer);
    tTimer = setTimeout(saveTrainer, 700);
  });

  async function saveTrainer() {
    tmsg = { text: 'saving…' };
    await post('/trainer', tcfg);
    await loadTrainer();
    tmsg = { ok: true, text: trainer.installed ? '✓ saved — trainer ready' : '✓ saved' };
  }

  async function repair() {
    smsg = null; slog = []; sstatus = null; sstep = 0; stotal = 0;
    const r = await post('/trainer/setup', { cuda });
    if (r.status === 409) { attachSetup(); return; }
    if (r.data?.error) { smsg = { err: true, text: r.data.error }; return; }
    attachSetup();
  }

  async function attachSetup() {
    sbusy = true; scancelling = false;
    try {
      const res = await fetch('/api/train/stream');
      if (res.status === 204 || !res.body) { sbusy = false; return; }
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
            slog = [...slog.slice(-400), ev.line];
            queueMicrotask(() => slogEl && (slogEl.scrollTop = slogEl.scrollHeight));
          } else if (ev.type === 'progress') { sstep = ev.done; stotal = ev.total; }
          else if (ev.type === 'done') { sstatus = ev.status; }
        }
      }
    } catch (e) { smsg = { err: true, text: String(e) }; }
    sbusy = false; scancelling = false;
    await loadTrainer();
  }

  async function cancelSetup() { scancelling = true; await post('/train/cancel'); }

  // RunPod inference — per-image-model local⇄serverless toggle.
  let rpModels = $state({ models: [], configured: false, endpoint_id: '' });
  async function loadRpModels() { try { rpModels = await get('/runpod-models'); } catch {} }
  async function toggleRp(key, on) {
    try { await put('/runpod-models', { key, runpod: on }); } catch {}
    const m = rpModels.models.find((x) => x.key === key); if (m) m.runpod = on;
  }

  onMount(async () => {
    loadSrv();
    loadRpModels();
    await refreshFlags();
    gpu = await get('/trainer/detect');
    await loadTrainer();
    const j = await get('/train/job');
    if (j && j.status === 'running' && j.kind === 'Trainer setup') attachSetup();
  });
</script>

<div class="screen">

  <!-- Content gate -->
  <section class="card">
    <div class="card-head">
      <h3>Content</h3>
      <span class="card-sub">global rating gate</span>
    </div>
    <label class="nsfwrow">
      <input type="checkbox" checked={app.allowNsfw} onchange={(e) => setAllowNsfw(e.currentTarget.checked)} />
      <span class="nsfwlabel">Allow NSFW content
        <span class="nsfwsub">When off, nsfw-rated lorebooks are excluded from retrieval everywhere.</span>
      </span>
    </label>
  </section>

  <!-- Image generation -->
  <section class="card">
    <div class="card-head">
      <h3>Image generation</h3>
      <span class="card-sub">render pipeline steps</span>
    </div>
    <label class="nsfwrow">
      <input type="checkbox" checked={app.imgDetailer} onchange={(e) => setAppFlag('imgDetailer', e.currentTarget.checked)} />
      <span class="nsfwlabel">Detailer (ADetailer)
        <span class="nsfwsub">Face/detail refinement pass. The usual need; off skips it for faster renders.</span>
      </span>
    </label>
    <label class="nsfwrow">
      <input type="checkbox" checked={app.imgUpscale} onchange={(e) => setAppFlag('imgUpscale', e.currentTarget.checked)} />
      <span class="nsfwlabel">4K upscale
        <span class="nsfwsub">Heavy hi-res + Ultimate-SD upscale chain. Off by default — most renders don't need it.</span>
      </span>
    </label>
  </section>

  <!-- RunPod inference -->
  <section class="card">
    <div class="card-head">
      <h3>RunPod inference</h3>
      <span class="card-sub">{rpModels.configured ? `endpoint ${rpModels.endpoint_id}` : 'not configured (set RUNPOD_ENDPOINT_ID + RUNPOD_API_KEY)'}</span>
    </div>
    <p class="card-sub" style="margin:0 0 10px">Run a workflow on the RunPod serverless GPU instead of local ComfyUI. Per-model — flip heavy graphs (e.g. Wan 14B) to the cloud, keep light ones local.</p>
    {#each rpModels.models as m (m.key)}
      <label class="nsfwrow">
        <input type="checkbox" checked={m.runpod} disabled={!rpModels.configured} onchange={(e) => toggleRp(m.key, e.currentTarget.checked)} />
        <span class="nsfwlabel">{m.name}
          <span class="nsfwsub">{m.runpod ? '☁ runs on RunPod serverless' : '🖥 runs on local ComfyUI'}</span>
        </span>
      </label>
    {/each}
    {#if !rpModels.models.length}<p class="card-sub">No image models registered.</p>{/if}
  </section>

  <!-- Row 1: Environment + Backend -->
  <div class="row2">
    <section class="card">
      <div class="card-head">
        <h3>Environment</h3>
        <span class="card-sub">from <code>user.yaml</code></span>
      </div>
      <div class="rows">
        <div class="row"><span>Profile</span><b>{profile.name || '—'}</b></div>
        {#if profile.email}<div class="row"><span>Email</span><b>{profile.email}</b></div>{/if}
        <div class="row"><span>GPU</span><b title={gpu?.gpu}>{gpu?.gpu || 'not detected'}</b></div>
        <div class="row"><span>CUDA</span><b>{gpu?.cuda || '—'}{gpu?.compute_cap ? ` · sm_${gpu.compute_cap}` : ''}</b></div>
      </div>
    </section>

    <section class="card">
      <div class="card-head">
        <h3>Backend</h3>
        <span class="dot {restarting ? 'busy' : 'up'}"></span>
        <span class="status-lbl">{restarting ? 'restarting…' : 'running'}</span>
      </div>
      <div class="rows">
        <div class="row"><span>Uptime</span><b>{fmtUptime(srv?.uptime_s)}</b></div>
        <div class="row"><span>Python</span><b class="mono">{srv?.python || '—'}</b></div>
        <div class="row"><span>PID</span><b class="mono">{srv?.pid || '—'}</b></div>
      </div>
      <div class="card-foot">
        <button onclick={restartServer} disabled={restarting}>
          {restarting ? 'Restarting…' : 'Restart server'}
        </button>
        <span class="foot-note">Re-reads config · interrupts generation · ComfyUI keeps running</span>
      </div>
    </section>
  </div>

  <!-- Row 2: ComfyUI -->
  <section class="card">
    <div class="card-head">
      <h3>ComfyUI</h3>
      <span class="slight {comfy.up ? 'up' : 'down'}"></span>
      <span class="status-lbl">{comfy.up ? 'live' : 'off'}</span>
      {#if !comfy.up}
        <button class="ghost sm" onclick={launchComfy} disabled={starting}>{starting ? 'starting…' : 'launch'}</button>
      {/if}
    </div>
    <div class="comfy-grid">
      <div class="kv"><span>Server</span><b class="mono" title={comfy.base_url}>{comfy.base_url || '—'}</b></div>
      <div class="kv"><span>Mode</span><b class="muted">{comfy.managed ? 'managed (auto-launch)' : 'connect-only'}</b></div>
    </div>
    <p class="hint">Edit <code>comfyui.base_url</code> and <code>comfyui.managed</code> in <code>user.yaml</code>, then restart the backend above.</p>
  </section>

  <!-- Row 3: Trainer -->
  <section class="card">
    <div class="card-head">
      <h3>Trainer</h3>
      {#if trainer}
        {#if trainer.installed}<span class="pill ok">ready</span>{:else}<span class="pill bad">not ready</span>{/if}
      {/if}
      {#if tmsg}<span class="status-lbl" class:ok={tmsg.ok}>{tmsg.text}</span>{/if}
    </div>

    <div class="trainer-grid">
      <!-- Left: install -->
      <div class="tcol">
        <label>CUDA build</label>
        <select bind:value={cuda}>
          <option value="auto">Auto{gpu?.cuda ? ` → ${gpu.cuda}` : ''}</option>
          <option value="cu128">cu128 (Blackwell / RTX 50-series)</option>
          <option value="cu124">cu124 (RTX 20–40 series)</option>
          <option value="cu121">cu121 (older)</option>
        </select>
        {#if trainer && !trainer.installed && trainer.issues?.length}
          <div class="note">{trainer.issues[0]}</div>
        {/if}
        <div class="actions">
          <button onclick={repair} disabled={sbusy}>{sbusy ? 'Setting up…' : (trainer?.installed ? 'Reinstall' : 'Install trainer')}</button>
          {#if sbusy}<button class="ghost sm" onclick={cancelSetup} disabled={scancelling}>{scancelling ? 'Cancelling…' : 'Cancel'}</button>{/if}
        </div>
        {#if smsg}<div class:err={smsg.err} class="msg">{smsg.text}</div>{/if}
      </div>

      <!-- Right: paths -->
      <div class="tcol">
        <label>sd-scripts folder</label>
        <input bind:value={tcfg.sd_scripts_dir} placeholder="C:\…\sd-scripts" />

        <label>venv python <span class="dim">(blank = autodetect)</span></label>
        <input bind:value={tcfg.python} placeholder="…\venv\Scripts\python.exe" />

        <label>Checkpoints <span class="dim">(blank = ComfyUI's)</span></label>
        <input bind:value={tcfg.checkpoints_dir} placeholder={trainer?.checkpoints_dir_resolved || 'auto'} />

        <label>LoRA output <span class="dim">(blank = ComfyUI's)</span></label>
        <input bind:value={tcfg.loras_dir} placeholder={trainer?.loras_out_dir || 'auto'} />
      </div>
    </div>

    {#if sbusy || slog.length}
      <div class="run">
        <div class="prow">
          <div class="bar" class:indet={spct === null}><span style={spct !== null ? `width:${spct}%` : ''}></span></div>
          <div class="pmeta">{spct !== null ? `${spct}%` : (sbusy ? 'working…' : (sstatus || ''))}</div>
        </div>
        <pre class="log" bind:this={slogEl}>{slog.join('\n')}</pre>
      </div>
    {/if}
  </section>

</div>

<style>
  .screen {
    flex: 1; min-height: 0; overflow-y: auto;
    display: flex; flex-direction: column; gap: 16px;
    padding: 18px 20px 18px 4px;
  }

  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

  .card {
    background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg); padding: 20px 22px;
    display: flex; flex-direction: column; gap: 0;
    min-width: 0;
  }

  .card-head {
    display: flex; align-items: center; gap: 9px;
    padding-bottom: 14px; border-bottom: 1px solid var(--border-soft);
    margin-bottom: 4px;
  }
  h3 { margin: 0; font-size: 14px; font-weight: 660; color: var(--text); }
  .card-sub { font-size: 11.5px; color: var(--faint, var(--muted)); margin-left: auto; }
  .card-sub code {
    background: var(--elev); padding: 1px 5px; border-radius: 4px;
    font-size: 11px; color: var(--muted);
  }

  /* status indicators */
  .dot, .slight {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    flex: none; box-shadow: 0 0 7px currentColor;
  }
  .dot { margin-left: auto; }
  .slight { width: 9px; height: 9px; }
  .dot.up, .slight.up   { background: var(--good); color: var(--good); }
  .dot.busy             { background: var(--warn); color: var(--warn); animation: pulse 1.2s ease-in-out infinite; }
  .slight.down          { background: var(--bad); color: var(--bad); }
  .status-lbl { font-size: 12px; color: var(--muted); }
  .status-lbl.ok { color: var(--good); }
  @keyframes pulse { 50% { opacity: .3; } }

  /* key-value rows (Environment / Backend) */
  .rows { display: flex; flex-direction: column; gap: 0; padding: 4px 0; }
  .row {
    display: flex; align-items: baseline; gap: 12px;
    padding: 9px 0; border-bottom: 1px solid var(--border-soft);
  }
  .row:last-child { border-bottom: none; }
  .row span { font-size: 12px; color: var(--muted); width: 62px; flex: none; letter-spacing: .01em; }
  .row b { font-size: 13px; font-weight: 550; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 450; }

  /* Backend card footer */
  .card-foot {
    display: flex; align-items: center; gap: 14px;
    margin-top: 18px; padding-top: 16px;
    border-top: 1px solid var(--border-soft);
  }
  .foot-note { font-size: 11.5px; color: var(--faint, var(--muted)); line-height: 1.5; flex: 1; }

  /* ComfyUI grid */
  .comfy-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-top: 14px; }
  .kv { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
  .kv span { font-size: 12.5px; color: var(--muted); width: 52px; flex: none; }
  .kv b { font-size: 13px; font-weight: 560; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .muted { color: var(--muted); font-weight: 400; }

  /* Trainer */
  .pill { font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 999px; }
  .pill.ok  { background: rgba(87, 217, 163, .14); color: var(--good); }
  .pill.bad { background: rgba(255, 122, 122, .14); color: var(--bad); }
  .note { font-size: 12.5px; color: var(--bad); margin: 8px 0; line-height: 1.5; }

  .trainer-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 32px; margin-top: 4px; }
  .tcol { display: flex; flex-direction: column; }

  label { display: block; font-size: 12px; color: var(--muted); margin: 12px 0 4px; }
  .dim { color: var(--faint, var(--muted)); }
  input { width: 100%; }
  select { width: 100%; padding: 8px 10px; border-radius: 8px; background: var(--elev); color: var(--text); border: 1px solid var(--border); }
  .actions { display: flex; align-items: center; gap: 10px; margin-top: 12px; }

  .run { margin-top: 16px; }
  .prow { margin-bottom: 8px; }
  .bar { height: 9px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .bar.indet span { width: 30%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
  .pmeta { font-size: 12px; color: var(--muted); }
  .log { height: 180px; overflow: auto; background: #0b0e14; border: 1px solid var(--border); border-radius: 8px;
         padding: 10px 12px; font: 11.5px/1.5 ui-monospace, monospace; color: #b9c2d0; white-space: pre-wrap; word-break: break-word; margin: 0; }

  /* shared */
  .hint { font-size: 12px; color: var(--muted); margin: 10px 0 0; line-height: 1.55; }
  .hint.mt0 { margin-top: 6px; }
  .hint code, code { background: var(--elev); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
  .msg { font-size: 12.5px; margin-top: 4px; }
  .ok  { color: var(--good); }
  .err { color: var(--bad); }
  .nsfwrow { display: flex; align-items: flex-start; gap: 10px; cursor: pointer; }
  .nsfwrow input { width: 16px; height: 16px; margin-top: 2px; flex: none; }
  .nsfwlabel { display: flex; flex-direction: column; gap: 2px; font-size: 13px; color: var(--text); }
  .nsfwsub { font-size: 11.5px; color: var(--muted); }
</style>

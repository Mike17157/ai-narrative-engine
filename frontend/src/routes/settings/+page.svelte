<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app, refreshHealth } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';

  // The configuration pane: system/environment info, ComfyUI, and the trainer —
  // including install/repair (which lives here, not in the Train tab).
  let trainer = $state(null);
  let gpu = $state(null);
  let starting = $state(false);     // comfy launch

  // editable trainer config (the /api/trainer endpoint persists these)
  let tcfg = $state({ sd_scripts_dir: '', python: '', checkpoints_dir: '', loras_dir: '' });
  let tmsg = $state(null);
  let tcfgSnap = $state(null);   // auto-save baseline
  let tTimer = null;

  // trainer install/repair (runs as a job, streams logs)
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

  let profile = $derived(app.health?.profile || {});
  let comfy = $derived(app.health?.comfyui || {});

  // --- backend server management ---
  let srv = $state(null);
  let restarting = $state(false);
  async function loadSrv() { try { srv = await get('/server/info'); } catch { srv = null; } }
  function fmtUptime(s) {
    if (s == null) return '—';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}h ${m}m` : m ? `${m}m ${sec}s` : `${sec}s`;
  }
  async function restartServer() {
    if (!await askConfirm({ title: 'Restart the backend?', message: 'Re-reads config (new workflows/models) and interrupts any running training or generation. Managed ComfyUI keeps running.', confirmLabel: 'Restart' })) return;
    restarting = true;
    try { await post('/server/restart'); } catch { /* connection drops as it re-execs */ }
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { const r = await fetch('/api/health'); if (r.ok) { location.reload(); return; } } catch { /* still down */ }
    }
    restarting = false; // gave up waiting
  }

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

  // Auto-save trainer paths (debounced).
  $effect(() => {
    const cur = JSON.stringify(tcfg);
    if (tcfgSnap === null || cur === tcfgSnap) return;
    clearTimeout(tTimer);
    tTimer = setTimeout(saveTrainer, 700);
  });

  onMount(async () => {
    loadSrv();
    gpu = await get('/trainer/detect');
    await loadTrainer();
    const j = await get('/train/job');
    if (j && j.status === 'running' && j.kind === 'Trainer setup') attachSetup();
  });

  async function launchComfy() {
    if (comfy.up || starting) return;
    starting = true; await post('/comfy/up'); starting = false; await refreshHealth();
  }

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
    await loadTrainer();   // flips the status pill once the venv is rebuilt
  }

  async function cancelSetup() { scancelling = true; await post('/train/cancel'); }
</script>

<div class="page">
<div class="wrap">
  <h2>Settings</h2>

  <section class="card">
    <h3>System</h3>
    <div class="grid">
      <div class="kv"><span>Profile</span><b>{profile.name || '—'}</b></div>
      {#if profile.email}<div class="kv"><span>Email</span><b>{profile.email}</b></div>{/if}
      <div class="kv"><span>GPU</span><b>{gpu?.gpu || 'not detected'}</b></div>
      <div class="kv"><span>CUDA</span><b>{gpu?.cuda || '—'}{gpu?.compute_cap ? ` · sm ${gpu.compute_cap}` : ''}</b></div>
    </div>
    <p class="hint">Profile is set in <code>user.yaml</code>. GPU/CUDA are auto-detected.</p>
  </section>

  <section class="card">
    <h3>ComfyUI</h3>
    <div class="grid">
      <div class="kv"><span>Status</span>
        <b><span class="slight {comfy.up ? 'up' : 'down'}"></span>{#if comfy.up}live{:else}off <button class="lnk" onclick={launchComfy} disabled={starting}>{starting ? 'starting…' : 'launch'}</button>{/if}</b>
      </div>
      <div class="kv"><span>Server</span><b class="mono" title={comfy.base_url}>{comfy.base_url || '—'}</b></div>
      <div class="kv"><span>Mode</span><b class="muted">{comfy.managed ? 'managed (auto-launch)' : 'connect-only'}</b></div>
    </div>
    <p class="hint">Server URL is configured in <code>user.yaml</code> (or per-run in Images → Connection).</p>
  </section>

  <section class="card">
    <div class="chead">
      <h3>Backend server</h3>
      <span class="slight {restarting ? 'busy' : 'up'}"></span>
      <span class="slbl">{restarting ? 'restarting…' : 'running'}</span>
    </div>
    <div class="grid">
      <div class="kv"><span>Uptime</span><b>{fmtUptime(srv?.uptime_s)}</b></div>
      <div class="kv"><span>Python</span><b class="mono">{srv?.python || '—'}</b></div>
      <div class="kv"><span>PID</span><b class="mono">{srv?.pid || '—'}</b></div>
    </div>
    <p class="hint" style="margin-top:0">Restart to apply config changes (new workflows/models, edited <code>models.yaml</code>). Interrupts running training/generation; managed ComfyUI keeps running.</p>
    <div class="row" style="margin-top:10px">
      <button onclick={restartServer} disabled={restarting}>{restarting ? 'Restarting…' : 'Restart server'}</button>
    </div>
  </section>

  <section class="card">
    <div class="chead">
      <h3>Trainer</h3>
      {#if trainer}
        {#if trainer.installed}<span class="pill ok">ready</span>{:else}<span class="pill bad">not ready</span>{/if}
      {/if}
    </div>
    {#if trainer && !trainer.installed && trainer.issues?.length}<div class="note">{trainer.issues[0]}</div>{/if}

    <p class="hint" style="margin-top:0">kohya sd-scripts in a dedicated Python 3.11 venv (ComfyUI can't train). Install or repair it here — it's a several-GB CUDA PyTorch download, matched to your GPU.</p>

    <div class="row" style="gap:10px; align-items:flex-end; margin-top:10px">
      <div>
        <label style="margin-top:0">CUDA build</label>
        <select bind:value={cuda}>
          <option value="auto">Auto{gpu?.cuda ? ` → ${gpu.cuda}` : ''}</option>
          <option value="cu128">cu128 (Blackwell / RTX 50-series)</option>
          <option value="cu124">cu124 (RTX 20–40 series)</option>
          <option value="cu121">cu121 (older)</option>
        </select>
      </div>
      <button onclick={repair} disabled={sbusy}>{sbusy ? 'Setting up…' : (trainer?.installed ? 'Reinstall trainer' : 'Install trainer')}</button>
      {#if sbusy}<button class="ghost sm" onclick={cancelSetup} disabled={scancelling}>{scancelling ? 'Cancelling…' : 'Cancel'}</button>{/if}
    </div>
    {#if gpu?.gpu}<p class="hint" style="margin-top:8px">Detected: <strong>{gpu.gpu}</strong> → recommends <strong>{gpu.cuda}</strong>. Terminal alternative: <code>pwsh {trainer?.setup_script} -Recreate -Configure</code></p>{/if}
    {#if smsg}<div class:err={smsg.err} class="msg">{smsg.text}</div>{/if}

    {#if sbusy || slog.length}
      <div class="run">
        <div class="prow">
          <div class="bar" class:indet={spct === null}><span style={spct !== null ? `width:${spct}%` : ''}></span></div>
          <div class="pmeta">{spct !== null ? `${spct}%` : (sbusy ? 'working…' : (sstatus || ''))}</div>
        </div>
        <pre class="log" bind:this={slogEl}>{slog.join('\n')}</pre>
      </div>
    {/if}

    <div class="divider"></div>
    <div class="shd">Paths</div>
    <label>sd-scripts folder</label>
    <input bind:value={tcfg.sd_scripts_dir} placeholder="C:\…\sd-scripts" />
    <label>venv python <span class="dim">(blank = autodetect)</span></label>
    <input bind:value={tcfg.python} placeholder="…\sd-scripts\venv\Scripts\python.exe" />
    <div class="two">
      <div>
        <label>Checkpoints folder <span class="dim">(blank = ComfyUI's)</span></label>
        <input bind:value={tcfg.checkpoints_dir} placeholder={trainer?.checkpoints_dir_resolved || 'auto'} />
      </div>
      <div>
        <label>LoRA output folder <span class="dim">(blank = ComfyUI's)</span></label>
        <input bind:value={tcfg.loras_dir} placeholder={trainer?.loras_out_dir || 'auto'} />
      </div>
    </div>
    <div class="row" style="margin-top:14px; align-items:center; gap:12px">
      <span class:ok={tmsg?.ok} class="msg">{tmsg?.text || 'Auto-saves'}</span>
    </div>
  </section>
</div>
</div>

<style>
  .wrap { max-width: 760px; margin: 0 auto; }
  h2 { margin: 0 0 18px; font-size: 20px; font-weight: 680; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius-lg); padding: 18px; margin-bottom: 16px; }
  .card h3 { margin: 0 0 14px; font-size: 15px; font-weight: 650; }
  .chead { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
  .chead h3 { margin: 0; }

  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; }
  .kv { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
  .kv span { font-size: 12.5px; color: var(--muted); width: 72px; flex: none; }
  .kv b { font-size: 13px; font-weight: 560; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: inline-flex; align-items: center; gap: 8px; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .muted { color: var(--muted); font-weight: 400; }

  label { display: block; font-size: 12px; color: var(--muted); margin: 12px 0 4px; }
  .dim { color: var(--faint, var(--muted)); }
  input { width: 100%; }
  select { padding: 8px 10px; border-radius: 8px; background: var(--elev); color: var(--text); border: 1px solid var(--border); }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  .row { display: flex; }
  .divider { height: 1px; background: var(--border-soft); margin: 18px 0 4px; }
  .shd { font-size: 10px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); margin: 8px 0 2px; }

  .pill { font-size: 11px; font-weight: 600; padding: 2px 10px; border-radius: 999px; }
  .pill.ok { background: rgba(87, 217, 163, .14); color: var(--good); }
  .pill.bad { background: rgba(255, 122, 122, .14); color: var(--bad); }
  /* status light dot */
  .slight { display: inline-block; width: 9px; height: 9px; border-radius: 50%; box-shadow: 0 0 8px currentColor; vertical-align: middle; margin-right: 6px; }
  .slight.up { background: var(--good); color: var(--good); }
  .slight.down { background: var(--bad); color: var(--bad); }
  .slight.busy { background: var(--warn); color: var(--warn); animation: pulse 1.2s ease-in-out infinite; }
  .slbl { font-size: 12px; color: var(--muted); }
  @keyframes pulse { 50% { opacity: .35; } }
  .note { font-size: 12.5px; color: var(--bad); margin: 0 0 10px; line-height: 1.5; }
  .hint { font-size: 12px; color: var(--muted); margin: 12px 0 0; }
  code { background: var(--elev); padding: 1px 5px; border-radius: 5px; font-size: 11.5px; }
  .msg { font-size: 12.5px; margin-top: 8px; } .ok { color: var(--good); } .err { color: var(--bad); }
  .lnk { background: none; box-shadow: none; padding: 0; color: var(--accent); font: inherit; }
  .lnk:hover { filter: brightness(1.2); }

  .run { margin-top: 14px; }
  .prow { margin-bottom: 8px; }
  .bar { height: 9px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .bar.indet span { width: 30%; animation: slide 1.1s ease-in-out infinite; }
  @keyframes slide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
  .pmeta { font-size: 12px; color: var(--muted); }
  .log { height: 240px; overflow: auto; background: #0b0e14; border: 1px solid var(--border); border-radius: 8px;
         padding: 10px 12px; font: 11.5px/1.5 ui-monospace, monospace; color: #b9c2d0; white-space: pre-wrap; word-break: break-word; margin: 0; }
</style>

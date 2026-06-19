<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { askConfirm } from '$lib/confirm.svelte.js';

  let gpu = $state(null);
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
    try { await post('/server/restart'); } catch { /* connection drops as it re-execs */ }
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      try { const r = await fetch('/api/health'); if (r.ok) { location.reload(); return; } } catch { /* still down */ }
    }
    restarting = false;
  }

  onMount(async () => {
    loadSrv();
    gpu = await get('/trainer/detect');
  });
</script>

<div class="screen">
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

<style>
  .screen {
    flex: 1; min-height: 0;
    display: grid; grid-template-columns: 1fr 1fr; gap: 16px;
    padding: 18px 20px 18px 4px;
    align-content: start;
  }

  .card {
    background: var(--panel); border: 1px solid var(--border-soft);
    border-radius: var(--radius-lg); padding: 20px 22px;
    display: flex; flex-direction: column; gap: 0;
  }

  .card-head {
    display: flex; align-items: center; gap: 9px;
    padding-bottom: 16px; border-bottom: 1px solid var(--border-soft);
    margin-bottom: 4px;
  }
  h3 { margin: 0; font-size: 14px; font-weight: 660; color: var(--text); }
  .card-sub { font-size: 11.5px; color: var(--faint, var(--muted)); margin-left: auto; }
  .card-sub code {
    background: var(--elev); padding: 1px 5px; border-radius: 4px;
    font-size: 11px; color: var(--muted);
  }

  .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    flex: none; box-shadow: 0 0 7px currentColor; margin-left: auto;
  }
  .dot.up   { background: var(--good); color: var(--good); }
  .dot.busy { background: var(--warn); color: var(--warn); animation: pulse 1.2s ease-in-out infinite; }
  .status-lbl { font-size: 12px; color: var(--muted); }
  @keyframes pulse { 50% { opacity: .3; } }

  .rows { display: flex; flex-direction: column; gap: 0; padding: 4px 0; }
  .row {
    display: flex; align-items: baseline; gap: 12px;
    padding: 9px 0; border-bottom: 1px solid var(--border-soft);
  }
  .row:last-child { border-bottom: none; }
  .row span {
    font-size: 12px; color: var(--muted); width: 62px; flex: none;
    letter-spacing: .01em;
  }
  .row b {
    font-size: 13px; font-weight: 550; flex: 1; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; font-weight: 450; }

  .card-foot {
    display: flex; align-items: center; gap: 14px;
    margin-top: 18px; padding-top: 16px;
    border-top: 1px solid var(--border-soft);
  }
  .foot-note {
    font-size: 11.5px; color: var(--faint, var(--muted));
    line-height: 1.5; flex: 1;
  }
</style>

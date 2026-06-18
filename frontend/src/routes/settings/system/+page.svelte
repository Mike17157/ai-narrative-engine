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
    restarting = false; // gave up waiting
  }

  onMount(async () => {
    loadSrv();
    gpu = await get('/trainer/detect');
  });
</script>

<div class="screen">
  <div class="col left">
    <h3>Environment</h3>
    <p class="hint flat">This machine. Profile is read from <code>user.yaml</code>; GPU/CUDA are auto-detected.</p>
    <div class="grid">
      <div class="kv"><span>Profile</span><b>{profile.name || '—'}</b></div>
      {#if profile.email}<div class="kv"><span>Email</span><b>{profile.email}</b></div>{/if}
      <div class="kv"><span>GPU</span><b>{gpu?.gpu || 'not detected'}</b></div>
      <div class="kv"><span>CUDA</span><b>{gpu?.cuda || '—'}{gpu?.compute_cap ? ` · sm ${gpu.compute_cap}` : ''}</b></div>
    </div>
  </div>

  <div class="col right">
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
    <p class="hint flat">Restart to apply config changes (new workflows/models, edited <code>models.yaml</code>). Interrupts running training/generation; managed ComfyUI keeps running.</p>
    <div class="actions">
      <button onclick={restartServer} disabled={restarting}>{restarting ? 'Restarting…' : 'Restart server'}</button>
    </div>
  </div>
</div>

<style>
  .screen { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 18px 20px 18px 4px; }
  .col { background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 18px; overflow: auto; min-width: 0; }
  h3 { margin: 0 0 8px; font-size: 15px; font-weight: 650; }
  .chead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .chead h3 { margin: 0; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-top: 16px; }
  .kv { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
  .kv span { font-size: 12.5px; color: var(--muted); width: 64px; flex: none; }
  .kv b { font-size: 13px; font-weight: 560; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .actions { display: flex; align-items: center; gap: 10px; margin-top: 14px; }
  .hint { font-size: 12.5px; color: var(--muted); margin: 12px 0 0; line-height: 1.55; }
  .hint.flat { margin-top: 4px; }
  code { background: var(--elev); padding: 1px 5px; border-radius: 5px; font-size: 11.5px; }
  .slight { display: inline-block; width: 9px; height: 9px; border-radius: 50%; box-shadow: 0 0 8px currentColor; }
  .slight.up { background: var(--good); color: var(--good); }
  .slight.busy { background: var(--warn); color: var(--warn); animation: pulse 1.2s ease-in-out infinite; }
  .slbl { font-size: 12.5px; color: var(--muted); }
  @keyframes pulse { 50% { opacity: .35; } }
</style>

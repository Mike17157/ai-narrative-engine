<script>
  import { app, refreshActivity, refreshHealth, cancelJob } from '$lib/app.svelte.js';
  import { post } from '$lib/api.js';
  import ProgressBar from '$lib/components/shared/ProgressBar.svelte';
  import DropdownMenu from '$lib/components/shared/DropdownMenu.svelte';

  let { onnavigate } = $props();

  // Server-resident jobs + client-driven workloads (renders/generation), running first.
  // Both carry a `screen` to jump back to the output.
  let jobs = $derived([...(app.localJobs || []), ...(app.activity?.jobs || [])]
    .map((j, i) => ({ j, i }))
    .sort((a, b) => (a.j.status === 'running' ? 0 : 1) - (b.j.status === 'running' ? 0 : 1) || a.i - b.i)
    .map((x) => x.j));
  let sys = $derived(app.activity?.system || {});
  let comfyUp = $derived(app.health?.comfyui?.up);
  let starting = $state(false);
  const gb = (mib) => (mib / 1024).toFixed(1);

  function pct(j) { return j.total ? Math.round((j.done / j.total) * 100) : null; }

  async function cancel(j) {
    if (j.local) { cancelJob(j); return; }   // client workload — abort its loop/request
    if (!j.cancel) return;                    // server job — hit its cancel endpoint
    await post(j.cancel);
    await refreshActivity();
  }

  async function launchComfy() {
    if (comfyUp || starting) return;
    starting = true;
    await post('/comfy/up');
    starting = false;
    await refreshHealth();
  }
</script>

<DropdownMenu width="320px">
  {#if sys.cpu != null || sys.gpu || sys.mem}
    <div class="sys">
      {#if sys.cpu != null}
        <div class="stat"><span class="sl">CPU</span><div class="sbar"><span style="width:{sys.cpu}%"></span></div><span class="sv">{Math.round(sys.cpu)}%</span></div>
      {/if}
      {#if sys.gpu}
        <div class="stat" title={sys.gpu.name}>
          <span class="sl">GPU</span><div class="sbar"><span style="width:{sys.gpu.util}%"></span></div>
          <span class="sv">{Math.round(sys.gpu.util)}%</span>
          <span class="sg">{gb(sys.gpu.mem_used)}/{Math.round(sys.gpu.mem_total / 1024)} GB</span>
        </div>
        {#if sys.gpu.power != null && sys.gpu.power_limit}
          <div class="stat" title="Power draw vs limit — actual compute strain">
            <span class="sl">PWR</span>
            <div class="sbar"><span class="warm" style="width:{Math.min(100, (sys.gpu.power / sys.gpu.power_limit) * 100)}%"></span></div>
            <span class="sv">{Math.round((sys.gpu.power / sys.gpu.power_limit) * 100)}%</span>
            <span class="sg">{Math.round(sys.gpu.power)}/{Math.round(sys.gpu.power_limit)}W{sys.gpu.temp != null ? ` · ${Math.round(sys.gpu.temp)}°` : ''}</span>
          </div>
        {/if}
        {#if sys.gpu.mem_util != null}
          <div class="stat" title="Memory-controller (VRAM bandwidth) load">
            <span class="sl">VMEM</span><div class="sbar"><span style="width:{sys.gpu.mem_util}%"></span></div>
            <span class="sv">{Math.round(sys.gpu.mem_util)}%</span><span class="sg">bandwidth</span>
          </div>
        {/if}
      {/if}
      {#if sys.mem}
        <div class="stat">
          <span class="sl">RAM</span><div class="sbar"><span style="width:{sys.mem.percent}%"></span></div>
          <span class="sv">{Math.round(sys.mem.percent)}%</span>
          <span class="sg">{(sys.mem.used / 1e9).toFixed(1)}/{Math.round(sys.mem.total / 1e9)} GB</span>
        </div>
      {/if}
    </div>
  {/if}

  <div class="svc">
    <span class="dot {comfyUp ? 'running' : 'error'}"></span>
    <span class="kind">ComfyUI</span>
    {#if comfyUp}<span class="status running">live</span>
    {:else}<button class="lnk" onclick={launchComfy} disabled={starting}>{starting ? 'starting…' : 'Launch'}</button>{/if}
  </div>

  <div class="mhd">Workloads</div>
  {#if !jobs.length}
    <div class="empty">Nothing running. Start a render or generation and it'll show here — and keep running while you move around the app.</div>
  {:else}
    {#each jobs as j (j.id)}
      <div class="job" class:run={j.status === 'running'}>
        <div class="top">
          <span class="dot {j.status}"></span>
          <span class="kind">{j.kind}{j.label ? ` · ${j.label}` : ''}</span>
          <span class="status {j.status}">{j.cancelling ? 'cancelling…' : j.status}</span>
        </div>
        {#if j.status === 'running'}
          <ProgressBar value={pct(j)} max={100} height="6px" margin="8px 0 6px" />
        {/if}
        <div class="meta">
          <span>{j.total ? `${j.done}/${j.total}${j.unit ? ' ' + j.unit : ''}` : (j.status === 'running' ? 'working…' : 'done')}</span>
          <span class="acts">
            {#if j.screen}<button class="lnk" onclick={() => onnavigate?.(j.screen)}>Open output</button>{/if}
            {#if j.status === 'running' && (j.cancel || (j.local && j.onCancel))}<button class="lnk danger" onclick={() => cancel(j)} disabled={j.cancelling}>{j.cancelling ? 'cancelling…' : 'Cancel'}</button>{/if}
          </span>
        </div>
      </div>
    {/each}
  {/if}
</DropdownMenu>

<style>
  .mhd { font-size: 10.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); padding: 6px 8px 8px; }

  .sys { padding: 8px 8px 10px; border-bottom: 1px solid var(--border-soft); display: flex; flex-direction: column; gap: 7px; }
  .stat { display: flex; align-items: center; gap: 8px; }
  .stat .sl { font-size: 11px; font-weight: 700; color: var(--muted); width: 30px; flex: none; letter-spacing: .3px; }
  .stat .sbar { flex: 1; height: 6px; border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .stat .sbar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .4s; }
  .stat .sbar span.warm { background: linear-gradient(90deg, #f5c451, #ff7a7a); }
  .stat .sv { font-size: 11.5px; font-weight: 600; color: var(--text); width: 34px; text-align: right; flex: none; }
  .stat .sg { font-size: 10.5px; color: var(--faint); width: 78px; text-align: right; flex: none; white-space: nowrap; }
  .svc { display: flex; align-items: center; gap: 8px; padding: 7px 8px 9px; border-bottom: 1px solid var(--border-soft); }
  .svc .kind { flex: 1; }
  .empty { font-size: 12.5px; color: var(--muted); padding: 6px 8px 12px; line-height: 1.5; }

  .job { padding: 9px 8px; border-radius: 9px; }
  .job + .job { border-top: 1px solid var(--border-soft); }
  .top { display: flex; align-items: center; gap: 8px; }
  .kind { font-size: 13px; font-weight: 560; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--muted); }
  .dot.running { background: var(--accent); box-shadow: 0 0 8px var(--accent-glow); animation: pulse 1.2s ease-in-out infinite; }
  .dot.done { background: var(--good); } .dot.error, .dot.cancelled { background: var(--bad); }
  @keyframes pulse { 50% { opacity: .35; } }
  .status { font-size: 11px; color: var(--muted); text-transform: capitalize; }
  .status.running { color: var(--accent); } .status.done { color: var(--good); } .status.error, .status.cancelled { color: var(--bad); }


  .meta { display: flex; justify-content: space-between; align-items: center; font-size: 11.5px; color: var(--muted); margin-top: 4px; }
  .acts { display: flex; gap: 10px; }
  .lnk { background: none; padding: 0; color: var(--accent); font: inherit; font-size: 11.5px; }
  .lnk:hover { filter: brightness(1.2); }
  .lnk.danger { color: var(--bad); }
</style>

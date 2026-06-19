<script>
  import { app, refreshHealth } from '$lib/app.svelte.js';
  import { post } from '$lib/api.js';

  let comfy = $derived(app.health?.comfyui || {});
  let starting = $state(false);

  async function launchComfy() {
    if (comfy.up || starting) return;
    starting = true; await post('/comfy/up'); starting = false; await refreshHealth();
  }
</script>

<div class="screen">
  <div class="col">
    <div class="chead">
      <h3>ComfyUI</h3>
      <span class="slight {comfy.up ? 'up' : 'down'}"></span>
      <span class="slbl">{comfy.up ? 'live' : 'off'}</span>
      {#if !comfy.up}<button class="ghost sm" onclick={launchComfy} disabled={starting}>{starting ? 'starting…' : 'launch'}</button>{/if}
    </div>
    <p class="hint flat">The local Stable Diffusion backend. The server URL and launch mode come from <code>user.yaml</code> (hand-edited for now). The per-connection ComfyUI URL is set in <a href="/settings/connections">Connections</a>.</p>

    <div class="grid">
      <div class="kv"><span>Server</span><b class="mono" title={comfy.base_url}>{comfy.base_url || '—'}</b></div>
      <div class="kv"><span>Mode</span><b class="muted">{comfy.managed ? 'managed (auto-launch)' : 'connect-only'}</b></div>
    </div>

    <p class="hint flat">Edit <code>comfyui.base_url</code>, <code>comfyui.managed</code>, and launch overrides in <code>user.yaml</code>, then restart the backend (<a href="/settings/system">System</a>).</p>
  </div>
</div>

<style>
  .screen { flex: 1; min-height: 0; display: flex; padding: 18px 20px 18px 4px; }
  .col { flex: 1; background: var(--panel); border: 1px solid var(--border-soft); border-radius: var(--radius-lg); padding: 18px; overflow: auto; }
  .chead { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
  .chead h3 { margin: 0; font-size: 15px; font-weight: 650; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px; margin-top: 16px; }
  .kv { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
  .kv span { font-size: 12.5px; color: var(--muted); width: 64px; flex: none; }
  .kv b { font-size: 13px; font-weight: 560; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .mono { font-family: ui-monospace, monospace; font-size: 12px; }
  .muted { color: var(--muted); font-weight: 400; }
  .slight { display: inline-block; width: 9px; height: 9px; border-radius: 50%; box-shadow: 0 0 8px currentColor; }
  .slight.up { background: var(--good); color: var(--good); }
  .slight.down { background: var(--bad); color: var(--bad); }
  .slbl { font-size: 12.5px; color: var(--muted); }
  .hint { font-size: 12.5px; color: var(--muted); margin: 12px 0 0; line-height: 1.55; }
  .hint.flat { margin-top: 4px; }
  .hint a { color: var(--accent); }
  code { background: var(--elev); padding: 1px 5px; border-radius: 5px; font-size: 11.5px; }
</style>

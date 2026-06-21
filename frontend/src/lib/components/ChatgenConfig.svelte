<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let system = $state('');
  let saving = $state(false);
  let msg = $state(null);
  let loading = $state(true);
  let snap = $state(null);
  let saveTimer = null;

  onMount(async () => {
    const data = await get('/chatgen');
    system = data.system || '';
    loading = false;
    snap = system;
  });

  async function save() {
    saving = true; msg = null;
    const r = await post('/chatgen', { system });
    saving = false;
    msg = r.ok ? { ok: true, text: '✓ saved' } : { err: true, text: r.data?.error || 'save failed' };
    snap = system;
  }

  $effect(() => {
    if (loading || snap === null || system === snap) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(save, 700);
  });
</script>

<div class="cfg">
  <p class="intro">Global system prompt prepended to every chat turn, on top of the character's own system.
    Leave blank to let the character card govern entirely.</p>

  {#if !loading}
    <label>System prompt <span class="lo">— applied to all chats</span></label>
    <textarea class="fld ta mono" use:autosize={system} bind:value={system}></textarea>

    <div class="prow">
      <button class="ghost sm" onclick={() => { system = ''; }}>Clear</button>
      <span class="pm" class:ok={msg?.ok} class:err={msg?.err}>{saving ? 'Saving…' : (msg?.text || 'Auto-saves')}</span>
    </div>
  {:else}
    <p class="lo">Loading…</p>
  {/if}
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
  .prow { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
  .pm { font-size: 12px; color: var(--muted); } .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }
</style>

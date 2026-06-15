<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import Combobox from '$lib/components/Combobox.svelte';

  // Per-role image-workflow config: which workflow each kind of generation uses.
  // 'Auto' (empty) keeps the default. Saves instantly to configs/image_roles.json.
  let data = $state(null);
  let savedRole = $state('');

  async function load() { data = await get('/image-roles'); }
  onMount(load);

  let items = $derived(data
    ? [{ value: '', label: 'Auto (default)' }, ...data.models.map((m) => ({ value: m, label: m }))]
    : []);

  async function pick(role, value) {
    const r = await post('/image-roles', { role, model: value });
    if (r.ok && r.data) {
      data.config = r.data.config;
      data.effective = r.data.effective;
      savedRole = role;
      setTimeout(() => { if (savedRole === role) savedRole = ''; }, 1600);
    }
  }
</script>

<div class="hint">
  Which workflow each kind of image generation uses. <b>Auto</b> keeps the current default — the
  active image model, or the role's built-in workflow (sprites/scenes/style). Changes save
  instantly, no restart. Tip: duplicate a workflow in <b>Graph</b> to make a tweakable variant,
  then point a role at it here.
</div>

{#if data}
  <div class="roles">
    {#each data.roles as role (role)}
      <div class="role">
        <div class="info">
          <div class="rlabel">{data.labels[role]}</div>
          <div class="reff">
            uses <code>{data.effective[role] || '—'}</code>
            {#if !data.config[role]}<span class="auto">auto</span>{/if}
          </div>
        </div>
        <div class="rpick">
          <Combobox {items} value={data.config[role]} placeholder="Auto (default)" onpick={(v) => pick(role, v)} />
          {#if savedRole === role}<span class="ok">✓ saved</span>{/if}
        </div>
      </div>
    {/each}
  </div>
{:else}
  <div class="center">Loading…</div>
{/if}

<style>
  .hint { font-size: 12.5px; color: var(--muted); margin-bottom: 16px; line-height: 1.55; max-width: 760px; }
  .center { display: grid; place-items: center; height: 24vh; color: var(--muted); font-size: 13px; }
  .roles { display: flex; flex-direction: column; gap: 10px; max-width: 760px; }
  .role {
    display: grid; grid-template-columns: 1fr 320px; gap: 16px; align-items: center;
    background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px; padding: 12px 16px;
  }
  .rlabel { font-size: 14px; font-weight: 600; color: var(--text); }
  .reff { font-size: 12px; color: var(--muted); margin-top: 3px; }
  .reff code { background: var(--elev); padding: 1px 6px; border-radius: 5px; color: var(--text); font-size: 11.5px; }
  .auto { color: var(--faint); margin-left: 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .3px; }
  .rpick { display: flex; align-items: center; gap: 10px; }
  .ok { color: var(--good, #8fc7a0); font-size: 12px; white-space: nowrap; }
  @media (max-width: 640px) { .role { grid-template-columns: 1fr; gap: 8px; } .rpick { width: 100%; } }
</style>

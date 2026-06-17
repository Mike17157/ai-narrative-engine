<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import Combobox from '$lib/components/Combobox.svelte';

  // One imaging aspect's ComfyUI image WORKFLOW (image_roles.json). Compatible workflows only —
  // these are image graphs, distinct from the text generation models. Saves instantly, shared
  // with Images ▸ Roles (same file).
  let { role } = $props();
  let data = $state(null);
  let saved = $state(false);

  async function load() { data = await get('/image-roles'); }
  onMount(load);

  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other');
  let items = $derived(data
    ? [{ value: '', label: 'Auto (default)' },
       ...data.models.map((m) => ({ value: m, label: m, group: famCap((data.families || {})[m]) }))]
    : []);

  async function pick(value) {
    const r = await post('/image-roles', { role, model: value });
    if (r.ok && r.data) {
      data.config = r.data.config;
      data.effective = r.data.effective;
      saved = true; setTimeout(() => (saved = false), 1600);
    }
  }
</script>

<div class="cfg">
  {#if data}
    <h3 class="shead">{data.labels?.[role] || role}</h3>
    <p class="intro">Which ComfyUI <b>image workflow</b> renders this aspect. These are image graphs —
      a different kind from the text generation models. <b>Auto</b> keeps the default (the active image
      model or the role's built-in workflow). Saves instantly; shared with Images ▸ Roles.</p>

    <label>Workflow <span class="lo">— compatible image workflows only</span></label>
    <Combobox {items} value={data.config?.[role] || ''} placeholder="Auto (default)" onpick={pick} />

    <p class="note">uses <code>{data.effective?.[role] || '—'}</code>
      {#if !data.config?.[role]}<span class="auto">auto</span>{/if}
      {#if saved}<span class="ok">✓ saved</span>{/if}</p>
  {:else}<p class="lo">Loading…</p>{/if}
</div>

<style>
  .cfg { max-width: 760px; }
  .shead { margin: 0 0 2px; font-size: 16px; font-weight: 700; color: var(--text); }
  .intro { font-size: 12.5px; color: var(--muted); margin: 4px 0 6px; line-height: 1.5; }
  label { display: block; font-size: 11px; color: var(--muted); margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .note { font-size: 12px; color: var(--muted); margin-top: 8px; }
  .note code { background: var(--elev); padding: 1px 6px; border-radius: 5px; color: var(--text); font-size: 11.5px; }
  .auto { color: var(--faint); margin-left: 6px; font-size: 11px; text-transform: uppercase; letter-spacing: .3px; }
  .ok { color: var(--good, #8fc7a0); font-size: 12px; margin-left: 8px; }
</style>

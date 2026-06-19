<script>
  import { onMount } from 'svelte';
  import { app, refreshModels, setActiveImage } from '../app.svelte.js';
  import Combobox from './Combobox.svelte';

  // Pick which image model / workflow renders pictures in chat. The selection is
  // what `send()` passes to /run as image_model, and it persists across sessions.
  const famCap = (f) => (f && f !== 'unknown' ? f[0].toUpperCase() + f.slice(1) : 'Other');
  let items = $derived((app.models.image || []).map((m) => ({ value: m.key, label: m.key, group: famCap(m.family) })));

  onMount(() => { if (!app.models.image?.length) refreshModels(); });
</script>

{#if items.length}
  <div class="hint">The image workflow used to render pictures in chat &amp; stories. {items.length} available — edit workflows in the <b>Images</b> section.</div>
  <label>Image model</label>
  <Combobox items={items} value={app.activeImage} placeholder="workflow…" onpick={(v) => setActiveImage(v)} />
{:else}
  <div class="hint">No image workflows yet — connect ComfyUI in <a href="/settings/comfyui">Settings ▸ ComfyUI</a>.</div>
{/if}

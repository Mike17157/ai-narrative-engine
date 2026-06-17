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
  <div class="hint">The image model / workflow used to render pictures in chat. {items.length} available — edit the workflow itself in the <b>Images</b> section.</div>
  <label>Image model</label>
  <Combobox items={items} value={app.activeImage} placeholder="workflow…" onpick={(v) => setActiveImage(v)} />
{:else}
  <div class="hint">No image models yet — connect ComfyUI in the <b>Connection</b> tab.</div>
{/if}

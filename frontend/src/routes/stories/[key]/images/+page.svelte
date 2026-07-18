<script>
  import { page } from '$app/stores';
  import { isStoryHostDesktop, requestStoryHost, resolveStoryAssetDataUrl } from '$lib/story-host-client';

  let key = $derived($page.params.key || '');
  let status = $state(null);
  let images = $state([]);
  let loading = $state(true);
  let galleryError = $state('');
  let renderError = $state('');
  let prompt = $state('');
  let role = $state('scene');
  let model = $state('');
  let rendering = $state(false);
  let requestedKey = '';
  let requestVersion = 0;

  let models = $derived(Array.isArray(status?.models) ? status.models : []);
  let roles = $derived(Array.isArray(status?.roles) && status.roles.length
    ? status.roles
    : ['scene', 'base', 'sprite', 'chat']);
  let capabilityAvailable = $derived(status?.enabled === true && models.length > 0);

  async function json(response) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      // Preserve a useful HTTP error even when an intermediary returned no JSON.
    }
    if (!response.ok) {
      throw new Error(data?.error || `Request failed (${response.status})`);
    }
    return data;
  }

  async function resolveDesktopImages(rawImages, storyKey, version) {
    const resolved = await Promise.all((Array.isArray(rawImages) ? rawImages : []).map(async (image) => {
      const asset = image?.asset;
      if (!asset || typeof asset !== 'object') return { ...image, url: '' };
      try {
        return { ...image, url: await resolveStoryAssetDataUrl(asset) };
      } catch {
        // A stale/missing file is not a reason to expose a filesystem path or
        // retry through HTTP. Keep the gallery entry with a harmless state.
        return { ...image, url: '' };
      }
    }));
    return version === requestVersion && storyKey === key ? resolved : null;
  }

  async function load() {
    const storyKey = key;
    if (!storyKey) return;
    const version = ++requestVersion;
    requestedKey = storyKey;
    loading = true;
    galleryError = '';
    const desktop = isStoryHostDesktop();

    const encodedKey = encodeURIComponent(storyKey);
    const [statusResult, galleryResult] = await Promise.allSettled([
      desktop ? requestStoryHost('image.status') : fetch('/api/lean/images/status').then(json),
      desktop ? requestStoryHost('image.list', { key: storyKey }) : fetch(`/api/stories/${encodedKey}/images`).then(json),
    ]);

    if (version !== requestVersion || storyKey !== key) return;
    loading = false;

    if (statusResult.status === 'fulfilled') {
      status = statusResult.value;
      const availableModels = Array.isArray(status.models) ? status.models : [];
      if (!availableModels.includes(model)) model = availableModels[0] || '';
      const availableRoles = Array.isArray(status.roles) ? status.roles : [];
      if (availableRoles.length && !availableRoles.includes(role)) role = availableRoles[0];
    } else {
      status = { enabled: false, models: [], message: 'The lean image capability is unavailable on this server.' };
    }

    if (galleryResult.status === 'fulfilled') {
      const rawImages = Array.isArray(galleryResult.value?.images) ? galleryResult.value.images : [];
      if (desktop) {
        const resolved = await resolveDesktopImages(rawImages, storyKey, version);
        if (!resolved) return;
        images = resolved;
      } else {
        images = rawImages;
      }
    } else {
      images = [];
      galleryError = galleryResult.reason?.message || 'Could not load this story’s images.';
    }
  }

  $effect(() => {
    if (!key || requestedKey === key) return;
    void load();
  });

  async function renderImage() {
    renderError = '';
    if (!capabilityAvailable) return;
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      renderError = 'Describe the image before rendering.';
      return;
    }

    rendering = true;
    try {
      const desktop = isStoryHostDesktop();
      const result = desktop
        ? await requestStoryHost('image.request', { key, prompt: cleanPrompt, role, ...(model ? { model } : {}) })
        : await fetch(`/api/stories/${encodeURIComponent(key)}/images/render`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: cleanPrompt, role, ...(model ? { model } : {}) }),
        }).then(json);
      if (result?.image) {
        let image = result.image;
        if (desktop) {
          const resolvedImages = await resolveDesktopImages([image], key, requestVersion);
          const resolved = resolvedImages?.[0];
          image = resolved || image;
        }
        images = [image, ...images.filter((existing) => existing.id !== image.id)];
      }
      prompt = '';
    } catch (error) {
      renderError = error?.message || 'Could not render the image.';
    } finally {
      rendering = false;
    }
  }
</script>

<div class="page image-page">
  <div class="col wide">
    <header class="page-head">
      <div>
        <p class="eyebrow">Story-owned artwork</p>
        <h2>Story images</h2>
        <p class="intro">Generate only the images that belong to this story. The optional local Krea2/Comfy runner starts when you request a render.</p>
      </div>
      <button class="soft" type="button" onclick={load} disabled={loading || rendering}>Refresh</button>
    </header>

    {#if loading}
      <section class="card status loading" aria-live="polite">Checking the story image capability…</section>
    {:else}
      <section class="card status" class:ready={capabilityAvailable}>
        <div class="status-copy">
          <span class="status-dot" aria-hidden="true"></span>
          <div>
            <strong>{capabilityAvailable ? 'Krea2 / Comfy enabled on demand' : 'Krea2 / Comfy not available'}</strong>
            <p>{status?.message || (capabilityAvailable ? 'A local Krea2 workflow will start only for a render.' : 'No Krea2 workflow is configured for the lean story app.')}</p>
          </div>
        </div>
        {#if models.length}
          <span class="model-count">{models.length} workflow{models.length === 1 ? '' : 's'}</span>
        {/if}
      </section>

      {#if capabilityAvailable}
        <form class="card render-form" onsubmit={(event) => { event.preventDefault(); void renderImage(); }}>
          <div class="form-head">
            <div>
              <h3>Render an image</h3>
              <p>Choose a story role and Krea2 workflow, then describe the image.</p>
            </div>
          </div>
          <label for="story-image-prompt">Prompt</label>
          <textarea id="story-image-prompt" bind:value={prompt} rows="5" maxlength="12000"
                    placeholder="A rain-slick city street at blue hour, viewed from the protagonist’s window…"></textarea>
          <div class="controls">
            <label>
              <span>Role</span>
              <select bind:value={role}>
                {#each roles as option}
                  <option value={option}>{option}</option>
                {/each}
              </select>
            </label>
            <label>
              <span>Workflow</span>
              <select bind:value={model}>
                {#each models as option}
                  <option value={option}>{option}</option>
                {/each}
              </select>
            </label>
            <button class="primary render" type="submit" disabled={rendering || !prompt.trim()}>
              {rendering ? 'Rendering…' : 'Render image'}
            </button>
          </div>
          {#if renderError}<p class="error" aria-live="assertive">{renderError}</p>{/if}
        </form>
      {:else}
        <section class="card unavailable">
          <h3>Rendering is optional</h3>
          <p>Start the lean story app with its Comfy option enabled and configure a local <code>krea2*</code> workflow to render here. Existing story images remain available below.</p>
        </section>
      {/if}
    {/if}

    <section class="gallery-section" aria-labelledby="gallery-title">
      <div class="gallery-head">
        <div>
          <p class="eyebrow">Gallery</p>
          <h3 id="gallery-title">Generated for this story</h3>
        </div>
        {#if !loading}<span class="image-count">{images.length} image{images.length === 1 ? '' : 's'}</span>{/if}
      </div>

      {#if galleryError}
        <p class="error" aria-live="polite">{galleryError}</p>
      {:else if !loading && !images.length}
        <div class="empty">No story images yet.</div>
      {:else if images.length}
        <div class="gallery">
          {#each images as image (image.id)}
            {#if image.url}
              <a class="image-card" href={image.url} target="_blank" rel="noreferrer" title="Open full-size image">
                <img src={image.url} alt={`Story image ${image.id}`} loading="lazy" />
                <span>{image.role || image.id}</span>
              </a>
            {:else}
              <div class="image-card missing" title="This local image file is unavailable">
                <span>{image.role || image.id} is unavailable</span>
              </div>
            {/if}
          {/each}
        </div>
      {/if}
    </section>
  </div>
</div>

<style>
  .image-page { padding-bottom: 48px; }
  .page-head, .gallery-head, .status, .status-copy, .controls { display: flex; }
  .page-head { align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 18px; }
  .page-head h2 { margin-bottom: 5px; }
  .page-head > button { flex: none; }
  .eyebrow { margin: 0 0 5px; color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
  .intro { max-width: 620px; margin: 0; color: var(--muted); font-size: 13px; line-height: 1.5; }
  .status { align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; padding: 13px 16px; }
  .status-copy { align-items: flex-start; gap: 10px; }
  .status strong { font-size: 13px; }
  .status p { margin: 3px 0 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
  .status-dot { width: 8px; height: 8px; flex: none; margin-top: 5px; border-radius: 50%; background: var(--muted); }
  .status.ready .status-dot { background: #65c58a; box-shadow: 0 0 0 4px rgba(101, 197, 138, .12); }
  .model-count, .image-count { flex: none; border: 1px solid var(--border); border-radius: 999px; padding: 3px 8px; color: var(--muted); font-size: 11px; }
  .render-form { margin-bottom: 14px; }
  .form-head h3, .gallery-head h3, .unavailable h3 { margin: 0; font-size: 15px; }
  .form-head p, .unavailable p { margin: 4px 0 14px; color: var(--muted); font-size: 12px; line-height: 1.45; }
  .render-form > label { display: block; margin-bottom: 5px; color: var(--muted); font-size: 11px; font-weight: 650; letter-spacing: .03em; text-transform: uppercase; }
  textarea, select { width: 100%; box-sizing: border-box; color: var(--text); background: var(--elev); border: 1px solid var(--border); border-radius: 8px; font: inherit; }
  textarea { display: block; min-height: 104px; padding: 9px 10px; resize: vertical; line-height: 1.45; }
  textarea:focus, select:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); }
  .controls { align-items: end; gap: 10px; margin-top: 12px; }
  .controls label { flex: 1; min-width: 0; color: var(--muted); font-size: 11px; font-weight: 650; letter-spacing: .03em; text-transform: uppercase; }
  .controls label span { display: block; margin-bottom: 5px; }
  select { height: 34px; padding: 0 8px; }
  .render { height: 34px; flex: none; }
  .error { margin: 10px 0 0; color: var(--bad, #e17474); font-size: 12px; }
  .unavailable { margin-bottom: 28px; }
  .unavailable p { margin-bottom: 0; }
  .unavailable code { font-size: .95em; }
  .gallery-section { margin-top: 28px; }
  .gallery-head { align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
  .gallery-head h3 { font-size: 16px; }
  .empty { display: grid; min-height: 150px; place-items: center; border: 1px dashed var(--border); border-radius: 12px; color: var(--muted); font-size: 13px; }
  .gallery { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
  .image-card { display: flex; flex-direction: column; min-width: 0; overflow: hidden; border: 1px solid var(--border-soft); border-radius: 10px; color: var(--muted); background: var(--elev); text-decoration: none; transition: border-color .14s, transform .14s; }
  .image-card:hover { border-color: var(--accent); transform: translateY(-1px); }
  .image-card.missing { min-height: 120px; justify-content: end; border-style: dashed; color: var(--faint); }
  .image-card img { display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: cover; background: var(--bg); }
  .image-card span { overflow: hidden; padding: 7px 8px; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
  @media (max-width: 620px) {
    .page-head, .status { align-items: stretch; flex-direction: column; }
    .page-head > button { align-self: flex-start; }
    .controls { display: grid; grid-template-columns: 1fr 1fr; }
    .render { grid-column: 1 / -1; width: 100%; }
  }
</style>

<script>
  import { chars, importFile, importUrl } from '$lib/characters.svelte.js';

  let url = $state('');
  let dragging = $state(false);
  let fileInput = $state();

  async function onFile(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) await importFile(file);
  }
  async function onDrop(e) {
    e.preventDefault();
    dragging = false;
    const file = e.dataTransfer?.files?.[0];
    if (file) await importFile(file);
  }
</script>

<div class="importpane">
  <div class="hint">
    Import a SillyTavern character card — PNG or JSON, by file or URL. The avatar,
    persona, greetings, and lorebook are all preserved.
  </div>

  <div class="dropzone" class:drag={dragging}
    role="button" tabindex="0"
    onclick={() => fileInput.click()}
    onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && fileInput.click()}
    ondragover={(e) => { e.preventDefault(); dragging = true; }}
    ondragleave={() => (dragging = false)}
    ondrop={onDrop}>
    <div class="dzicon">＋</div>
    <div class="dztitle">{chars.importing ? 'Importing…' : 'Drop a card here, or click to choose a file'}</div>
    <div class="dzsub">.png or .json</div>
  </div>
  <input type="file" accept=".png,.json,image/png,application/json"
    bind:this={fileInput} onchange={onFile} hidden />

  <div class="orline"><span>or import from a URL</span></div>
  <div class="urlrow">
    <input class="url" placeholder="Chub, JanitorAI, AICC, Pygmalion, RisuRealm, or a direct .png link"
      bind:value={url} onkeydown={(e) => e.key === 'Enter' && importUrl(url)} />
    <button class="import" onclick={() => importUrl(url)} disabled={chars.importing || !url.trim()}>
      {chars.importing ? 'Importing…' : 'Import'}
    </button>
  </div>
</div>

<style>
  .importpane { max-width: 620px; }
  .importpane .hint { font-size: 13px; color: var(--muted); margin-bottom: 16px; }
  .dropzone {
    display: flex; flex-direction: column; align-items: center; gap: 6px; text-align: center;
    padding: 40px 20px; border: 1.5px dashed var(--border); border-radius: 16px;
    background: var(--elev); color: var(--muted); cursor: pointer; transition: border-color .12s, background .12s;
  }
  .dropzone:hover { border-color: #323847; background: var(--elev-2); }
  .dropzone.drag { border-color: var(--accent); background: rgba(109, 140, 255, .08); color: var(--text); }
  .dzicon {
    width: 48px; height: 48px; border-radius: 14px; display: grid; place-items: center; font-size: 26px;
    color: #fff; background: linear-gradient(135deg, var(--accent), #9a6dff); margin-bottom: 4px;
  }
  .dztitle { font-size: 14.5px; font-weight: 560; color: var(--text); }
  .dzsub { font-size: 12px; color: var(--faint); }
  .orline { display: flex; align-items: center; gap: 12px; margin: 20px 0 12px; color: var(--faint); font-size: 12px; }
  .orline::before, .orline::after { content: ''; flex: 1; height: 1px; background: var(--border-soft); }
  .urlrow { display: flex; gap: 12px; }
  .url { flex: 1; padding: 8px 12px; }
  .import { white-space: nowrap; padding: 0 14px; min-height: 40px; font-weight: 560; font-size: 13px; }
</style>

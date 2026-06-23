<script>
  // Wan generation studio — one pipeline, one flag: render a still (num_frames=1 → image)
  // or a short clip (num_frames → mp4 via VHS). Streams progress over SSE from
  // POST /api/wan/render and shows the result inline (<img> or <video>).
  let mode = $state('image');      // 'image' | 'video'
  let prompt = $state('');
  let negative = $state('blurry, low quality, distorted, deformed');
  let width = $state(512);
  let height = $state(512);
  let steps = $state(20);
  let numFrames = $state(25);      // video only (forced to 4n+1 server-side)
  let fps = $state(16);            // video only

  let running = $state(false);
  let pct = $state(0);
  let stage = $state('');
  let result = $state(null);       // { kind:'image'|'video', src }
  let err = $state(null);
  let _ctrl = null;

  async function render() {
    if (running) { _ctrl?.abort(); return; }
    if (!prompt.trim()) { err = 'Enter a prompt'; return; }
    running = true; pct = 0; stage = 'queued…'; err = null; result = null;
    _ctrl = new AbortController();
    try {
      const res = await fetch('/api/wan/render', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: _ctrl.signal,
        body: JSON.stringify({ prompt, negative, mode, width, height, steps, num_frames: numFrames, fps }),
      });
      const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true }); let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).split('\n').find((l) => l.startsWith('data:'));
          buf = buf.slice(i + 2); if (!line) continue;
          const ev = JSON.parse(line.slice(5).trim());
          if (ev.type === 'progress') { pct = ev.max ? Math.round((ev.value / ev.max) * 100) : pct; stage = mode === 'video' ? 'sampling frames…' : 'sampling…'; }
          else if (ev.type === 'node') { stage = 'running…'; }
          else if (ev.type === 'image') {
            if ((ev.videos || []).length) result = { kind: 'video', src: ev.videos[0] };
            else if ((ev.images || []).length) result = { kind: 'image', src: ev.images[0] };
          }
          else if (ev.type === 'error') { err = ev.error; }
        }
      }
    } catch (e) { if (e.name !== 'AbortError') err = String(e); }
    running = false; stage = ''; _ctrl = null;
  }
</script>

<div class="wan">
  <div class="panel">
    <div class="modeseg">
      <button class:on={mode === 'image'} onclick={() => (mode = 'image')} disabled={running}>🖼 Image</button>
      <button class:on={mode === 'video'} onclick={() => (mode = 'video')} disabled={running}>🎞 Video</button>
    </div>

    <label class="fld">Prompt
      <textarea rows="3" bind:value={prompt} placeholder="a red panda walking through a snowy forest, cinematic"></textarea>
    </label>
    <label class="fld">Negative
      <input bind:value={negative} />
    </label>

    <div class="grid">
      <label>Width<input type="number" step="16" min="64" bind:value={width} /></label>
      <label>Height<input type="number" step="16" min="64" bind:value={height} /></label>
      <label>Steps<input type="number" min="1" max="60" bind:value={steps} /></label>
      {#if mode === 'video'}
        <label title="Forced to the nearest 4n+1 server-side">Frames<input type="number" min="5" step="4" bind:value={numFrames} /></label>
        <label>FPS<input type="number" min="1" max="30" bind:value={fps} /></label>
      {/if}
    </div>

    <button class="go" onclick={render}>
      {running ? '■ Stop' : (mode === 'video' ? 'Generate clip' : 'Generate image')}
    </button>
    {#if running}
      <div class="bar"><div class="fill" style="width:{pct}%"></div></div>
      <div class="stage">{stage} {pct ? pct + '%' : ''}</div>
    {/if}
    {#if err}<div class="err">{err}</div>{/if}
    <p class="hint">Wan 2.1 1.3B on local ComfyUI. Image = a single frame (text→image); Video = a short clip (mp4). The 14B I2V model is RunPod-bound.</p>
  </div>

  <div class="stage-out">
    {#if result?.kind === 'image'}
      <img src={result.src} alt="wan result" />
    {:else if result?.kind === 'video'}
      <video src={result.src} controls autoplay loop muted></video>
    {:else}
      <div class="ph">{running ? 'Rendering…' : 'Your result appears here'}</div>
    {/if}
  </div>
</div>

<style>
  .wan { display: flex; gap: 18px; align-items: flex-start; }
  .panel { width: 360px; flex: none; display: flex; flex-direction: column; gap: 12px; }
  .modeseg { display: inline-flex; border: 1px solid var(--border); border-radius: 10px; overflow: hidden; }
  .modeseg button { flex: 1; border: 0; border-radius: 0; background: var(--elev); color: var(--muted); padding: 9px 0; font-size: 13px; font-weight: 600; box-shadow: none; }
  .modeseg button.on { color: #fff; background: var(--accent); }
  .fld { display: flex; flex-direction: column; gap: 5px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .fld textarea, .fld input { text-transform: none; letter-spacing: 0; font-weight: 400; padding: 8px 10px; font-size: 13px; border-radius: 8px; background: var(--bg); border: 1px solid var(--border); color: var(--text); width: 100%; box-sizing: border-box; }
  .fld textarea { resize: vertical; line-height: 1.45; font-family: inherit; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; }
  .grid label { display: flex; flex-direction: column; gap: 4px; font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .3px; color: var(--muted); }
  .grid input { padding: 6px 8px; font-size: 12.5px; border-radius: 7px; background: var(--bg); border: 1px solid var(--border); color: var(--text); }
  .go { padding: 11px; font-size: 14px; font-weight: 700; }
  .bar { height: 6px; border-radius: 999px; background: var(--elev); overflow: hidden; }
  .fill { height: 100%; background: var(--accent); transition: width .2s; }
  .stage { font-size: 12px; color: var(--accent); }
  .err { font-size: 12.5px; color: var(--bad); white-space: pre-wrap; }
  .hint { font-size: 11.5px; color: var(--faint); line-height: 1.5; margin: 2px 0 0; }

  .stage-out { flex: 1; min-width: 0; aspect-ratio: 1; max-height: 70vh; border: 1px solid var(--border-soft); border-radius: 14px; background: var(--panel); display: grid; place-items: center; overflow: hidden; }
  .stage-out img, .stage-out video { max-width: 100%; max-height: 100%; border-radius: 12px; }
  .ph { color: var(--faint); font-size: 13px; }
</style>

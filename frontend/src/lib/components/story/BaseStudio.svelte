<script>
  // The BASE STUDIO — regenerate an outfit's base image deliberately: pick one of several art
  // STYLES (the active anchor, the broadcast style, curated alternates), MUTATE the prompt with
  // a plain-English instruction (DeepSeek V4 Pro), render 3 candidates, keep the one you like.
  // Picking a candidate saves it as the base (and persists a mutated attire prompt so future
  // renders match).
  import { get, post } from '$lib/api.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';
  import Modal from '$lib/components/shared/Modal.svelte';

  let { charKey, charName, outfit, onSaved = () => {}, onClose } = $props();

  const CURATED = [
    { id: '', name: 'Current anchor', text: '' },
    { id: 'cel', name: 'Vivid cel', text: 'Vivid modern anime cel illustration, crisp lineart, hard two-tone cel shading, saturated colors, glossy highlights, clean light background.' },
    { id: 'ink', name: 'Manga ink', text: 'Manga illustration, bold confident ink lineart, dramatic screentone shading, high-contrast values, sparse colour accents, clean white background.' },
    { id: 'paint', name: 'Painterly', text: 'Semi-realistic painterly anime illustration, soft brushwork, diffused warm lighting, richly blended shading, subtle canvas texture, plain warm-grey background.' },
    { id: 'water', name: 'Watercolor', text: 'Storybook watercolor illustration, delicate pencil lineart, translucent watercolor washes, soft paper texture, muted pastel palette, airy white background.' },
  ];
  let styles = $state([...CURATED]);
  let styleId = $state('');
  get('/style').then((r) => {   // get() returns the JSON itself
    if (r?.style) styles = [{ id: 'broadcast', name: '⭐ Broadcast style', text: r.style }, ...CURATED];
  });

  const origAttire = outfit?.attire_prompt || outfit?.prompt || '';
  let attire = $state(origAttire);
  let instr = $state('');
  let mutating = $state(false);
  let err = $state('');
  async function mutate() {
    if (mutating || !instr.trim()) return;
    mutating = true; err = '';
    const r = await post('/prompt/mutate', { prompt: attire, instruction: instr.trim() });
    mutating = false;
    if (r.ok && r.data?.prompt) { attire = r.data.prompt; instr = ''; }
    else err = r.data?.error || 'mutate failed';
  }

  let cands = $state([]);
  let rendering = $state(false);
  async function render(n = 3) {
    if (rendering) return;
    rendering = true; err = ''; cands = [];
    const st = styles.find((s) => s.id === styleId)?.text || '';
    const job = startJob('Outfit image render', charName, 'base-studio', n);
    for (let i = 0; i < n; i++) {
      const r = await limitedPost(`/characters/${charKey}/portraits/outfit/${outfit.id}/candidate`,
                                  { style: st, attire }, {}, job);
      if (r.ok && r.data?.image) { cands = [...cands, r.data.image]; job.done = i + 1; }
      else { err = r.data?.error || 'render failed'; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = 'done';
    rendering = false;
  }

  let saving = $state(false);
  async function pick(uri) {
    if (saving) return;
    saving = true; err = '';
    const r = await post(`/characters/${charKey}/portraits/outfit/${outfit.id}/base`, { data: uri });
    if (r.ok && attire.trim() && attire !== origAttire) {
      await post(`/characters/${charKey}/portraits/outfit/${outfit.id}`, { attire_prompt: attire });
    }
    saving = false;
    if (r.ok) { onSaved(); onClose?.(); }
    else err = r.data?.error || 'save failed';
  }
</script>

<Modal {onClose} width="880px" maxHeight="92vh" flush>
  <div class="bs">
    <div class="bshead">
      <b>🎛 {charName} — “{outfit?.name}” base</b>
      <span class="hint">style · prompt · candidates — pick the render you like</span>
      <button class="x" onclick={onClose}>✕</button>
    </div>
    <div class="bsbody">
      <div class="row wrap">
        {#each styles as s (s.id)}
          <button class="chip" class:on={styleId === s.id} onclick={() => (styleId = s.id)}
                  title={s.text || 'the active global style anchor'}>{s.name}</button>
        {/each}
      </div>
      <textarea class="attire" bind:value={attire} rows="6" spellcheck="false"></textarea>
      <div class="row">
        <input class="minstr" bind:value={instr} onkeydown={(e) => e.key === 'Enter' && mutate()}
               placeholder="mutate the prompt — e.g. “make the cloak deep green, moodier lighting”" />
        <button class="soft sm" onclick={mutate} disabled={mutating || !instr.trim()}>{mutating ? '…' : '✨ Mutate'}</button>
        <span class="sp"></span>
        <button class="primary sm" onclick={() => render(3)} disabled={rendering}>
          {rendering ? `Rendering ${cands.length}/3…` : '🎨 Render 3 candidates'}</button>
      </div>
      {#if err}<p class="err">{err}</p>{/if}
      {#if cands.length || rendering}
        <div class="cands">
          {#each cands as c, i (i)}
            <button class="cand" onclick={() => pick(c)} disabled={saving} title="Use this as the base">
              <img src={c} alt={`candidate ${i + 1}`} />
              <span class="use">{saving ? '…' : 'use ✓'}</span>
            </button>
          {/each}
          {#if rendering}<div class="cand ph"><span class="spin2"></span></div>{/if}
        </div>
      {/if}
    </div>
  </div>
</Modal>

<style>
  .bs { display: flex; flex-direction: column; min-height: 0; }
  .bshead { display: flex; align-items: center; gap: 10px; padding: 12px 16px;
            border-bottom: 1px solid var(--border-soft); font-size: 14px; }
  .bshead .x { margin-left: auto; background: none; border: 0; color: var(--faint); cursor: pointer; }
  .bsbody { padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; }
  .row { display: flex; align-items: center; gap: 8px; }
  .row.wrap { flex-wrap: wrap; }
  .chip { padding: 4px 11px; border-radius: 999px; background: var(--elev); font-size: 12px;
          border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .chip.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--elev)); }
  .attire { width: 100%; box-sizing: border-box; font: inherit; font-size: 12.5px; line-height: 1.5;
            padding: 9px 11px; border-radius: 9px; background: var(--elev); resize: vertical;
            border: 1px solid var(--border-soft); color: var(--text); }
  .attire:focus { outline: none; border-color: var(--accent); }
  .minstr { flex: 1; padding: 7px 10px; font: inherit; font-size: 12.5px; border-radius: 8px;
            background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); }
  .minstr:focus { outline: none; border-color: var(--accent); }
  .sp { flex: 1; }
  .err { margin: 0; font-size: 12px; color: var(--bad); }
  .cands { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .cand { position: relative; padding: 0; border-radius: 10px; overflow: hidden; cursor: pointer;
          background: var(--elev); border: 1px solid var(--border-soft); min-height: 220px; }
  .cand img { display: block; width: 100%; height: 320px; object-fit: contain; background: #d8d8d8; }
  .cand:hover { border-color: var(--accent); }
  .cand .use { position: absolute; bottom: 8px; left: 50%; transform: translateX(-50%);
               font-size: 12px; font-weight: 700; color: #fff; background: var(--accent);
               padding: 3px 12px; border-radius: 999px; opacity: 0; transition: opacity .15s; }
  .cand:hover .use { opacity: 1; }
  .cand.ph { display: grid; place-items: center; }
  .spin2 { width: 22px; height: 22px; border-radius: 50%; border: 3px solid rgba(109,140,255,.3);
           border-top-color: var(--accent); animation: bsp .7s linear infinite; }
  @keyframes bsp { to { transform: rotate(360deg); } }
</style>

<script>
  // Per-chapter regeneration / editing modal.
  // Opens when the user clicks ↻ or ✎ on a ChapterCard.
  // Supports inline editing of all chapter fields + optional LLM-driven regeneration.
  import { post } from '$lib/api.js';
  import { consumeSse } from '$lib/sse.js';
  import Modal from '$lib/components/shared/Modal.svelte';

  let {
    chapter = null,   // the chapter being edited
    index = 0,
    board = {},       // full board context passed to regen API
    charKey = '',
    storyKey = '',    // for per-location background generation
    locations = [],   // story locations — resolve this chapter's scene
    onSave = null,    // callback(updatedChapter)
    onBgPicked = null,// callback() after a background is selected (so cards refresh)
    onClose = null,   // callback when dismissed
  } = $props();

  // Local editable copies of all fields.
  let title         = $state('');
  let summary       = $state('');
  let emotionalCore = $state('');
  let hook          = $state('');
  let location      = $state('');
  let scenePrompt   = $state('');
  let instruction   = $state('');

  // SSE streaming state for regen.
  let regenBusy   = $state(false);
  let regenErr    = $state(null);
  let streamLines = $state([]);   // [{kind, text}]
  let regenDone   = $state(false);
  let streamBox;

  // Seed local state from the incoming chapter prop (once on mount; ignore later updates).
  $effect(() => {
    if (chapter) {
      title         = chapter.title         || '';
      summary       = chapter.summary       || '';
      emotionalCore = chapter.emotional_core || '';
      hook          = chapter.hook          || '';
      location      = chapter.location      || '';
      scenePrompt   = chapter.scene_prompt  || '';
    }
  });

  // ── Scene background (per location, shared by chapters in that place) ──────
  let bgBusy   = $state(false);
  let bgCands  = $state([]);
  let bgErr    = $state(null);
  const N_BG = 4;
  // Resolve the chapter's location (by id or name) to a location object.
  let locObj = $derived(
    (locations || []).find((l) => l.id === location
      || (l.name || '').toLowerCase() === (location || '').toLowerCase()) || null
  );

  async function genBg() {
    if (!locObj || !storyKey || bgBusy) return;
    bgBusy = true; bgErr = null; bgCands = [];
    for (let i = 0; i < N_BG; i++) {
      const r = await post(`/stories/${storyKey}/locations/${locObj.id}/background/candidate`, {});
      if (r.data?.image) bgCands = [...bgCands, r.data.image];
      else { bgErr = r.data?.error || 'render failed'; break; }
    }
    bgBusy = false;
  }
  async function useBg(dataUri) {
    if (!locObj) return;
    const r = await post(`/stories/${storyKey}/locations/${locObj.id}/background/select`, { data: dataUri });
    if (r.data?.url) { locObj.background = r.data.url; bgCands = []; onBgPicked?.(); }
  }

  function close() { onClose?.(); }

  function accept() {
    onSave?.({
      ...chapter,
      title,
      summary,
      emotional_core: emotionalCore,
      hook,
      location,
      scene_prompt: scenePrompt,
    });
  }

  function discard() {
    // Reset editable fields back to original chapter values.
    title         = chapter?.title         || '';
    summary       = chapter?.summary       || '';
    emotionalCore = chapter?.emotional_core || '';
    hook          = chapter?.hook          || '';
    location      = chapter?.location      || '';
    scenePrompt   = chapter?.scene_prompt  || '';
    regenErr      = null;
    streamLines   = [];
    regenDone     = false;
    instruction   = '';
  }

  function scrollStream() {
    queueMicrotask(() => { if (streamBox) streamBox.scrollTop = streamBox.scrollHeight; });
  }

  async function regen() {
    regenBusy = true;
    regenErr  = null;
    streamLines = [];
    regenDone = false;

    try {
      // POST triggers the job, which returns a direct SSE stream for this endpoint.
      const res = await fetch('/api/stories/chapter/regenerate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          character: charKey,
          board,
          chapter_index: index,
          instruction: instruction.trim(),
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        regenErr = data?.error || `HTTP ${res.status}`;
        regenBusy = false;
        return;
      }

      // Read the SSE stream directly from the response body.
      await consumeSse(res, (ev) => {
        if (ev.type === 'delta') {
          const last = streamLines[streamLines.length - 1];
          if (last && last.kind === 'delta') { last.text += ev.text; streamLines = [...streamLines]; }
          else streamLines = [...streamLines, { kind: 'delta', text: ev.text }];
          scrollStream();
        } else if (ev.type === 'phase') {
          streamLines = [...streamLines, { kind: 'phase', text: ev.label }];
          scrollStream();
        } else if (ev.type === 'chapter') {
          // Structured chapter result — populate editable fields.
          const c = ev.chapter;
          title         = c.title         || title;
          summary       = c.summary       || summary;
          emotionalCore = c.emotional_core || emotionalCore;
          hook          = c.hook          || hook;
          location      = c.location      || location;
          scenePrompt   = c.scene_prompt  || scenePrompt;
        } else if (ev.type === 'error') {
          regenErr = ev.error;
          streamLines = [...streamLines, { kind: 'error', text: ev.error }];
          scrollStream();
        } else if (ev.type === 'done') {
          regenDone = true;
        }
      });
    } catch (e) {
      regenErr = String(e);
    } finally {
      regenBusy = false;
      if (!regenDone && !regenErr) regenDone = true;
    }
  }
</script>

<Modal onClose={close} flush width="680px" maxHeight="90vh">
    <div class="dhead">
      <span class="chnum">Chapter {index + 1}</span>
      <h3 class="dtitle">{title || 'Untitled Chapter'}</h3>
      <button class="x" onclick={close} aria-label="Close">✕</button>
    </div>

    <div class="body">
      <!-- Editable fields -->
      <div class="fields">
        <div class="frow">
          <label class="fl">Title</label>
          <input class="fld" bind:value={title} placeholder="Chapter title" />
        </div>

        <div class="frow">
          <label class="fl">Narrative summary</label>
          <textarea class="fld ta" rows="3" bind:value={summary} placeholder="What happens in this chapter…"></textarea>
        </div>

        <div class="frow">
          <label class="fl">Emotional core</label>
          <input class="fld" bind:value={emotionalCore} placeholder="The feeling at the heart of this chapter…" />
        </div>

        <div class="frow half">
          <div>
            <label class="fl">Location</label>
            <input class="fld" bind:value={location} placeholder="Scene location" />
          </div>
          <div>
            <label class="fl">Hook</label>
            <input class="fld" bind:value={hook} placeholder="Ending hook or cliffhanger…" />
          </div>
        </div>

        <div class="frow">
          <label class="fl">Background prompt <span class="lo">— image generation tags for this scene</span></label>
          <textarea class="fld ta" rows="2" bind:value={scenePrompt} placeholder="booru tags for the scene background…"></textarea>
        </div>
      </div>

      <!-- Scene background — per location, shows on every card in that place -->
      {#if locObj}
        <div class="bg-section">
          <label class="fl">Scene background <span class="lo">— for "{locObj.name}", shared by every chapter set here</span></label>
          {#if locObj.background && !bgCands.length}
            <img class="bg-current" src={locObj.background} alt={locObj.name} />
          {/if}
          <div class="bg-acts">
            <button class="regen-btn" onclick={genBg} disabled={bgBusy}>
              {bgBusy ? `Rendering ${bgCands.length}/${N_BG}…` : (locObj.background ? '↻ New options' : `🖼 Generate background`)}
            </button>
          </div>
          {#if bgErr}<div class="err">⚠ {bgErr}</div>{/if}
          {#if bgCands.length}
            <div class="bg-cands">
              {#each bgCands as img, i (i)}
                <div class="bg-cand">
                  <img src={img} alt={`option ${i + 1}`} />
                  <button class="use" onclick={() => useBg(img)}>Use</button>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {:else if location}
        <div class="bg-note">No matching location "<b>{location}</b>" — add it in Story settings to generate a background.</div>
      {/if}

      <!-- Regen instruction + stream preview -->
      <div class="regen-section">
        <label class="fl">Regeneration instruction <span class="lo">(optional)</span></label>
        <textarea class="fld ta" rows="2" bind:value={instruction} placeholder="What to change? e.g. 'make the conflict more subtle' — blank = faithful refresh"></textarea>

        {#if streamLines.length > 0}
          <div class="stream" bind:this={streamBox}>
            <div class="shead">
              {#if regenBusy}<span class="spin" aria-hidden="true"></span>{:else}<span class="tick">✓</span>{/if}
              <span class="sttl">Regenerating chapter…{regenDone ? ' done' : ''}</span>
            </div>
            <div class="sbody">
              {#each streamLines as l, i (i)}
                {#if l.kind === 'phase'}<div class="sph">▸ {l.text}</div>
                {:else if l.kind === 'error'}<div class="ser">⚠ {l.text}</div>
                {:else}<pre class="sdl">{l.text}</pre>{/if}
              {/each}
            </div>
          </div>
        {/if}

        {#if regenErr && !streamLines.length}
          <div class="err">⚠ {regenErr}</div>
        {/if}
      </div>
    </div>

    <!-- Action bar -->
    <div class="foot">
      <button class="regen-btn" onclick={regen} disabled={regenBusy}>
        {regenBusy ? 'Regenerating…' : '↻ Regenerate'}
      </button>
      <span class="sp"></span>
      <button class="ghost" onclick={discard} disabled={regenBusy}>✗ Discard</button>
      <button class="accept-btn" onclick={accept} disabled={regenBusy}>✓ Accept</button>
    </div>
</Modal>

<style>
  .dhead {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 14px 16px 10px;
    border-bottom: 1px solid var(--border-soft);
    flex: none;
  }
  .chnum {
    font-size: 11px;
    font-weight: 700;
    color: var(--accent);
    background: rgba(109, 140, 255, .14);
    border-radius: 999px;
    padding: 2px 8px;
    flex: none;
  }
  .dtitle {
    margin: 0;
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .x {
    width: 28px;
    height: 28px;
    flex: none;
    padding: 0;
    border-radius: 7px;
    background: var(--elev);
    border: 1px solid var(--border);
    color: var(--muted);
    font-size: 11px;
  }
  .x:hover { color: var(--text); background: var(--elev-2); }

  .body {
    padding: 14px 16px;
    overflow: auto;
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .fields { display: flex; flex-direction: column; gap: 10px; }
  .frow { display: flex; flex-direction: column; gap: 4px; }
  .frow.half { flex-direction: row; gap: 12px; }
  .frow.half > div { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .fl {
    font-size: 10.5px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: .3px;
    font-weight: 600;
  }
  .lo { color: var(--faint); text-transform: none; letter-spacing: 0; font-weight: 400; }
  .fld {
    width: 100%;
    padding: 7px 9px;
    font-size: 13px;
    border-radius: 8px;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: inherit;
    box-sizing: border-box;
  }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.2)); }
  .ta { line-height: 1.5; resize: vertical; }

  .regen-section { display: flex; flex-direction: column; gap: 8px; }

  /* Scene background */
  .bg-section { display: flex; flex-direction: column; gap: 8px; }
  .bg-current { width: 100%; max-height: 200px; object-fit: cover; border-radius: 9px; border: 1px solid var(--border); }
  .bg-acts { display: flex; gap: 8px; }
  .bg-cands { display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 8px; }
  .bg-cand { display: flex; flex-direction: column; gap: 4px; }
  .bg-cand img { width: 100%; border-radius: 8px; border: 1px solid var(--border); display: block; }
  .bg-cand .use { font-size: 11px; padding: 4px 6px; border-radius: 6px; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); cursor: pointer; }
  .bg-cand .use:hover { border-color: var(--accent); color: var(--accent); }
  .bg-note { font-size: 12px; color: var(--faint); border: 1px dashed var(--border); border-radius: 8px; padding: 8px 11px; }

  /* Stream viewer */
  .stream {
    border: 1px solid var(--border);
    border-radius: 9px;
    overflow: hidden;
    background: var(--panel);
  }
  .shead {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 11px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text);
    background: rgba(109,140,255,.09);
    border-bottom: 1px solid var(--border-soft);
  }
  .sttl { color: var(--text); }
  .tick { color: var(--good, #8fc7a0); }
  .sbody {
    max-height: 180px;
    overflow: auto;
    padding: 7px 11px;
    font-size: 11.5px;
    line-height: 1.5;
  }
  .sph { color: var(--accent); font-weight: 600; margin: 6px 0 2px; }
  .ser { color: var(--bad, #ff7a7a); }
  .sdl { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, monospace; font-size: 11px; color: var(--muted); }

  .err { font-size: 12.5px; }

  .foot {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px 12px;
    border-top: 1px solid var(--border-soft);
    flex: none;
  }
  .sp { flex: 1; }
  .regen-btn {
    font-size: 12.5px;
    font-weight: 600;
    padding: 7px 14px;
    border-radius: 8px;
    background: var(--elev-2);
    border: 1px solid var(--border);
    color: var(--text);
    cursor: pointer;
  }
  .regen-btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  .regen-btn:disabled { opacity: .45; cursor: not-allowed; }
  .accept-btn {
    font-size: 13px;
    font-weight: 700;
    padding: 7px 18px;
    border-radius: 8px;
    background: var(--accent);
    border: 0;
    color: #fff;
    cursor: pointer;
  }
  .accept-btn:hover:not(:disabled) { filter: brightness(1.1); }
  .accept-btn:disabled { opacity: .45; cursor: not-allowed; }

  .spin {
    width: 12px;
    height: 12px;
    flex: none;
    border-radius: 50%;
    border: 2px solid rgba(109,140,255,.3);
    border-top-color: var(--accent);
    animation: spin .7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>

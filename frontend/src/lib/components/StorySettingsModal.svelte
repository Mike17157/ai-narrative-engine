<script>
  // Story-level settings as a centered modal — wraps the existing StoryEditor
  // (title / premise / tone / themes / cast / locations / chapters) so all that
  // editing now lives on the graph instead of a separate nav tab. Editing uses
  // the store's edit clone + autosave; closing flushes a final save and refreshes
  // the loaded story.
  import { chars } from '$lib/characters.svelte.js';
  import { stories, editStory, finishEdit } from '$lib/stories.svelte.js';
  import StoryEditor from '$lib/components/StoryEditor.svelte';

  let { onClose = null } = $props();

  let charItems = $derived(chars.list.filter((c) => !c.story).map((c) => ({ value: c.key, label: c.name || c.key })));

  editStory();   // clone current → edit buffer (synchronous, before first effect)

  // StoryEditor's own "Done" sets editing=null; mirror that into closing the modal.
  let primed = false;
  $effect(() => {
    if (stories.editing) { primed = true; return; }
    if (primed) onClose?.();
  });

  async function close() { await finishEdit(); onClose?.(); }
</script>

<div class="overlay" onclick={close} role="presentation">
  <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
    <div class="dhead">
      <h3 class="dtitle">Story settings</h3>
      <button class="x" onclick={close} aria-label="Close">✕</button>
    </div>
    <div class="body">
      <StoryEditor {charItems} />
    </div>
  </div>
</div>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') close(); }} />

<style>
  .overlay {
    position: fixed; inset: 0; z-index: 200;
    background: rgba(0, 0, 0, .6);
    display: grid; place-items: center;
    padding: 24px; backdrop-filter: blur(2px);
    animation: fade .12s ease;
  }
  .dlg {
    width: min(96vw, 820px); max-height: 90vh;
    display: flex; flex-direction: column;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-lg, 14px);
    box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55));
    animation: pop .13s ease; overflow: hidden;
  }
  .dhead {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 10px; border-bottom: 1px solid var(--border-soft); flex: none;
  }
  .dtitle { margin: 0; font-size: 15px; font-weight: 700; color: var(--text); flex: 1; }
  .x {
    width: 28px; height: 28px; flex: none; padding: 0; border-radius: 7px; box-shadow: none;
    background: var(--elev); border: 1px solid var(--border); color: var(--muted); font-size: 11px;
  }
  .x:hover { color: var(--text); background: var(--elev-2); filter: none; }
  .body { padding: 14px 16px; overflow: auto; flex: 1; }

  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

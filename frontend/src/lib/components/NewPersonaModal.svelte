<script>
  import { newPersonaState, resolveNewPersona } from '$lib/newpersona.svelte.js';

  // Confirm on Enter inside the name field (but not inside the textarea, where
  // Enter is a newline). Cancel on Escape — handled by the window binding below.
  let nameEl = $state();
  function confirm() {
    const name = newPersonaState.name.trim();
    if (!name) { nameEl?.focus(); return; }
    resolveNewPersona({ name, description: newPersonaState.description.trim() });
  }
  function cancel() { resolveNewPersona(null); }
</script>

{#if newPersonaState.open}
  <!-- The overlay closes on click/Escape; clicking inside the dialog must NOT bubble, so
       stopPropagation on both pointer + keyboard keeps an interior click from dismissing it. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="overlay" onclick={cancel} role="presentation"
    onkeydown={(e) => { if (e.key === 'Escape') cancel(); }}>
    <div class="dlg" role="dialog" aria-modal="true" tabindex="-1" onclick={(e) => e.stopPropagation()}>
      <h3 class="title">New persona</h3>
      <p class="msg">Who are you in the chat? Give the persona a name and a description — you can
        flesh it out and generate a picture afterwards.</p>

      <label for="npname">Name</label>
      <input id="npname" bind:this={nameEl} bind:value={newPersonaState.name}
        onkeydown={(e) => { if (e.key === 'Enter') confirm(); }}
        placeholder="e.g. Alex, the wanderer" />

      <label for="npdesc">Description <span class="lo">— optional; refine after creating</span></label>
      <textarea id="npdesc" rows="4" bind:value={newPersonaState.description}
        placeholder="Describe yourself — appearance, personality, how you carry yourself…"></textarea>

      <div class="acts">
        <button class="ghost" onclick={cancel}>Cancel</button>
        <button class:disabled={!newPersonaState.name.trim()} onclick={confirm}
          disabled={!newPersonaState.name.trim()}>Create persona</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (newPersonaState.open && e.key === 'Escape') cancel(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 80; background: rgba(6, 8, 12, .62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(92vw, 520px); background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-lg, 14px); box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55));
    padding: 18px 18px 16px; animation: pop .13s ease; }
  .title { margin: 0 0 8px; font-size: 16px; font-weight: 680; color: var(--text); }
  .msg { margin: 0 0 14px; font-size: 13.5px; line-height: 1.55; color: var(--muted); }
  .dlg label { margin: 12px 0 5px; }
  .dlg label:first-of-type { margin-top: 4px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .dlg textarea { resize: vertical; line-height: 1.5; }
  .acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

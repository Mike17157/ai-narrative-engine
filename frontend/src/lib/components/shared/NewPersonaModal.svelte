<script>
  import Modal from '$lib/components/shared/Modal.svelte';
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

<Modal open={newPersonaState.open} onClose={cancel} width="520px">
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
</Modal>

<style>
  .title { margin: 0 0 8px; font-size: 16px; font-weight: 680; color: var(--text); }
  .msg { margin: 0 0 14px; font-size: 13.5px; line-height: 1.55; color: var(--muted); }
  label { margin: 12px 0 5px; }
  label:first-of-type { margin-top: 4px; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  textarea { resize: vertical; line-height: 1.5; }
  .acts { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
</style>

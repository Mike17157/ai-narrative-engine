<script>
  let {
    value = '',
    placeholder = 'Click to add text',
    label = 'text',
    multiline = true,
    disabled = false,
    onsave = null
  } = $props();

  let editing = $state(false);
  let draft = $state('');
  let saving = $state(false);
  let error = $state('');
  let editor = $state(null);

  function begin() {
    if (disabled || saving) return;
    draft = String(value ?? '');
    error = '';
    editing = true;
    requestAnimationFrame(() => {
      editor?.focus();
      if (typeof editor?.setSelectionRange === 'function') {
        const end = editor.value.length;
        editor.setSelectionRange(end, end);
      }
    });
  }

  function cancel() {
    editing = false;
    draft = String(value ?? '');
    error = '';
  }

  async function commit() {
    if (saving) return;
    const next = draft.trim();
    if (next === String(value ?? '').trim()) {
      cancel();
      return;
    }
    saving = true;
    error = '';
    try {
      await onsave?.(next);
      editing = false;
    } catch (reason) {
      error = reason?.message || 'Could not save this edit.';
    } finally {
      saving = false;
    }
  }

  function keydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      cancel();
    } else if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      commit();
    }
  }
</script>

{#if editing}
  <span class="inline-editor" class:multiline>
    {#if multiline}
      <textarea bind:this={editor} bind:value={draft} onkeydown={keydown} aria-label={`Edit ${label}`} disabled={saving}></textarea>
    {:else}
      <input bind:this={editor} bind:value={draft} onkeydown={keydown} aria-label={`Edit ${label}`} disabled={saving} />
    {/if}
    <span class="edit-actions">
      {#if error}<small role="alert">{error}</small>{:else}<small>Enter to save · Shift+Enter for a new line · Esc to cancel</small>{/if}
      <button type="button" onclick={cancel} disabled={saving}>Cancel</button>
      <button class="save" type="button" onclick={commit} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
    </span>
  </span>
{:else}
  <button class="editable-copy" class:empty={!String(value ?? '').trim()} type="button" onclick={begin} disabled={disabled} aria-label={`Edit ${label}`} title={`Edit ${label}`}>
    <span>{String(value ?? '').trim() || placeholder}</span><i aria-hidden="true">Edit</i>
  </button>
{/if}

<style>
  .editable-copy { box-sizing: border-box; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; width: 100%; min-width: 0; margin: 0; border: 1px solid transparent; border-radius: 2px; padding: 4px 5px; background: transparent; color: inherit; text-align: left; font: inherit; line-height: inherit; cursor: text; }
  .editable-copy:hover, .editable-copy:focus-visible { border-color: color-mix(in srgb, var(--accent) 42%, var(--border)); background: color-mix(in srgb, var(--accent) 5%, transparent); outline: none; }
  .editable-copy span { min-width: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .editable-copy i { flex: none; opacity: 0; margin-top: 1px; color: var(--accent); font-size: 8px; font-style: normal; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; transition: opacity .14s ease; }
  .editable-copy:hover i, .editable-copy:focus-visible i { opacity: 1; }
  .editable-copy.empty span { color: var(--faint); font-style: italic; }
  .inline-editor { display: grid; gap: 6px; width: 100%; min-width: 0; }
  input, textarea { box-sizing: border-box; width: 100%; border: 1px solid var(--accent); border-radius: 2px; padding: 8px 9px; background: var(--bg); color: var(--text); font: inherit; line-height: 1.45; outline: 0; box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 10%, transparent); }
  textarea { min-height: 92px; resize: vertical; }
  .edit-actions { display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
  .edit-actions small { margin-right: auto; color: var(--faint); font-size: 8px; line-height: 1.3; }
  .edit-actions small[role='alert'] { color: var(--bad, #d0655a); }
  .edit-actions button { border: 1px solid var(--border-soft); border-radius: 2px; padding: 5px 7px; background: var(--elev); color: var(--muted); font: inherit; font-size: 8px; font-weight: 800; cursor: pointer; }
  .edit-actions button.save { border-color: var(--accent); background: var(--accent); color: #0b0e14; }
  .edit-actions button:disabled, .editable-copy:disabled { opacity: .55; cursor: default; }
</style>

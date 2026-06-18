// New-persona dialog. `await askNewPersona()` opens the modal and resolves to
// { name, description } on confirm, or null on cancel/dismiss. A single
// <NewPersonaModal/> mounted in the root layout renders the active request —
// the same singleton-store idiom as confirm.svelte.js.
export const newPersonaState = $state({
  open: false, name: '', description: '',
});
let _resolve = null;

export function askNewPersona({ name = '', description = '' } = {}) {
  return new Promise((resolve) => {
    newPersonaState.name = name;
    newPersonaState.description = description;
    _resolve = resolve;
    newPersonaState.open = true;
  });
}

// Resolve + close. Call with null (or falsy) to cancel.
export function resolveNewPersona(v) {
  newPersonaState.open = false;
  const r = _resolve;
  _resolve = null;
  if (r) r(v || null);
}

// Graph-driven prompt editor. `await openGraphPicker({tags, kind})` resolves to the edited tag
// array (Apply) or null (Cancel). A single <TagGraphModal/> mounted in the root layout renders the
// active request. Mirrors confirm.svelte.js.
export const pickerState = $state({ open: false, tags: [], kind: 'clothing', title: 'Edit prompt', target: null });
let _resolve = null;

export function openGraphPicker({ tags = [], kind = 'clothing', title = 'Edit prompt', target = null } = {}) {
  return new Promise((resolve) => {
    pickerState.tags = Array.isArray(tags) ? tags.slice() : [];
    pickerState.kind = kind === 'appearance' ? 'appearance' : 'clothing';
    pickerState.title = title;
    pickerState.target = target;
    _resolve = resolve;
    pickerState.open = true;
  });
}

export function resolveGraphPicker(result) {
  pickerState.open = false;
  const r = _resolve; _resolve = null;
  if (r) r(result ?? null);
}

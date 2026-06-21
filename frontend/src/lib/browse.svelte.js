// Global opener for the reusable EntityBrowseModal — the ONE way to reference one infra
// entity from another (attach lorebooks to a chat, add lorebooks to a preset, pick a preset
// for a lorebook…). Mirrors configModal.svelte.js: a single store + a single mount in the
// root layout, so any surface can call openBrowse() without wiring its own modal.
//
//   openBrowse({ kind, value, multi, title, filter, onConfirm })
//     kind      — 'lorebook' | 'preset'   (what to list)
//     value     — currently-selected id(s)  (array; single-select uses the first)
//     multi     — true → checkboxes + Confirm; false → click-to-pick + close
//     title     — modal heading
//     filter    — optional (entity) => boolean to narrow the list
//     onConfirm — callback(ids) with the chosen id array

export const browse = $state({
  open: false,
  kind: 'lorebook',
  value: [],
  multi: true,
  title: 'Browse',
  filter: null,
  onConfirm: null,
});

export function openBrowse(opts = {}) {
  browse.kind = opts.kind || 'lorebook';
  browse.value = opts.value ? [...opts.value] : [];
  browse.multi = opts.multi !== false;
  browse.title = opts.title || (browse.kind === 'preset' ? 'Browse presets' : 'Browse lorebooks');
  browse.filter = opts.filter || null;
  browse.onConfirm = opts.onConfirm || null;
  browse.open = true;
}

export function closeBrowse() {
  browse.open = false;
  browse.onConfirm = null;
  browse.filter = null;
}

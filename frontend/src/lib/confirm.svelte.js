// App-styled confirmation dialog. `await askConfirm({...})` resolves true/false.
// A single <ConfirmModal/> mounted in the root layout renders the active request.
export const confirmState = $state({
  open: false, title: '', message: '', confirmLabel: 'Confirm', cancelLabel: 'Cancel', danger: false,
});
let _resolve = null;

export function askConfirm({ title = 'Are you sure?', message = '', confirmLabel = 'Confirm',
                            cancelLabel = 'Cancel', danger = false } = {}) {
  return new Promise((resolve) => {
    confirmState.title = title; confirmState.message = message;
    confirmState.confirmLabel = confirmLabel; confirmState.cancelLabel = cancelLabel;
    confirmState.danger = danger;
    _resolve = resolve; confirmState.open = true;
  });
}
export function resolveConfirm(v) {
  confirmState.open = false;
  const r = _resolve; _resolve = null;
  if (r) r(v);
}

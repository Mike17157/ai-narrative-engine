// Svelte action: grow a <textarea> to fit its content so it never scrolls.
// Pass the bound value as the argument so it re-fits when set programmatically
// (e.g. streamed/regenerated content): use:autosize={someValue}.
export function autosize(el) {
  const fit = () => { el.style.height = 'auto'; el.style.height = el.scrollHeight + 'px'; };
  el.style.overflow = 'hidden';
  el.style.resize = 'none';
  requestAnimationFrame(fit);
  el.addEventListener('input', fit);
  return {
    update() { requestAnimationFrame(fit); },   // value changed → refit
    destroy() { el.removeEventListener('input', fit); }
  };
}

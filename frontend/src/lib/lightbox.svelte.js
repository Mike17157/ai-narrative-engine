// App-wide image lightbox state. Any image (via ZoomImage or openLightbox) can
// open the single global <Lightbox/> mounted at the root layout — so enlarge
// works everywhere, not just inside the LoRA section.
export const lightbox = $state({ src: null, cap: '' });
export function openLightbox(src, cap = '') { if (src) { lightbox.src = src; lightbox.cap = cap; } }
export function closeLightbox() { lightbox.src = null; lightbox.cap = ''; }

<script>
  // Standard image cell: fills its container, hover shows a ⤢ enlarge button, and
  // clicking opens the global lightbox. Pass `onpick` to give the image itself a
  // different click action (e.g. select), keeping ⤢ for enlarge.
  import { openLightbox } from '$lib/lightbox.svelte.js';
  // `inline` = size to the image's natural dimensions (capped by the container's
  // max-width), instead of filling a fixed-size cell. Used for chat bubbles.
  let { src, caption = '', alt = '', fit = 'cover', inline = false, onpick = null } = $props();
</script>

<div class="zwrap" class:inline>
  <img {src} alt={alt || caption} style={inline ? '' : `object-fit:${fit}`}
    onclick={() => (onpick ? onpick() : openLightbox(src, caption))} />
  <button class="zoom" title="Enlarge" aria-label="Enlarge"
    onclick={(e) => { e.stopPropagation(); openLightbox(src, caption); }}>⤢</button>
</div>

<style>
  .zwrap { position: relative; width: 100%; height: 100%; }
  .zwrap img { width: 100%; height: 100%; display: block; cursor: zoom-in; border-radius: inherit; }
  .zwrap.inline { width: auto; height: auto; display: inline-block; border-radius: inherit; }
  .zwrap.inline img { width: auto; height: auto; max-width: 100%; }
  .zoom {
    position: absolute; top: 6px; right: 6px; padding: 2px 7px; font-size: 13px; line-height: 1;
    border-radius: 7px; background: rgba(20, 24, 34, .7); border: 1px solid var(--border); color: #fff;
    box-shadow: none; opacity: 0; transition: opacity .12s;
  }
  .zwrap:hover .zoom { opacity: 1; }
  .zoom:hover { background: rgba(20, 24, 34, .92); filter: none; }
</style>

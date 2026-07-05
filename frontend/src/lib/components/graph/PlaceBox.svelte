<script>
  // A GROUP container node — a labelled box that holds the character nodes of one grouping
  // (a home location, a family/household). Character nodes are children (parentId) so they live
  // inside the box. Non-interactive; it's just the frame. Hue is assigned per group.
  let { data } = $props();
</script>

<div class="pbox" style="--hue:{data.hue}">
  <div class="pbox-h"><span class="pin">{data.icon || '🚉'}</span> {data.name}</div>
</div>

<style>
  .pbox { width: 100%; height: 100%; box-sizing: border-box; border-radius: 16px;
          border: 1.5px dashed hsl(var(--hue) 45% 58% / .55);
          background: hsl(var(--hue) 45% 50% / .07); }
  .pbox-h { display: flex; align-items: center; gap: 6px; padding: 8px 14px;
            font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .5px;
            color: hsl(var(--hue) 50% 72%); }
  .pin { font-size: 13px; }
</style>

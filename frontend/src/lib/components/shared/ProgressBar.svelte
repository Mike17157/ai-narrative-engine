<script>
  // Thin progress bar, determinate or indeterminate.
  //   <ProgressBar {value} {max} />        → fills to value/max
  //   <ProgressBar value={null} />          → indeterminate sweep (unknown total)
  // Previously this exact markup+CSS was copy-pasted in 5 places (lora cards + ActivityMenu).
  let { value = null, max = 100, height = '8px', margin = '0', grow = false } = $props();

  let indet = $derived(value == null);
  let pct = $derived(indet ? 0 : (max ? Math.max(0, Math.min(100, Math.round((value / max) * 100))) : 0));
</script>

<div class="bar" class:indet class:grow style="height:{height}; margin:{margin}">
  <span style={indet ? '' : `width:${pct}%`}></span>
</div>

<style>
  .bar { border-radius: 999px; background: var(--elev); overflow: hidden; border: 1px solid var(--border); }
  .bar.grow { flex: 1; }
  .bar span { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #9a6dff); transition: width .3s; }
  .bar.indet span { width: 30%; animation: pbslide 1.1s ease-in-out infinite; }
  @keyframes pbslide { 0% { margin-left: -30%; } 100% { margin-left: 100%; } }
</style>

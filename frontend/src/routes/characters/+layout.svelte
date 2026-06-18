<script>
  import { onMount } from 'svelte';
  import { chars, loadChars } from '$lib/characters.svelte.js';

  let { children } = $props();
  // The folder tree (Selected / Browse / Import) is rendered by the root layout's TreeNav.
  onMount(loadChars);
</script>

<div class="page">
  <div class="col wide">
    {#if chars.msg}<div class="impmsg" class:ok={chars.msg.ok} class:err={chars.msg.err}>{chars.msg.text}</div>{/if}
    {@render children()}
  </div>
</div>

<style>
  .impmsg { margin-bottom: 12px; font-size: 13px; }
  .impmsg.ok { color: var(--good); }
  .impmsg.err { color: var(--bad); }
</style>

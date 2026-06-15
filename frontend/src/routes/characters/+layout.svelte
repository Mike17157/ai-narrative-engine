<script>
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';
  import { app, setSubnav } from '$lib/app.svelte.js';
  import { chars, loadChars } from '$lib/characters.svelte.js';

  let { children } = $props();

  let path = $derived($page.url.pathname);
  let curTab = $derived(['selected', 'search', 'import', 'personas'].find((id) => path === `/characters/${id}`) || 'selected');
  let activeChar = $derived(chars.list.find((c) => c.key === app.activeChar) || null);

  // Drive the global sub-header controller (standard nav for every section).
  $effect(() => {
    setSubnav({
      title: 'Characters',
      items: [
        { id: 'selected', label: activeChar ? `Selected · ${activeChar.name || activeChar.key}` : 'Selected' },
        { id: 'search', label: `Browse (${chars.list.length})` },
        { id: 'import', label: 'Import card' },
        { id: 'personas', label: 'Personas' }
      ],
      value: curTab,
      onpick: (id) => goto(`/characters/${id}`)
    });
  });

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

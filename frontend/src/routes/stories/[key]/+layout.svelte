<script>
  import { page } from '$app/stores';
  import { loadStory } from '$lib/stories.svelte.js';
  import StoryNavigator from '$lib/components/story/StoryNavigator.svelte';
  import SectionChat from '$lib/components/story/SectionChat.svelte';

  let { children } = $props();
  let key = $derived($page.params.key);
  let path = $derived($page.url.pathname);
  let search = $derived($page.url.search || '');

  // Load on key change inside an EFFECT (loadStory mutates the store; an impure $derived would loop).
  let storyPromise = $state(Promise.resolve(null));
  let reqKey = '';
  $effect(() => {
    const k = key;
    if (!k || reqKey === k) return;
    reqKey = k;
    storyPromise = loadStory(k);
  });

  // Explorer collapse — persisted; drives --storynav-w.
  const ls = (fn, d) => { try { return typeof localStorage !== 'undefined' ? fn() : d; } catch { return d; } };
  let navCollapsed = $state(ls(() => localStorage.getItem('loom.storynav.collapsed') === '1', false));
  function toggleNav() { navCollapsed = !navCollapsed; ls(() => localStorage.setItem('loom.storynav.collapsed', navCollapsed ? '1' : '0')); }
  let navW = $derived(navCollapsed ? '0px' : '248px');

  // The section EDITOR now lives at the shell (outside individual panes) and FOLLOWS the current view:
  // its edit target (card layer) is derived from the route. Null → no editor (play / outfits).
  function editorFor(p, s) {
    if (/\/play\/?$/.test(p) || /\/cast\/?$/.test(p) || /\/prompts\/?$/.test(p)) return null;
    if (p.includes('/characters')) return { layer: 'relationships', label: 'Characters' };
    if (p.includes('/arcs') || p.includes('/scenes')) return { layer: 'plot', label: 'Arcs & scenes' };
    const tab = new URLSearchParams(s).get('tab') || 'overview';
    const M = { overview: 'Overview', map: 'World', relationships: 'Relationships', plot: 'Plot' };
    return { layer: M[tab] ? tab : 'overview', label: M[tab] || 'Overview' };
  }
  let editor = $derived(editorFor(path, search));
  let chatCollapsed = $state(ls(() => localStorage.getItem('loom.storychat.collapsed') === '1', false));
  function toggleChat() { chatCollapsed = !chatCollapsed; ls(() => localStorage.setItem('loom.storychat.collapsed', chatCollapsed ? '1' : '0')); }
  let showChat = $derived(!!editor && !chatCollapsed);
  let chatW = $derived(showChat ? '336px' : '0px');
</script>

{#await storyPromise}
  <div class="page"><div class="col"><p class="lo">Loading…</p></div></div>
{:then story}
  {#if story && story.key === key}
    <div class="storyshell" style:--storynav-w={navW} style:--storychat-w={chatW}>
      <StoryNavigator collapsed={navCollapsed} onToggle={toggleNav} />
      {#if showChat}
        <SectionChat storyKey={key} layer={editor.layer} layerLabel={editor.label} onCollapse={toggleChat} />
      {:else if editor}
        <button class="chatreopen" onclick={toggleChat} title="Show editor" aria-label="Show editor">✎</button>
      {/if}
      <div class="storybody">{@render children()}</div>
    </div>
  {:else}
    <div class="page"><div class="col"><p class="lo">Story not found.</p></div></div>
  {/if}
{:catch}
  <div class="page"><div class="col"><p class="lo">Couldn’t load this story.</p></div></div>
{/await}

<style>
  .lo { color: var(--muted); font-size: 13px; }
  /* Content clears the fixed explorer (left) + the fixed editor (docked beside it). Both widths are
     inherited CSS vars so the fixed SectionChat docks at left:--storynav-w and the body offsets by both. */
  .storybody { padding-left: calc(var(--storynav-w, 248px) + var(--storychat-w, 0px)); transition: padding-left .16s ease; }
  @media (max-width: 900px) { .storybody { padding-left: 0; } }
  .chatreopen {
    position: fixed; left: calc(var(--storynav-w, 0px) + 8px); top: calc(var(--chrome-top, 48px) + 40px); z-index: 46;
    width: 26px; height: 26px; display: grid; place-items: center; padding: 0;
    background: var(--elev); border: 1px solid var(--border); border-radius: 8px; color: var(--muted); cursor: pointer;
    box-shadow: 0 2px 8px rgba(0,0,0,.3);
  }
  .chatreopen:hover { color: var(--accent); border-color: var(--accent); }
</style>

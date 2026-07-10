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
  // The editor lives in the BOTTOM half of the screen. Only 'bottom' (default) and 'hidden' remain —
  // the old side-dock and modal modes were removed. Any legacy stored value normalizes to 'bottom'.
  const CHAT_LS = 'loom.storychat.mode';
  let chatMode = $state(ls(() => localStorage.getItem(CHAT_LS) === 'hidden' ? 'hidden' : 'bottom', 'bottom'));
  function setChatMode(m) { chatMode = m; ls(() => localStorage.setItem(CHAT_LS, m)); }
  const hideChat = () => setChatMode('hidden');
  const showChatPanel = () => setChatMode('bottom');
  // The bottom half the body clears. '0vh' (not '0px') when hidden so the padding-bottom TRANSITION
  // interpolates cleanly — a var flipping between vh and px units gets stuck mid-animation in Chromium.
  let chatH = $derived(!!editor && chatMode !== 'hidden' ? '50vh' : '0vh');
</script>

{#await storyPromise}
  <div class="page"><div class="col"><p class="lo">Loading…</p></div></div>
{:then story}
  {#if story && story.key === key}
    <div class="storyshell" style:--storynav-w={navW} style:--storychat-h={chatH}>
      <StoryNavigator collapsed={navCollapsed} onToggle={toggleNav} />
      {#if editor}
        <!-- Mounted whenever there's an editor (even when hidden) so the CONVERSATION survives
             hide/show — SectionChat renders nothing while presentation==='hidden'. -->
        <SectionChat storyKey={key} layer={editor.layer} layerLabel={editor.label}
                     presentation={chatMode} onCollapse={hideChat}
                     interview={!story.premise && !(story.premise_parts?.question)
                                && (editor.layer === 'overview' || editor.layer === 'map')} />
      {/if}
      {#if chatMode === 'hidden' && editor}
        <button class="chatreopen" onclick={showChatPanel} title="Show editor" aria-label="Show editor">✎</button>
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
  /* Both offsets transition; padding-bottom uses vh↔vh (see chatH) so it interpolates in sync with
     the panel's slide (.24s) instead of getting stuck on a mixed-unit var change. */
  .storybody { padding-left: var(--storynav-w, 248px);
               padding-bottom: var(--storychat-h, 0vh);
               transition: padding-left .16s ease, padding-bottom .24s cubic-bezier(.4, 0, .2, 1); }
  @media (max-width: 900px) { .storybody { padding-left: 0; } }
  /* The reopen "edit" button — a floating action button pinned to the bottom-right. */
  .chatreopen {
    position: fixed; right: 18px; bottom: 18px; z-index: 46;
    width: 44px; height: 44px; display: grid; place-items: center; padding: 0; font-size: 18px;
    background: var(--accent); border: none; border-radius: 50%; color: #0b0e14; cursor: pointer;
    box-shadow: 0 6px 20px rgba(0,0,0,.35); transition: transform .16s ease, box-shadow .16s ease;
  }
  .chatreopen:hover { transform: translateY(-2px); box-shadow: 0 10px 26px rgba(0,0,0,.45); }
</style>

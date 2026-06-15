<script>
  import '../app.css';
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import { goto, afterNavigate, beforeNavigate } from '$app/navigation';
  import { app, subnav, clearSubnav, refreshAll, refreshHealth, refreshActivity } from '$lib/app.svelte.js';
  import ActivityMenu from '$lib/components/ActivityMenu.svelte';
  import DetailsMenu from '$lib/components/DetailsMenu.svelte';
  import Lightbox from '$lib/components/Lightbox.svelte';
  import ConfirmModal from '$lib/components/ConfirmModal.svelte';

  let { children } = $props();

  let showActivity = $state(false);
  let showDetails = $state(false);
  const closeMenus = () => { showActivity = false; showDetails = false; };

  let comfyUp = $derived(app.health?.comfyui?.up);
  let who = $derived(app.health?.profile?.name || '');
  let running = $derived(app.activity?.running || 0);
  let path = $derived($page.url.pathname);
  const isActive = (id) => path === `/${id}` || path.startsWith(`/${id}/`);

  // Major categories — a left icon rail (ComfyUI / VS Code style). Selecting one opens
  // its dedicated panel (the section's sub-navigation, from the `subnav` store).
  const nav = [
    { id: 'chat', label: 'Chat', icon: '💬', href: '/chat' },
    { id: 'characters', label: 'Characters', icon: '👥', href: '/characters/selected' },
    { id: 'stories', label: 'Stories', icon: '📖', href: '/stories' },
    { id: 'images', label: 'Images', icon: '🖼', href: '/images/models' },
    { id: 'settings', label: 'Settings', icon: '⚙', href: '/settings' }
  ];

  // Persist the last route per section, and the panel open/closed state.
  const LS_LAST = 'loom.lastRoute', LS_PANEL = 'loom.panelOpen';
  const ls = (fn, d) => { try { return typeof localStorage !== 'undefined' ? fn() : d; } catch { return d; } };
  let lastRoute = $state(ls(() => JSON.parse(localStorage.getItem(LS_LAST) || '{}'), {}));
  let panelOpen = $state(ls(() => localStorage.getItem(LS_PANEL) !== '0', true));
  $effect(() => { ls(() => localStorage.setItem(LS_PANEL, panelOpen ? '1' : '0')); });

  // Cache the FULL last route per section (path + query), so jumping between Chat / Characters /
  // Stories / Images returns to the exact pane — including query-driven state like the open cast
  // member (?c=…) or image sub-tab.
  afterNavigate(({ to }) => {
    const seg = to?.url?.pathname?.split('/')[1];
    if (seg) {
      lastRoute[seg] = to.url.pathname + (to.url.search || '');
      ls(() => localStorage.setItem(LS_LAST, JSON.stringify(lastRoute)));
    }
  });
  function go(n) {
    if (isActive(n.id)) { panelOpen = !panelOpen; return; }   // re-click active = toggle panel
    panelOpen = true;
    goto(lastRoute[n.id] || n.href);
  }

  // The active section's panel shows its sub-navigation (if any).
  let hasPanel = $derived(subnav.items.length > 0);
  let activeLabel = $derived(nav.find((n) => isActive(n.id))?.label || '');

  // Drop a section's sub-items when switching sections; the destination repopulates them.
  beforeNavigate(({ from, to }) => {
    const seg = (u) => u?.url?.pathname.split('/')[1] || '';
    if (seg(from) !== seg(to)) clearSubnav();
  });

  // Honor programmatic deep-links (e.g. Train → Settings) as route navigations.
  $effect(() => { if (app.nav.screen) { const s = app.nav.screen; app.nav.screen = null; goto(`/${s}`); } });

  let timer, atimer;
  onMount(async () => {
    await refreshAll();
    await refreshActivity();
    timer = setInterval(refreshHealth, 6000);
    atimer = setInterval(refreshActivity, 3000);
  });
  onDestroy(() => { clearInterval(timer); clearInterval(atimer); });
</script>

<svelte:window onclick={closeMenus} />

<div class="app">
  <!-- icon rail: major categories -->
  <nav class="rail">
    <div class="mark" aria-hidden="true"></div>
    <div class="railnav">
      {#each nav.filter((n) => n.id !== 'settings') as n (n.id)}
        <button class="railbtn" class:on={isActive(n.id)} onclick={() => go(n)} title={n.label}>
          <span class="ricon">{n.icon}</span><span class="rlabel">{n.label}</span>
        </button>
      {/each}
    </div>
    <div class="railfoot">
      <div class="actwrap">
        <button class="ico" class:busy={running > 0}
          onclick={(e) => { e.stopPropagation(); const v = !showActivity; closeMenus(); showActivity = v; }}
          title="Live workloads + ComfyUI" aria-label="Activity">
          ⚡{#if running > 0}<span class="badge">{running}</span>{/if}
        </button>
        {#if showActivity}<ActivityMenu onnavigate={(s) => { if (s) goto(`/${s}`); showActivity = false; }} />{/if}
      </div>
      <div class="actwrap">
        <button class="ico" onclick={(e) => { e.stopPropagation(); const v = !showDetails; closeMenus(); showDetails = v; }}
          title="Active models" aria-label="Active models">ⓘ</button>
        {#if showDetails}<DetailsMenu />{/if}
      </div>
      <span class="cdot {comfyUp ? 'up' : 'down'}" title={comfyUp ? 'ComfyUI live' : 'ComfyUI off'}></span>
      <button class="railbtn sm" class:on={isActive('settings')} onclick={() => go(nav[4])} title="Settings">
        <span class="ricon">⚙</span>
      </button>
    </div>
  </nav>

  <!-- panel: the active category's sub-navigation -->
  {#if hasPanel && panelOpen}
    <aside class="panel">
      <div class="phead">{activeLabel}</div>
      <div class="ptree">
        {#each subnav.items as it (it.id)}
          <button class="pitem" class:on={it.id === subnav.value} onclick={() => subnav.onpick?.(it.id)}>{it.label}</button>
          {#if it.id === subnav.value && subnav.sub?.items?.length}
            <div class="pchildren">
              {#each subnav.sub.items as s (s.id)}
                <button class="pitem deep" class:on={s.id === subnav.sub.value} disabled={s.disabled} onclick={() => subnav.sub.onpick?.(s.id)}>{s.label}</button>
                {#if s.id === subnav.sub.value && s.children?.length}
                  <div class="pchildren third">
                    {#each s.children as ch (ch.id)}
                      <button class="pitem deeper" class:on={ch.id === subnav.sub.childValue} onclick={() => subnav.sub.onpickChild?.(ch.id)}>{ch.label}</button>
                    {/each}
                  </div>
                {/if}
              {/each}
            </div>
          {/if}
        {/each}
      </div>
    </aside>
  {/if}

  <main>{@render children()}</main>
</div>

<Lightbox />
<ConfirmModal />

<style>
  .app { display: flex; height: 100vh; }

  /* icon rail */
  .rail {
    width: 72px; flex: none; display: flex; flex-direction: column; align-items: center; gap: 4px;
    padding: 12px 6px 10px; background: #12151d; border-right: 1px solid var(--border);
  }
  .mark { width: 24px; height: 24px; border-radius: 7px; transform: rotate(45deg); margin-bottom: 10px;
          background: linear-gradient(135deg, var(--accent), #9a6dff); box-shadow: 0 0 14px var(--accent-glow); }
  .railnav { display: flex; flex-direction: column; gap: 4px; width: 100%; }
  .railbtn {
    width: 100%; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 9px 2px;
    border: 0; box-shadow: none; background: none; color: var(--muted); border-radius: 10px; cursor: pointer;
  }
  .railbtn:hover { color: var(--text); background: var(--elev); filter: none; }
  .railbtn.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1px var(--accent); }
  .ricon { font-size: 19px; line-height: 1; }
  .rlabel { font-size: 10px; font-weight: 600; letter-spacing: .2px; }
  .railbtn.sm { padding: 8px 2px; } .railbtn.sm .ricon { font-size: 16px; }
  .railfoot { margin-top: auto; display: flex; flex-direction: column; align-items: center; gap: 8px; padding-top: 8px; }

  .actwrap { position: relative; }
  .actwrap :global(.menu) { top: auto; bottom: 0; left: calc(100% + 8px); right: auto; }
  .ico {
    position: relative; width: 34px; height: 34px; display: grid; place-items: center; padding: 0;
    font-size: 15px; border-radius: 9px; box-shadow: none; border: 1px solid var(--border);
    background: var(--elev); color: var(--muted);
  }
  .ico:hover { color: var(--text); filter: none; background: var(--elev-2); }
  .ico.busy { color: var(--accent); border-color: rgba(109, 140, 255, .4); background: rgba(109, 140, 255, .1); }
  .ico .badge {
    position: absolute; top: -5px; right: -5px; min-width: 16px; height: 16px; padding: 0 4px;
    border-radius: 999px; background: var(--accent); color: #0b0e14; font-size: 11px; font-weight: 800;
    display: grid; place-items: center;
  }
  .cdot { width: 8px; height: 8px; border-radius: 50%; box-shadow: 0 0 8px currentColor; }
  .cdot.up { color: var(--good); background: var(--good); }
  .cdot.down { color: var(--bad); background: var(--bad); }

  /* category panel */
  .panel {
    width: 216px; flex: none; display: flex; flex-direction: column; padding: 12px 10px;
    background: linear-gradient(180deg, #171b25, #13161e); border-right: 1px solid var(--border);
  }
  .phead { font-size: 15px; font-weight: 680; color: #fff; padding: 2px 8px 12px; }
  .ptree { display: flex; flex-direction: column; gap: 1px; overflow-y: auto; }
  .pitem {
    text-align: left; padding: 8px 11px; border-radius: 8px; font-size: 13.5px; font-weight: 560;
    color: var(--muted); background: none; border: 0; box-shadow: none;
  }
  .pitem:hover { color: var(--text); background: var(--elev); filter: none; }
  .pitem.on { color: #fff; background: var(--elev); box-shadow: inset 0 0 0 1px var(--accent); }
  .pitem:disabled { opacity: .4; cursor: default; } .pitem:disabled:hover { background: none; color: var(--muted); }
  .pchildren { display: flex; flex-direction: column; gap: 1px; margin: 2px 0 6px 12px;
               padding-left: 8px; border-left: 1px solid var(--border-soft); }
  .pchildren.third { margin: 1px 0 4px 10px; }
  .pitem.deep { font-size: 12.5px; padding: 6px 10px; }
  .pitem.deeper { font-size: 12px; padding: 5px 10px; color: var(--faint); }
  .pitem.deeper.on { color: #fff; }

  main { flex: 1; min-width: 0; position: relative; display: flex; flex-direction: column; overflow: auto; }
</style>

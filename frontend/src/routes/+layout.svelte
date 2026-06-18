<script>
  import '../app.css';
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import { goto, afterNavigate } from '$app/navigation';
  import { app, refreshAll, refreshHealth, refreshActivity, startPruneTimer,
           migratePersonas, refreshPersonas, setActivePersona } from '$lib/app.svelte.js';
  import { post } from '$lib/api.js';
  import { askNewPersona } from '$lib/newpersona.svelte.js';
  import { treeFor } from '$lib/nav.svelte.js';
  import TreeNav from '$lib/components/TreeNav.svelte';
  import ActivityMenu from '$lib/components/ActivityMenu.svelte';
  import DetailsMenu from '$lib/components/DetailsMenu.svelte';
  import Lightbox from '$lib/components/Lightbox.svelte';
  import ConfirmModal from '$lib/components/ConfirmModal.svelte';
  import NewPersonaModal from '$lib/components/NewPersonaModal.svelte';
  import TagGraphModal from '$lib/components/TagGraphModal.svelte';

  let { children } = $props();

  let showActivity = $state(false);
  let showDetails = $state(false);
  const closeMenus = () => { showActivity = false; showDetails = false; };

  let comfyUp = $derived(app.health?.comfyui?.up);
  let running = $derived(app.activity?.running || 0);
  let path = $derived($page.url.pathname);
  let search = $derived($page.url.search || '');
  const isActive = (id) => path === `/${id}` || path.startsWith(`/${id}/`);

  // Major categories — a left icon rail (ComfyUI / VS Code style). The rail picks
  // a section; TreeNav (the panel) shows that section's folder tree.
  const nav = [
    { id: 'chat', label: 'Chat', icon: '💬', href: '/chat' },
    { id: 'characters', label: 'Characters', icon: '👥', href: '/characters/selected' },
    { id: 'stories', label: 'Stories', icon: '📖', href: '/stories' },
    { id: 'images', label: 'Images', icon: '🖼', href: '/images/models' },
    { id: 'settings', label: 'Settings', icon: '⚙', href: '/settings' }
  ];

  // Sections that own a folder tree. Chat has none (it's a single surface).
  const TREE_SECTIONS = new Set(['characters', 'stories', 'images', 'settings']);
  let section = $derived(path.split('/')[1] || '');

  // Cache the full last route per section (path + query) so jumping between
  // sections returns to the exact pane — query-driven state like ?c=… or ?section=…
  const LS_LAST = 'loom.lastRoute';
  const ls = (fn, d) => { try { return typeof localStorage !== 'undefined' ? fn() : d; } catch { return d; } };
  let lastRoute = $state(ls(() => JSON.parse(localStorage.getItem(LS_LAST) || '{}'), {}));
  afterNavigate(({ to }) => {
    const seg = to?.url?.pathname?.split('/')[1];
    if (seg) {
      lastRoute[seg] = to.url.pathname + (to.url.search || '');
      ls(() => localStorage.setItem(LS_LAST, JSON.stringify(lastRoute)));
    }
  });
  function go(n) { goto(lastRoute[n.id] || n.href); }

  let activeLabel = $derived(nav.find((n) => isActive(n.id))?.label || '');
  // The tree for the active section. `treeFor` reads store state, so it stays live
  // (character/story counts, wizard progress) — wrap in $derived so it recomputes.
  let tree = $derived(TREE_SECTIONS.has(section) ? treeFor(section) : []);
  let activeHref = $derived(path + search);   // include query so ?section= config leaves match

  // Honor programmatic deep-links (e.g. Train → Settings) as route navigations.
  $effect(() => { if (app.nav.screen) { const s = app.nav.screen; app.nav.screen = null; goto(`/${s}`); } });

  let timer, atimer, ptimer;

  // One-shot first-view persona gate: if you have no persona (or none selected)
  // when the app loads, prompt you to describe yourself before anything else.
  // The persona is who *you* are — now the player identity the story director
  // narrates to, not just the legacy-chat {{user}}. Guarded by a module-level
  // flag so it fires at most once per page session, never nagging on reloads.
  let personaGateRan = false;
  async function maybePromptPersona() {
    if (personaGateRan) return;
    personaGateRan = true;
    const need = app.personas.length === 0 || !app.activePersona ||
                 !app.personas.some((p) => p.id === app.activePersona);
    if (!need) return;
    const res = await askNewPersona();
    if (!res) return;                       // dismissed — non-blocking, "You" fallback
    const r = await post('/personas', { name: res.name, description: res.description });
    if (r.data?.key) {
      setActivePersona(r.data.key);
      await refreshPersonas();
    }
  }

  onMount(async () => {
    await refreshAll();
    await refreshActivity();
    await migratePersonas();   // one-shot: legacy localStorage personas → server YAML
    await refreshPersonas();   // then load the server-backed list into the shared store
    void maybePromptPersona(); // overlay the gate on the Library (no await — don't block the UI)
    timer = setInterval(refreshHealth, 6000);
    atimer = setInterval(refreshActivity, 3000);
    ptimer = startPruneTimer();
  });
  onDestroy(() => { clearInterval(timer); clearInterval(atimer); clearInterval(ptimer); });
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

  <!-- panel: the active section's folder tree (real TreeNav, solid-fill active) -->
  {#if tree.length}
    <aside class="panel">
      <div class="phead">{activeLabel}</div>
      <div class="ptree">
        <TreeNav nodes={tree} activeHref={activeHref} />
      </div>
    </aside>
  {/if}

  <main>{@render children()}</main>
</div>

<Lightbox />
<ConfirmModal />
<NewPersonaModal />
<TagGraphModal />

<style>
  .app { display: flex; height: 100vh; }

  /* icon rail */
  .rail {
    width: 72px; flex: none; display: flex; flex-direction: column; align-items: center; gap: 4px;
    padding: 12px 6px 10px; background: #12151d; border-right: 1px solid var(--border);
  }
  .mark { width: 24px; height: 24px; border-radius: 7px; transform: rotate(45deg); margin-bottom: 10px;
          background: linear-gradient(135deg, var(--accent), #9a6dff); }
  .railnav { display: flex; flex-direction: column; gap: 4px; width: 100%; }
  .railbtn {
    width: 100%; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 9px 2px;
    border: 0; background: none; color: var(--muted); border-radius: 10px; cursor: pointer;
  }
  .railbtn:hover { color: var(--text); background: var(--elev); }
  .railbtn.on { color: #fff; background: var(--elev-2); }
  .ricon { font-size: 19px; line-height: 1; }
  .rlabel { font-size: 10px; font-weight: 600; letter-spacing: .2px; }
  .railbtn.sm { padding: 8px 2px; } .railbtn.sm .ricon { font-size: 16px; }
  .railfoot { margin-top: auto; display: flex; flex-direction: column; align-items: center; gap: 8px; padding-top: 8px; }

  .actwrap { position: relative; }
  .actwrap :global(.menu) { top: auto; bottom: 0; left: calc(100% + 8px); right: auto; }
  .ico {
    position: relative; width: 34px; height: 34px; display: grid; place-items: center; padding: 0;
    font-size: 15px; border: 1px solid var(--border);
    background: var(--elev); color: var(--muted);
  }
  .ico:hover { color: var(--text); background: var(--elev-2); }
  .ico.busy { color: var(--accent); border-color: rgba(109, 140, 255, .4); background: rgba(109, 140, 255, .1); }
  .ico .badge {
    position: absolute; top: -5px; right: -5px; min-width: 16px; height: 16px; padding: 0 4px;
    border-radius: 999px; background: var(--accent); color: #0b0e14; font-size: 11px; font-weight: 800;
    display: grid; place-items: center;
  }
  .cdot { width: 8px; height: 8px; border-radius: 50%; }
  .cdot.up { background: var(--good); }
  .cdot.down { background: var(--bad); }

  /* folder tree panel */
  .panel {
    width: 220px; flex: none; display: flex; flex-direction: column; padding: 12px 10px;
    background: #13161e; border-right: 1px solid var(--border);
  }
  .phead { font-size: 14px; font-weight: 680; color: #fff; padding: 2px 8px 12px; }
  .ptree { flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 1px; }

  main { flex: 1; min-width: 0; position: relative; display: flex; flex-direction: column; overflow: auto; }
</style>

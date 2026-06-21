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
  import ActivityMenu from '$lib/components/shared/ActivityMenu.svelte';
  import Lightbox from '$lib/components/shared/Lightbox.svelte';
  import ConfirmModal from '$lib/components/shared/ConfirmModal.svelte';
  import NewPersonaModal from '$lib/components/shared/NewPersonaModal.svelte';
  import TagGraphModal from '$lib/components/image/TagGraphModal.svelte';
  import ConfigModal from '$lib/components/ConfigModal.svelte';

  let { children } = $props();

  // ── all $state declarations first ──
  let showActivity = $state(false);
  let openDrop = $state(null); // { id, el } — which subnav dropdown is open

  // ── derived ──
  let path = $derived($page.url.pathname);
  let search = $derived($page.url.search || '');
  let section = $derived(path.split('/')[1] || '');
  let tree = $derived(new Set(['characters', 'stories', 'images', 'training', 'settings', 'lorebooks']).has(section) ? treeFor(section, path) : []);
  let activeHref = $derived(path + search);

  let comfyUp = $derived(app.health?.comfyui?.up);
  let runpodConfigured = $derived(app.health?.runpod?.configured ?? false);
  let runpodEnabled = $derived(app.health?.runpod?.enabled ?? true);
  let running = $derived(app.activity?.running || 0);

  // ── helpers ──
  function closeMenus() { showActivity = false; openDrop = null; }

  async function toggleRunpod() {
    await post('/runpod/enabled', { enabled: !runpodEnabled });
    await refreshHealth();
  }

  const nav = [
    { id: 'chat',       label: 'Chat',       icon: '💬', href: '/chat' },
    { id: 'characters', label: 'Characters', icon: '👥', href: '/characters/selected' },
    { id: 'stories',    label: 'Stories',    icon: '📖', href: '/stories' },
    { id: 'lorebooks',  label: 'Lorebooks',  icon: '📚', href: '/lorebooks' },
    { id: 'images',     label: 'Images',     icon: '🖼', href: '/images/graph' },
    { id: 'training',   label: 'Training',   icon: '🎓', href: '/training' },
    { id: 'settings',   label: 'Settings',   icon: '⚙',  href: '/settings/system' },
  ];

  const isActive = (id) => path === `/${id}` || path.startsWith(`/${id}/`);

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

  // Programmatic deep-link from child components (e.g. Train → Settings).
  $effect(() => { if (app.nav.screen) { const s = app.nav.screen; app.nav.screen = null; goto(`/${s}`); } });

  // ── subnav dropdown ──
  function toggleDrop(id, e) {
    e.stopPropagation();
    const el = e.currentTarget.closest('.snwrap');
    openDrop = openDrop?.id === id ? null : { id, el };
  }

  $effect(() => {
    const drop = openDrop;
    if (!drop) return;
    const handler = (e) => { if (!drop.el?.contains(e.target)) openDrop = null; };
    const t = setTimeout(() => document.addEventListener('mousedown', handler), 0);
    return () => { clearTimeout(t); document.removeEventListener('mousedown', handler); };
  });

  function nodeActive(n) {
    if (n.header || n.picker) return false;
    if (n.children) return n.children.some(nodeActive);
    if (!n.href) return false;
    if (n.match === 'exact') return activeHref === n.href;
    return activeHref === n.href || activeHref.startsWith(n.href + '/') || activeHref.startsWith(n.href + '?');
  }

  // ── persona gate ──
  let personaGateRan = false;
  async function maybePromptPersona() {
    if (personaGateRan) return;
    personaGateRan = true;
    const need = app.personas.length === 0 || !app.activePersona ||
                 !app.personas.some((p) => p.id === app.activePersona);
    if (!need) return;
    const res = await askNewPersona();
    if (!res) return;
    const r = await post('/personas', { name: res.name, description: res.description });
    if (r.data?.key) { setActivePersona(r.data.key); await refreshPersonas(); }
  }

  let timer, atimer, ptimer;
  onMount(async () => {
    await refreshAll();
    await refreshActivity();
    await migratePersonas();
    await refreshPersonas();
    void maybePromptPersona();
    timer = setInterval(refreshHealth, 6000);
    atimer = setInterval(refreshActivity, 3000);
    ptimer = startPruneTimer();
  });
  onDestroy(() => { clearInterval(timer); clearInterval(atimer); clearInterval(ptimer); });
</script>

<svelte:window onclick={closeMenus} />

<div class="shell">

  <!-- ── top bar: section switcher + status tools ── -->
  <header class="topbar">
    <div class="mark" aria-hidden="true"></div>

    <nav class="topnav">
      {#each nav as n (n.id)}
        <button class="navbtn" class:on={isActive(n.id)} onclick={() => go(n)}>
          <span class="nicon">{n.icon}</span>
          <span class="nlabel">{n.label}</span>
        </button>
      {/each}
    </nav>

    <div class="topright">
      <div class="actwrap">
        <button class="ico" class:busy={running > 0}
          onclick={(e) => { e.stopPropagation(); const v = !showActivity; closeMenus(); showActivity = v; }}
          title="Live workloads" aria-label="Activity">
          ⚡{#if running > 0}<span class="badge">{running}</span>{/if}
        </button>
        {#if showActivity}<ActivityMenu onnavigate={(s) => { if (s) goto(`/${s}`); showActivity = false; }} />{/if}
      </div>
      {#if runpodConfigured}
        <button class="rptoggle" class:rpon={runpodEnabled} onclick={toggleRunpod}
          title={runpodEnabled ? 'RunPod active — click to run locally' : 'Running locally — click to use RunPod'}>☁</button>
      {/if}
      <a href="/settings/system" class="cdot {comfyUp ? 'up' : 'down'}" title={comfyUp ? 'ComfyUI live — click for system info' : 'ComfyUI off — click for system info'}></a>
    </div>
  </header>

  <!-- ── subnav: section tree as a horizontal bar ── -->
  {#if tree.length}
    <nav class="subnav">
      {#each tree as node (node.id)}
        {#if node.children?.length}
          <div class="snwrap">
            <button class="snbtn drop" class:on={nodeActive(node)} class:picker={node.picker}
                    onclick={(e) => toggleDrop(node.id, e)}>
              {node.label} <span class="sarr" class:up={openDrop?.id === node.id}>▾</span>
            </button>
            {#if openDrop?.id === node.id}
              <div class="sdrop" role="menu">
                {#each node.children as child (child.id)}
                  {#if child.header}
                    <div class="sdhead">{child.label}</div>
                  {:else if child.children?.length}
                    <a href={child.href} class="sditem parent" class:on={nodeActive(child)}
                       role="menuitem" onclick={() => (openDrop = null)}>
                      {child.icon ? child.icon + ' ' : ''}{child.label}
                    </a>
                    {#each child.children as leaf (leaf.id)}
                      <a href={leaf.href} class="sditem leaf" class:on={nodeActive(leaf)}
                         role="menuitem" onclick={() => (openDrop = null)}>
                        {leaf.icon ? leaf.icon + ' ' : ''}{leaf.label}
                      </a>
                    {/each}
                  {:else}
                    <a href={child.href} class="sditem" class:on={nodeActive(child)}
                       role="menuitem" onclick={() => (openDrop = null)}>{child.label}</a>
                  {/if}
                {/each}
              </div>
            {/if}
          </div>
        {:else if node.action}
          <button class="snbtn" onclick={node.action}>{node.label}</button>
        {:else}
          <a href={node.href} class="snbtn" class:on={nodeActive(node)}
             class:dimmed={node.dimmed} class:done={node.done}>{node.label}</a>
        {/if}
      {/each}
    </nav>
  {/if}

  <main>{@render children()}</main>
</div>

<Lightbox />
<ConfirmModal />
<NewPersonaModal />
<TagGraphModal />
<ConfigModal />

<style>
  .shell { display: flex; flex-direction: column; height: 100vh; overflow: hidden; }

  /* ── top bar ── */
  .topbar {
    flex: none; height: 48px;
    display: flex; align-items: center; gap: 0; padding: 0 14px;
    background: #12151d; border-bottom: 1px solid var(--border);
  }
  .mark {
    width: 22px; height: 22px; border-radius: 6px; transform: rotate(45deg); flex: none; margin-right: 16px;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
  }

  .topnav { display: flex; align-items: center; gap: 2px; }
  .navbtn {
    display: flex; align-items: center; gap: 6px; padding: 6px 13px;
    border: 0; background: none; color: var(--muted); border-radius: 8px;
    cursor: pointer; font-size: 13px; font-weight: 600; line-height: 1;
    transition: color .12s, background .12s;
  }
  .navbtn:hover { color: var(--text); background: var(--elev); }
  .navbtn.on { color: #fff; background: var(--elev-2); }
  .nicon { font-size: 15px; line-height: 1; }

  .topright { margin-left: auto; display: flex; align-items: center; gap: 8px; }

  .actwrap { position: relative; }
  .actwrap :global(.menu) { top: calc(100% + 6px); right: 0; left: auto; bottom: auto; }

  .ico {
    position: relative; width: 32px; height: 32px; display: grid; place-items: center; padding: 0;
    font-size: 15px; border: 1px solid var(--border); border-radius: 8px;
    background: var(--elev); color: var(--muted); cursor: pointer;
  }
  .ico:hover { color: var(--text); background: var(--elev-2); }
  .ico.busy { color: var(--accent); border-color: rgba(109,140,255,.4); background: rgba(109,140,255,.1); }
  .ico .badge {
    position: absolute; top: -5px; right: -5px; min-width: 16px; height: 16px; padding: 0 4px;
    border-radius: 999px; background: var(--accent); color: #0b0e14; font-size: 11px; font-weight: 800;
    display: grid; place-items: center;
  }
  .rptoggle {
    width: 32px; height: 32px; display: grid; place-items: center; padding: 0; font-size: 15px;
    border: 1px solid var(--border); border-radius: 8px; background: var(--elev); color: var(--muted); cursor: pointer;
  }
  .rptoggle:hover { color: var(--text); background: var(--elev-2); }
  .rptoggle.rpon { color: #5ba3f5; border-color: rgba(91,163,245,.4); background: rgba(91,163,245,.12); }
  .cdot { width: 8px; height: 8px; border-radius: 50%; flex: none; cursor: pointer; }
  .cdot.up { background: var(--good); }
  .cdot.down { background: var(--bad); }

  /* ── subnav ── */
  .subnav {
    flex: none; height: 38px;
    display: flex; align-items: center; gap: 2px; padding: 0 14px;
    background: #13161e; border-bottom: 1px solid var(--border);
  }
  .snbtn {
    display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px;
    border: none; border-radius: 7px; background: none;
    font-size: 13px; font-weight: 600; color: var(--muted);
    cursor: pointer; text-decoration: none; line-height: 1;
    transition: color .12s, background .12s;
  }
  .snbtn:hover { color: var(--text); background: var(--elev); }
  .snbtn.on { color: #fff; background: var(--elev-2); }
  .snbtn.dimmed { opacity: 0.4; }
  .snbtn.dimmed:hover { opacity: 0.7; }
  .snbtn.done { color: var(--good); }
  .snbtn.picker { font-weight: 700; }
  .snwrap { position: relative; }
  .sarr { font-size: 10px; transition: transform .15s; display: inline-block; }
  .sarr.up { transform: rotate(180deg); }

  .sdrop {
    position: absolute; top: calc(100% + 4px); left: 0; z-index: 50;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.4);
    padding: 4px; min-width: 160px; max-height: 70vh; overflow-y: auto;
    display: flex; flex-direction: column; gap: 1px;
  }
  .sdhead {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
    color: var(--faint); padding: 8px 10px 3px; user-select: none;
  }
  .sditem {
    display: block; padding: 7px 12px; border-radius: 6px;
    font-size: 13px; font-weight: 500; color: var(--muted);
    text-decoration: none; white-space: nowrap;
    transition: color .1s, background .1s;
  }
  .sditem:hover { color: var(--text); background: var(--elev); }
  .sditem.on { color: #fff; background: var(--elev-2); }
  .sditem.parent { font-weight: 600; }
  .sditem.leaf { padding-left: 22px; font-size: 12.5px; }

  /* ── content ── */
  main { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; }
</style>

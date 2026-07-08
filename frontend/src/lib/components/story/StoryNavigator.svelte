<script>
  // The story EXPLORER — a VS Code-style file tree that replaces the horizontal in-story subnav.
  // It mirrors the authored hierarchy (Overview · World▸locations · Cast▸characters · Relationships ·
  // Arcs▸scenes · Play) over the routes that already exist. One tree, collapsible folders, indent
  // guides, active-path reveal. See [[navigation-pattern]] / [[story-tab-shell]] — this is the
  // in-story nav; the app's top bar is unchanged.
  import { page } from '$app/stores';
  import { stories } from '$lib/stories.svelte.js';
  import { charName } from '$lib/characters.svelte.js';
  import { arcSpine, ACCESS } from '$lib/storyspine.js';

  let { collapsed = false, onToggle } = $props();

  let st = $derived(stories.current);
  let key = $derived(st?.key || $page.params.key || '');
  let path = $derived($page.url.pathname);
  let search = $derived($page.url.search || '');

  const tab = (t) => `/stories/${key}/structure?tab=${t}`;

  // The tree model — derived from the live story. kind: file | folder; folders carry children.
  let tree = $derived.by(() => {
    if (!st) return [];
    const locs = st.locations || [];
    const cast = st.cast || [];
    const primary = cast.find((m) => m.primary)?.character || cast[0]?.character;
    // Arcs (and the scenes inside them) come from the ONE shared spine — real arcs or the concrete
    // example — so the tree, the Arcs page, and the Scenes pane never disagree.
    const arcNodes = arcSpine(st).arcs;
    // Per-character STAGE snapshots = the same arcs (each arc is a card snapshot for that character).
    const stageOf = (charKey) => arcNodes.map((a, i) => ({
      id: `cs-${charKey}-${a.id}`, icon: '·', label: a.title,
      href: `/stories/${key}/characters?char=${charKey}&stage=${i}` }));
    const cch = `/stories/${key}/characters`;
    return [
      { id: 'overview', label: 'Overview', icon: '◈', href: tab('overview') },
      { id: 'world', label: 'World', icon: '🌐', href: tab('map'),
        children: locs.map((l) => ({ id: `loc-${l.id}`, label: l.name || 'Location', icon: '▪', href: tab('map') })) },
      // Characters = the narrative CARDS (base + tell/shape/full ladder, per-stage snapshots).
      { id: 'characters', label: 'Characters', icon: '🪪', href: cch,
        children: cast.map((m) => ({ id: `char-${m.character}`, icon: m.character === primary ? '★' : '◦',
          label: charName(m.character) || m.character, href: `${cch}?char=${m.character}`,
          children: stageOf(m.character) })) },
      { id: 'relationships', label: 'Relationships', icon: '⁂', href: tab('relationships') },
      // Selecting an arc opens ITS scenes directly (/scenes?arc=). Scenes aren't nested in the tree —
      // an arc IS its scenes. (The "Arcs" header still opens the spine overview at /arcs.)
      { id: 'arcs', label: 'Arcs', icon: '❖', href: `/stories/${key}/arcs`,
        children: arcNodes.map((a) => ({ id: `arc-${a.id}`, icon: '●', tint: (ACCESS[a.access] || {}).color,
          label: a.title, title: `access: ${(ACCESS[a.access] || {}).label || ''}`,
          href: `/stories/${key}/scenes?arc=${a.id}` })) },
      // Outfits = the wardrobe / appearance catalogue (sprites, emotions) — distinct from the cards.
      { id: 'outfits', label: 'Outfits', icon: '👗', href: `/stories/${key}/cast`,
        children: cast.map((m) => ({ id: `fit-${m.character}`, icon: '▪',
          label: charName(m.character) || m.character, href: `/stories/${key}/cast` })) },
      // Prompts = every image prompt (art style · appearance · outfits · locations · scenes) in one place.
      { id: 'prompts', label: 'Prompts', icon: '🎨', href: `/stories/${key}/prompts` },
    ];
  });

  // ── active-path matching ──
  function hrefActive(href) {
    if (!href) return false;
    const A = path + search;
    if (href.includes('?')) return A === href;          // ?tab= links match exactly
    return path === href || path.startsWith(href + '/');
  }
  function branchActive(node) {
    if (node.href && hrefActive(node.href)) return true;
    return (node.children || []).some(branchActive);
  }

  // ── expand state (persisted) + auto-reveal the active branch ──
  const LS = `loom.storynav.${key}`;
  const load = () => { try { return new Set(JSON.parse(localStorage.getItem(LS) || '[]')); } catch { return new Set(); } };
  let open = $state(typeof localStorage !== 'undefined' ? load() : new Set());
  let seeded = '';
  $effect(() => { if (key && seeded !== key) { seeded = key; open = load(); } });
  function toggle(id) {
    const next = new Set(open);
    next.has(id) ? next.delete(id) : next.add(id);
    open = next;
    try { localStorage.setItem(LS, JSON.stringify([...next])); } catch { /* ignore */ }
  }
  // The active branch auto-reveals without persisting: a folder renders open if it's user-opened
  // OR it contains the active route (branchActive). No effect needed — avoids a read/write loop.
  const isOpen = (node) => open.has(node.id) || branchActive(node);
</script>

{#if collapsed}
  <button class="reopen" onclick={onToggle} title="Show explorer" aria-label="Show explorer">⟩</button>
{:else}
  <nav class="explorer" aria-label="Story explorer">
    <div class="head">
      <a class="back" href="/stories" title="All stories">‹ Stories</a>
      <button class="collapse" onclick={onToggle} title="Hide explorer" aria-label="Hide explorer">⟨</button>
    </div>
    <div class="titlebar" title={st?.name || ''}>
      <span class="tico">{st?.type === 'vn' ? '🎴' : '📖'}</span>
      <span class="tname">{st?.name || 'Untitled story'}</span>
    </div>

    <div class="tree">
      {#each tree as node (node.id)}
        {@render Row(node, 0)}
      {/each}
    </div>

    <a class="play" class:on={path.endsWith('/play')} href={`/stories/${key}/play`}>
      <span class="pico">▶</span> Play
    </a>
  </nav>
{/if}

{#snippet Row(node, depth)}
  {@const folder = !!node.children}
  {@const active = node.href ? hrefActive(node.href) : false}
  {@const expanded = folder && isOpen(node)}
  <div class="row" class:active class:folder style:--depth={depth}>
    {#if folder}
      <button class="twisty" class:open={expanded}
              onclick={() => toggle(node.id)} aria-label={expanded ? 'Collapse' : 'Expand'}>▸</button>
    {:else}
      <span class="twisty ghost"></span>
    {/if}
    <a class="rowlink" href={node.href} title={node.title || null}>
      <span class="ico" class:star={node.icon === '★'} style:color={node.tint || null}>{node.icon === '▸fold' ? '' : node.icon}</span>
      <span class="lbl">{node.label}</span>
    </a>
  </div>
  {#if expanded}
    {#each node.children as child (child.id)}
      {@render Row(child, depth + 1)}
    {/each}
    {#if !node.children.length}
      <div class="row empty" style:--depth={depth + 1}><span class="twisty ghost"></span><span class="lbl none">— empty —</span></div>
    {/if}
  {/if}
{/snippet}

<style>
  .explorer {
    position: fixed; left: 0; top: var(--chrome-top, 48px); bottom: 0; width: 248px; z-index: 45;
    display: flex; flex-direction: column;
    background: #10131a; border-right: 1px solid var(--border);
    font-size: 13px; user-select: none;
  }

  /* header: back + collapse */
  .head { flex: none; display: flex; align-items: center; height: 34px; padding: 0 6px 0 10px; }
  .back { flex: 1; font-size: 11.5px; font-weight: 600; letter-spacing: .2px; color: var(--muted);
          text-decoration: none; text-transform: uppercase; }
  .back:hover { color: var(--text); }
  .collapse, .reopen {
    width: 24px; height: 24px; flex: none; display: grid; place-items: center; padding: 0;
    background: none; border: 1px solid transparent; border-radius: 6px; color: var(--faint);
    font-size: 13px; cursor: pointer;
  }
  .collapse:hover { color: var(--text); background: var(--elev); }
  .reopen {
    position: fixed; left: 8px; top: calc(var(--chrome-top, 48px) + 8px); z-index: 46;
    width: 26px; height: 26px; background: var(--elev); border-color: var(--border);
    color: var(--muted); box-shadow: 0 2px 8px rgba(0,0,0,.3);
  }
  .reopen:hover { color: var(--text); border-color: var(--accent); }

  /* story title */
  .titlebar {
    flex: none; display: flex; align-items: center; gap: 7px; padding: 4px 12px 10px;
    border-bottom: 1px solid var(--border); margin-bottom: 4px;
  }
  .tico { font-size: 14px; flex: none; }
  .tname { font-size: 13px; font-weight: 700; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* the tree */
  .tree { flex: 1; overflow-y: auto; overflow-x: hidden; padding: 2px 4px 10px; }
  .tree::-webkit-scrollbar { width: 8px; }
  .tree::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  .row {
    position: relative; display: flex; align-items: center; gap: 3px;
    height: 26px; border-radius: 6px; color: var(--muted);
    padding-left: calc(6px + var(--depth, 0) * 15px);
    transition: background .1s, color .1s;
  }
  .row:hover { background: var(--elev); color: var(--text); }
  .row.active { background: var(--elev-2, #222838); color: #fff; }
  .row.active::before {
    content: ''; position: absolute; left: 0; top: 3px; bottom: 3px; width: 2px;
    border-radius: 0 2px 2px 0; background: var(--accent);
  }
  .rowlink {
    flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; height: 100%;
    text-decoration: none; color: inherit; padding-right: 8px;
  }

  .twisty {
    width: 16px; height: 16px; flex: none; display: grid; place-items: center; padding: 0;
    background: none; border: none; color: var(--faint); font-size: 9px; cursor: pointer;
    transition: transform .12s;
  }
  .twisty.open { transform: rotate(90deg); }
  .twisty.ghost { cursor: default; }
  .twisty:not(.ghost):hover { color: var(--text); }

  .ico { width: 16px; flex: none; text-align: center; font-size: 12px; line-height: 1; opacity: .95; }
  .ico.star { color: #ffd45e; }
  .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  .lbl.none { color: var(--faint); font-style: italic; font-size: 11.5px; }
  .row.folder .lbl { font-weight: 600; color: var(--text); }
  .row.folder:not(.active) .lbl { color: var(--muted); }
  .row.empty { pointer-events: none; }

  /* Play — the runtime, pinned at the bottom, set apart */
  .play {
    flex: none; display: flex; align-items: center; gap: 8px; margin: 4px 8px 10px;
    padding: 9px 12px; border-radius: 8px; text-decoration: none;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
    color: var(--accent); font-weight: 700; font-size: 13px;
    transition: background .12s;
  }
  .play:hover { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .play.on { background: var(--accent); color: #0b0e14; border-color: var(--accent); }
  .pico { font-size: 10px; }
</style>

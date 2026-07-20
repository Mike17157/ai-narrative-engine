<script>
  // The story workspace is deliberately three-card shaped: a story card, character cards, and
  // outfit cards. The old world/arc/map tree exposed implementation layers instead of the things
  // a writer actually owns, so those editors now live inside the Story card.
  import { page } from '$app/stores';
  import { stories } from '$lib/stories.svelte.js';

  let { collapsed = false, onToggle } = $props();

  let st = $derived(stories.current);
  let key = $derived(st?.key || $page.params.key || '');
  let path = $derived($page.url.pathname);
  let search = $derived($page.url.search || '');
  const leanStoryMode = import.meta.env.VITE_LEAN_STORY === '1';

  // The three durable authoring frames. Each frame has one clear owner and no nested navigation.
  let tree = $derived.by(() => {
    if (!st) return [];
    return [
      { id: 'story', label: 'Story card', icon: '◈', href: `/stories/${key}` },
      { id: 'characters', label: 'Character cards', icon: '🪪', href: `/stories/${key}/characters` },
      ...(!leanStoryMode ? [{ id: 'outfits', label: 'Outfit cards', icon: '👗', href: `/stories/${key}/cast` }] : []),
      ...(!leanStoryMode ? [{ id: 'studio', label: 'Portrait studio', icon: '🎨', href: `/stories/${key}/studio` }] : []),
      { id: 'images', label: 'Story images', icon: '✦', href: `/stories/${key}/images` },
      ...(st.fields?.status === 'active'
        ? [{ id: 'play', label: 'Play', icon: '▶', href: `/stories/${key}/play` }]
        : []),
    ];
  });

  // ── active-path matching ──
  function hrefActive(href) {
    if (!href) return false;
    const A = path + search;
    if (href.includes('?')) return A === href;          // ?tab= links match exactly
    if (href === `/stories/${key}`) return path === href;
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

    <div class="tree frames">
      {#each tree as node (node.id)}
        {@render Row(node, 0)}
      {/each}
    </div>

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
  .frames { padding: 10px 8px; display: flex; flex-direction: column; gap: 7px; }
  .tree::-webkit-scrollbar { width: 8px; }
  .tree::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }

  .row {
    position: relative; display: flex; align-items: center; gap: 3px;
    height: 26px; border-radius: 6px; color: var(--muted);
    padding-left: calc(6px + var(--depth, 0) * 15px);
    transition: background .1s, color .1s;
  }
  .frames .row { height: 44px; padding-left: 8px; border: 1px solid var(--border); background: var(--elev); }
  .frames .row:hover { border-color: var(--accent); }
  .frames .row.active { border-color: var(--accent); }
  .frames .ico { font-size: 16px; width: 24px; }
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

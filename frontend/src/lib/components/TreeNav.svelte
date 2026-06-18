<script>
  // Collapsible folder tree — one nav model for every section.
  // Click a folder to expand/collapse its children inline. Capped at 2 levels:
  // a folder may contain leaves + non-clickable section headers (header: true),
  // never nested folders.
  //
  // Node shape: { id, label, href?, icon?, disabled?, children?, header?, match? }
  //   header: true  → a non-interactive group label rendered inside a folder
  //   match: 'prefix' (default) | 'exact'

  let { nodes = [], activeHref = '' } = $props();

  function isActive(n) {
    if (!n.href) return false;
    if (n.match === 'exact') return activeHref === n.href;
    return activeHref === n.href || activeHref.startsWith(n.href + '/');
  }
  // A folder is "hot" if any descendant is active → auto-expand it and mark it.
  function containsActive(n) {
    if (isActive(n)) return true;
    return (n.children || []).some(containsActive);
  }

  // Accordion: at most ONE folder open per panel. Clicking the open folder closes
  // it; clicking a different folder closes the current and opens the new one.
  // On load/navigation, the folder holding the active leaf auto-opens — but a
  // manual close sticks (the auto-open won't re-open a folder the user just shut),
  // so clicking the selected parent to collapse it actually stays collapsed.
  let openId = $state(null);        // the currently-open folder id, or null
  let userClosed = $state(null);    // an id the user just collapsed; suppresses auto-open
  $effect(() => {
    activeHref; // track navigation
    const holder = nodes.find((n) => n.children?.length && containsActive(n));
    if (holder && holder.id !== userClosed) openId = holder.id;
  });
  function isOpen(n) { return openId === n.id; }
  function toggle(n) {
    if (!n.children?.length) return;
    if (openId === n.id) { openId = null; userClosed = n.id; }   // close current
    else { openId = n.id; userClosed = null; }                   // close other, open this
  }
</script>

{#each nodes as n (n.id)}
  {#if n.header}
    <div class="tnav-header">{n.label}</div>
  {:else if n.children?.length}
    <button class="tnav-item folder" class:on={containsActive(n)} class:open={isOpen(n)}
      onclick={() => toggle(n)} aria-expanded={isOpen(n)}>
      <span class="chev">{isOpen(n) ? '▾' : '▸'}</span>
      {#if n.icon}<span class="ico">{n.icon}</span>{/if}
      <span class="lbl">{n.label}</span>
    </button>
    {#if isOpen(n)}
      <div class="kids">
        {#each n.children as c (c.id)}
          {#if c.header}
            <div class="tnav-header sub">{c.label}</div>
          {:else}
            <a class="tnav-item leaf" class:on={isActive(c)} class:dim={c.disabled}
              href={c.disabled ? undefined : c.href} aria-disabled={c.disabled || undefined} title={c.label}>
              <span class="chev-spacer"></span>
              {#if c.icon}<span class="ico">{c.icon}</span>{/if}
              <span class="lbl">{c.label}</span>
            </a>
          {/if}
        {/each}
      </div>
    {/if}
  {:else}
    <a class="tnav-item leaf" class:on={isActive(n)} class:dim={n.disabled}
      href={n.disabled ? undefined : n.href} aria-disabled={n.disabled || undefined} title={n.label}>
      <span class="chev-spacer"></span>
      {#if n.icon}<span class="ico">{n.icon}</span>{/if}
      <span class="lbl">{n.label}</span>
    </a>
  {/if}
{/each}

<style>
  .tnav-item {
    /* counteract the global `button` rule — this is a nav row, not an action */
    display: flex; align-items: center; gap: 7px; width: 100%;
    background: none; border: 0; padding: 6px 10px; border-radius: 8px;
    font: inherit; font-size: 13px; font-weight: 540; color: var(--muted);
    text-decoration: none; cursor: pointer; text-align: left;
  }
  .tnav-item:hover { background: var(--elev); color: var(--text); }
  .tnav-item.on { background: var(--elev-2); color: #fff; font-weight: 620; }
  .tnav-item.dim { opacity: .4; cursor: default; }
  .tnav-item.dim:hover { background: none; color: var(--muted); }

  .chev { width: 12px; flex: none; font-size: 10px; color: var(--faint); text-align: center; }
  .chev-spacer { width: 12px; flex: none; }
  .ico { flex: none; font-size: 13px; line-height: 1; opacity: .85; }
  .lbl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* Non-interactive group headers inside a folder (the former 3rd level) */
  .tnav-header {
    font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .5px;
    color: var(--faint); padding: 10px 10px 3px; user-select: none;
  }
  .tnav-header.sub { padding-top: 8px; }

  /* single indentation guide — children render once (max depth 2) */
  .kids { display: flex; flex-direction: column; gap: 1px; margin-left: 14px;
          padding-left: 8px; border-left: 1px solid var(--border-soft); }
</style>

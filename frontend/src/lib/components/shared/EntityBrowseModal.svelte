<script>
  // The reusable cross-reference picker. Driven by the global `browse` store; mounted once
  // in the root layout. Single-select picks-and-closes; multi-select collects then Confirms.
  import { onMount } from 'svelte';
  import Modal from '$lib/components/shared/Modal.svelte';
  import EntityList from '$lib/components/shared/EntityList.svelte';
  import { get } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { browse, closeBrowse } from '$lib/browse.svelte.js';

  // Cache the raw entities per kind so reopening is instant; refresh on open.
  let raw = $state({ lorebook: [], preset: [] });
  let local = $state([]);   // working selection (multi)

  async function loadKind(kind) {
    try {
      if (kind === 'preset') raw.preset = (await get('/presets')).presets || [];
      else raw.lorebook = (await get('/lorebooks')).books || [];
    } catch { /* leave as-is */ }
  }
  onMount(() => { loadKind('lorebook'); loadKind('preset'); });

  // (Re)load + seed the working selection each time the modal opens.
  let wasOpen = false;
  $effect(() => {
    if (browse.open && !wasOpen) { loadKind(browse.kind); local = [...browse.value]; }
    wasOpen = browse.open;
  });

  let items = $derived.by(() => {
    let list = (raw[browse.kind] || []).filter((e) => !browse.filter || browse.filter(e));
    // Global content gate: hide nsfw-rated books unless a currently-bound one (keep it visible
    // so it can be removed), matching the retrieval gate.
    if (browse.kind === 'lorebook' && !app.allowNsfw)
      list = list.filter((b) => b.rating !== 'nsfw' || browse.value.includes(b.id));
    if (browse.kind === 'preset') {
      return list.map((p) => ({ id: p.id, name: p.name || p.id,
        badge: p.mode || 'auto', badgeKind: 'accent', meta: p.model || '' }));
    }
    return list.map((b) => ({ id: b.id, name: b.name || b.id,
      badge: b.rating, badgeKind: b.rating, meta: `${b.entries ?? ''}${b.preset ? ' · 🎛' : ''}` }));
  });

  function toggle(id) {
    if (!browse.multi) { browse.onConfirm?.([id]); closeBrowse(); return; }
    local = local.includes(id) ? local.filter((x) => x !== id) : [...local, id];
  }
  function confirm() { browse.onConfirm?.([...local]); closeBrowse(); }

  let manageHref = $derived(browse.kind === 'preset' ? '/library/presets' : '/library/lorebooks');
</script>

<Modal open={browse.open} onClose={closeBrowse} flush width="520px" height="min(80vh, 560px)" zIndex={260}>
  <div class="head">
    <h3>{browse.title}</h3>
    <a class="manage" href={manageHref} onclick={closeBrowse}>Manage →</a>
    <button class="x" onclick={closeBrowse} title="Close">✕</button>
  </div>
  <div class="body">
    <EntityList {items} selected={browse.multi ? local : browse.value} multi={browse.multi}
      onToggle={toggle}
      placeholder={browse.kind === 'preset' ? 'Search presets…' : 'Search lorebooks…'}
      empty={browse.kind === 'preset' ? 'No presets.' : 'No lorebooks.'} />
  </div>
  {#if browse.multi}
    <div class="foot">
      <span class="cnt">{local.length} selected</span>
      <button class="ghost" onclick={closeBrowse}>Cancel</button>
      <button class="primary" onclick={confirm}>Confirm</button>
    </div>
  {/if}
</Modal>

<style>
  .head { display: flex; align-items: center; gap: 10px; padding: 13px 16px; border-bottom: 1px solid var(--border-soft); }
  .head h3 { margin: 0; font-size: 14px; font-weight: 660; flex: 1; }
  .manage { font-size: 12px; color: var(--accent); text-decoration: none; }
  .manage:hover { text-decoration: underline; }
  .x { background: none; border: 0; box-shadow: none; color: var(--muted); font-size: 14px; cursor: pointer; padding: 2px 6px; }
  .x:hover { color: var(--text); filter: none; }
  .body { padding: 12px 16px; display: flex; flex-direction: column; min-height: 0; flex: 1; }
  .foot { display: flex; align-items: center; gap: 10px; padding: 11px 16px; border-top: 1px solid var(--border-soft); }
  .cnt { font-size: 12px; color: var(--muted); flex: 1; }
  .ghost { font-size: 12.5px; padding: 7px 14px; border-radius: 8px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; box-shadow: none; }
  .ghost:hover { color: var(--text); filter: none; }
  .primary { font-size: 12.5px; padding: 7px 16px; border-radius: 8px; background: var(--accent); border: 0; color: #fff; cursor: pointer; font-weight: 600; }
  .primary:hover { filter: brightness(1.08); }
</style>

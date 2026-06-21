// Global opener for the reusable generation-config modal. Any chat/generation
// surface calls openConfigModal() to bring up the same modal (Configs · Models ·
// Connections · Lorebooks), so config lives at the point of use rather than on a
// settings page. The modal is mounted once in the root layout.
//
//   openConfigModal({ tab, lorebooks, onLorebooks })
//     tab          — which tab to open on ('configs' | 'models' | 'connections' | 'lorebooks')
//     lorebooks    — the surface's currently-attached lorebook ids (Lorebooks tab seed)
//     onLorebooks  — callback(ids) when the surface's attachment changes

export const configModal = $state({
  open: false,
  tab: 'configs',
  lorebooks: [],
  onLorebooks: null,
});

export function openConfigModal(opts = {}) {
  // Models & connections moved into presets (Library) — coerce old openers to a live tab.
  const tab = opts.tab || 'configs';
  configModal.tab = (tab === 'configs' || tab === 'lorebooks') ? tab : 'configs';
  configModal.lorebooks = opts.lorebooks ? [...opts.lorebooks] : [];
  configModal.onLorebooks = opts.onLorebooks || null;
  configModal.open = true;
}

export function closeConfigModal() {
  configModal.open = false;
  configModal.onLorebooks = null;
}

// Per-section folder-tree definitions for the subnav. Each function returns a
// plain node list ([{ id, label, href, icon?, disabled?, children?, match? }])
// built from current store state, so counts stay live. ONE nav model for every
// section; no in-page menu fragments anywhere. The root layout renders these as
// a horizontal subnav bar (+ dropdowns for nodes with children).

import { stories } from './stories.svelte.js';

// Settings subnav — System (environment, ComfyUI, trainer) + Personas (the
// portable "you" cards). Models, connections AND the generation pipeline configs
// all live on the per-chat ⚙ config modal (every chat surface has the gear), not
// in navigation.
export function settingsTree() {
  return [
    { id: 'system', label: 'System', href: '/settings/system' },
    { id: 'personas', label: 'Personas', href: '/settings/personas' },
  ];
}

// Stories subnav — 2 contextual modes:
//   Library (the list, genesis draft): NO subnav — the top-level "Stories" tab IS the library, so a lone
//     "Library" sub-tab would be redundant. Returns [] → the layout renders no bar.
//   Inside a story: [← Stories back-button] · [Story ▾ picker] · Structure · Cast · ▶ Play
export function storiesTree(path = '') {
  const list = stories.list || [];

  // Inside a specific story
  const m = path.match(/^\/stories\/([^/?]+)/);
  const key = m?.[1];
  const active = key && key !== 'new' ? list.find((s) => s.key === key) : null;

  // Inside a story the horizontal subnav is replaced by the left EXPLORER tree
  // (StoryNavigator, mounted in stories/[key]/+layout.svelte). No top subnav bar here.
  if (active) return [];

  // Library (or the genesis draft) — no subnav.
  return [];
}

export function treeFor(section, path = '') {
  switch (section) {
    case 'settings':   return settingsTree();
    case 'stories':    return storiesTree(path);
    default: return [];
  }
}

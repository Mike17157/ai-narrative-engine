// Per-section folder-tree definitions for the subnav. Each function returns a
// plain node list ([{ id, label, href, icon?, disabled?, children?, match? }])
// built from current store state, so counts stay live. ONE nav model for every
// section; no in-page menu fragments anywhere. The root layout renders these as
// a horizontal subnav bar (+ dropdowns for nodes with children).

import { chars } from './characters.svelte.js';
import { stories } from './stories.svelte.js';
import { app } from './app.svelte.js';

export function charactersTree() {
  const n = chars.list?.length || 0;
  const sel = chars.list?.find((c) => c.key === app.activeChar);
  return [
    { id: 'selected', label: sel ? `Selected · ${sel.name || sel.key}` : 'Selected', href: '/characters/selected' },
    { id: 'search', label: `Browse${n ? ` (${n})` : ''}`, href: '/characters/search' },
    { id: 'import', label: 'Import card', href: '/characters/import' },
    { id: 'personas', label: 'Personas', href: '/characters/personas' },
  ];
}

// Images subnav — the bespoke header that used to live in images/+layout.svelte
// is gone; everything here renders in the unified subnav bar.
export function imagesTree() {
  return [
    { id: 'workflows',   label: 'Workflows',  href: '/images/workflows' },
    { id: 'graph',       label: 'Graph',      href: '/images/graph' },
    { id: 'lora',        label: 'Preset Lab', href: '/images/lora/library' },
    { id: 'models',      label: 'Models',     href: '/images/models' },
    { id: 'poses',       label: 'Poses',      href: '/images/poses' },
  ];
}

// Training subnav — the LoRA-making pipeline (formerly a collapsed <details>
// inside the LoRA library page), now a first-class section.
export function trainingTree() {
  return [
    { id: 'pipeline',  label: 'Pipeline',  href: '/training' },
    { id: 'datasets',  label: 'Datasets',  href: '/training/datasets' },
    { id: 'trainer',   label: 'Trainer',   href: '/training/trainer' },
  ];
}

// Settings subnav — just System now (environment, ComfyUI, trainer). Models,
// connections AND the generation pipeline configs all live on the per-chat ⚙ config
// modal (every chat surface has the gear), not in navigation. Personas → Characters.
export function settingsTree() {
  return [
    { id: 'system', label: 'System', href: '/settings/system' },
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

  if (active) {
    return [
      { id: 'all', label: '← Stories', href: '/stories', match: 'exact' },
      { id: 'story-picker', label: active.name || active.key, picker: true,
        children: list.map((s) => ({ id: `sp-${s.key}`, label: s.name || s.key, href: `/stories/${s.key}/structure` })) },
      { id: `${key}-structure`,   label: 'Structure',   href: `/stories/${key}/structure` },
      { id: `${key}-cast`,        label: 'Cast',        href: `/stories/${key}/cast` },
      { id: `${key}-play`,        label: '▶ Play',      href: `/stories/${key}/play` },
    ];
  }

  // Library (or the genesis draft) — no subnav.
  return [];
}

// Library subnav — the two first-class entities you author: Presets (the model side:
// connection + model + mode + params) and Lorebooks (rules/data + function books). Models
// live INSIDE presets and connections are part of a preset, so neither is a tab here.
export function libraryTree() {
  return [
    { id: 'lib-presets',   label: 'Agents',    href: '/library/presets' },
    { id: 'lib-lorebooks', label: 'Lorebooks', href: '/library/lorebooks' },
    { id: 'lib-tools',     label: 'Tools',     href: '/library/tools' },
    { id: 'lib-image-presets', label: 'Image Presets', href: '/library/image-presets' },
  ];
}

export function treeFor(section, path = '') {
  switch (section) {
    case 'characters': return charactersTree();
    case 'images':     return imagesTree();
    case 'training':   return trainingTree();
    case 'settings':   return settingsTree();
    case 'stories':    return storiesTree(path);
    case 'library':    return libraryTree(path);
    default: return [];
  }
}

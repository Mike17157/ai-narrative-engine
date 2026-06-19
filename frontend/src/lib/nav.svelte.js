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
    { id: 'graph',       label: 'Graph',      href: '/images/graph' },
    { id: 'lora',        label: 'LoRA',       href: '/images/lora/library' },
    { id: 'models',      label: 'Models',     href: '/images/models' },
    { id: 'poses',       label: 'Poses',      href: '/images/poses' },
    { id: 'connection',  label: 'Connection', href: '/settings/connections' },
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

// Settings subnav — organized by concern, not by kind. Connections owns
// credentials (chat + image); Models owns which model/workflow is active off an
// active connection; Generation owns story-builder + image-role config.
// Personas live under Characters.
export function settingsTree() {
  return [
    { id: 'system',      label: 'System',      href: '/settings/system' },
    { id: 'connections', label: 'Connections', href: '/settings/connections' },
    { id: 'models',      label: 'Models',      href: '/settings/models' },
    { id: 'generation',  label: 'Generation',  href: '/settings/story-gen' },
  ];
}

// Stories subnav — 3 contextual modes:
//   Mode A (library): Library · story list · Pipeline
//   Mode B (wizard):  ← Library · Setup · Storyboard · Scenes · Cast  (step indicators)
//   Mode C (story):   [Story Name ▾ picker] · Story map · Cast · ▶ Play
//                     (backgrounds + editing now live in the story-map graph)
export function storiesTree(path = '') {
  const list = stories.list || [];

  // Mode B — wizard
  if (path.startsWith('/stories/new')) {
    const step = stories.wizard?.step ?? 0;
    const STEPS = [
      { id: 'wz-setup',      label: 'Setup',      href: '/stories/new/setup' },
      { id: 'wz-storyboard', label: 'Storyboard', href: '/stories/new/storyboard' },
      { id: 'wz-scenes',     label: 'Scenes',     href: '/stories/new/scenes' },
      { id: 'wz-cast',       label: 'Cast',       href: '/stories/new/characters' },
    ];
    return [
      { id: 'wz-back', label: '← Library', href: '/stories', match: 'exact' },
      ...STEPS.map((s, i) => ({
        ...s,
        done: i < step,
        dimmed: i > step,
        label: i < step ? s.label + ' ✓' : s.label
      }))
    ];
  }

  // Mode C — inside a specific story
  const m = path.match(/^\/stories\/([^/?]+)/);
  const key = m?.[1];
  const active = key && key !== 'new' ? list.find((s) => s.key === key) : null;

  if (active) {
    return [
      { id: 'story-picker', label: active.name || active.key, picker: true,
        children: list.map((s) => ({ id: `sp-${s.key}`, label: s.name || s.key, href: `/stories/${s.key}/overview` })) },
      { id: `${key}-overview`,    label: 'Story map',   href: `/stories/${key}/overview` },
      { id: `${key}-cast`,        label: 'Cast',        href: `/stories/${key}/cast` },
      { id: `${key}-play`,        label: '▶ Play',      href: `/stories/${key}/play` },
    ];
  }

  // Mode A — library (no story links — the library page is now the management view)
  return [
    { id: 'library', label: 'Library', href: '/stories', match: 'exact' },
    { id: 'pipeline', label: 'Generation', href: '/settings/story-gen' },
  ];
}

export function treeFor(section, path = '') {
  switch (section) {
    case 'characters': return charactersTree();
    case 'images':     return imagesTree();
    case 'training':   return trainingTree();
    case 'settings':   return settingsTree();
    case 'stories':    return storiesTree(path);
    default: return [];
  }
}

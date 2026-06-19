// Per-section folder-tree definitions for TreeNav. Each function returns a plain
// node tree ([{ id, label, href, icon?, disabled?, children?, match? }]) built
// from current store state, so counts stay live. No callbacks — TreeNav renders
// leaves as <a href>, navigation is native. ONE nav model for every section;
// no in-page menu fragments anywhere.

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
    { id: 'models', label: 'Models', href: '/settings/models' },
    { id: 'chat-connection', label: 'Connection', href: '/settings/connections/chat' },
  ];
}

export function imagesTree() {
  return [];
}

// Stories subnav — 3 contextual modes:
//   Mode A (library): Library · story list · Pipeline
//   Mode B (wizard):  ← Library · Setup · Storyboard · Scenes · Cast  (step indicators)
//   Mode C (story):   [Story Name ▾ picker] · Overview · Backgrounds · Cast · Edit · ▶ Play
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
      { id: `${key}-overview`,    label: 'Overview',    href: `/stories/${key}/overview` },
      { id: `${key}-backgrounds`, label: 'Backgrounds', href: `/stories/${key}/backgrounds` },
      { id: `${key}-cast`,        label: 'Cast',        href: `/stories/${key}/cast` },
      { id: `${key}-edit`,        label: 'Edit',        href: `/stories/${key}/edit` },
      { id: `${key}-play`,        label: '▶ Play',      href: `/stories/${key}/play` },
    ];
  }

  // Mode A — library
  return [
    { id: 'library', label: 'Library', href: '/stories', match: 'exact' },
    ...list.map((s) => ({ id: `story-${s.key}`, label: s.name || s.key, href: `/stories/${s.key}/overview` })),
    { id: 'pipeline', label: 'Pipeline', href: '/settings/story-gen' }
  ];
}

export function treeFor(section, path = '') {
  switch (section) {
    case 'characters': return charactersTree();
    case 'images': return imagesTree();
    case 'stories': return storiesTree(path);
    default: return [];
  }
}

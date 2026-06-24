// Per-section folder-tree definitions for the subnav. Each function returns a
// plain node list ([{ id, label, href, icon?, disabled?, children?, match? }])
// built from current store state, so counts stay live. ONE nav model for every
// section; no in-page menu fragments anywhere. The root layout renders these as
// a horizontal subnav bar (+ dropdowns for nodes with children).

import { chars } from './characters.svelte.js';
import { stories, storySteps } from './stories.svelte.js';
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
    { id: 'lora',        label: 'Image Presets', href: '/images/lora/library' },
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

// Stories subnav — 3 contextual modes:
//   Mode A (library): Library · story list · Pipeline
//   Mode B (wizard):  ← Library · Setup · Storyboard · Scenes · Cast  (step indicators)
//   Mode C (story):   [Story Name ▾ picker] · Story map · Cast · ▶ Play
//                     (backgrounds + editing now live in the story-map graph)
export function storiesTree(path = '') {
  const list = stories.list || [];

  // Mode B — lean wizard: Premise · Characters · Outfits · Scenes (no spine/storyboard).
  // Step ✓ marks are driven by ARTIFACT PRESENCE (storySteps), not the raw step index.
  if (path.startsWith('/stories/new')) {
    const st = Object.fromEntries(storySteps(stories.wizard).map((s) => [s.route, s.status]));
    const STEPS = [
      { id: 'wz-premise',    label: 'Premise',    href: '/stories/new/premise',    route: null },
      { id: 'wz-characters', label: 'Characters', href: '/stories/new/characters', route: 'characters' },
      { id: 'wz-outfits',    label: 'Outfits',    href: '/stories/new/outfits',    route: 'outfits' },
      { id: 'wz-scenes',     label: 'Scenes',     href: '/stories/new/scenes',     route: 'scenes' },
    ];
    return [
      { id: 'wz-back', label: '← Library', href: '/stories', match: 'exact' },
      ...STEPS.map((s) => {
        const done = s.route ? st[s.route] === 'done' : false;
        return { id: s.id, label: done ? s.label + ' ✓' : s.label, href: s.href, done };
      }),
    ];
  }

  // Mode C — inside a specific story
  const m = path.match(/^\/stories\/([^/?]+)/);
  const key = m?.[1];
  const active = key && key !== 'new' ? list.find((s) => s.key === key) : null;

  if (active) {
    return [
      { id: 'story-picker', label: active.name || active.key, picker: true,
        children: list.map((s) => ({ id: `sp-${s.key}`, label: s.name || s.key, href: `/stories/${s.key}/workshop` })) },
      { id: `${key}-workshop`,    label: '⚒ Iterate',  href: `/stories/${key}/workshop` },
      { id: `${key}-cast`,        label: 'Cast',        href: `/stories/${key}/cast` },
      { id: `${key}-play`,        label: '▶ Play',      href: `/stories/${key}/play` },
    ];
  }

  // Mode A — library: Finished gallery + a separate In progress tab for wizard drafts.
  const draftCount = list.filter((s) => s.draft).length;
  return [
    { id: 'library', label: 'Library', href: '/stories', match: 'exact' },
    { id: 'drafts', label: `In progress${draftCount ? ` (${draftCount})` : ''}`, href: '/stories/drafts' },
    { id: 'charlab', label: 'Character Lab', href: '/stories/characters' },
  ];
}

// Library subnav — the two first-class entities you author: Presets (the model side:
// connection + model + mode + params) and Lorebooks (rules/data + function books). Models
// live INSIDE presets and connections are part of a preset, so neither is a tab here.
export function libraryTree() {
  return [
    { id: 'lib-presets',   label: 'Presets',   href: '/library/presets' },
    { id: 'lib-lorebooks', label: 'Lorebooks', href: '/library/lorebooks' },
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

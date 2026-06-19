// Per-section folder-tree definitions for TreeNav. Each function returns a plain
// node tree ([{ id, label, href, icon?, disabled?, children?, match? }]) built
// from current store state, so counts stay live. No callbacks — TreeNav renders
// leaves as <a href>, navigation is native. ONE nav model for every section;
// no in-page menu fragments anywhere.

import { chars, charName } from './characters.svelte.js';
import { stories } from './stories.svelte.js';
import { app } from './app.svelte.js';

// Generation: single pane — connection, chat model, prompt gen, image workflow.
const MODELS = [
  { id: 'models', label: 'Generation', href: '/settings/models', match: 'prefix' }
];

// Story pipeline — global config for all story-builder AI stages and image workflow
// assignments (story_builder.json + image_roles.json), not per-story settings.
const STORY_GEN = [
  { id: 'story-gen', label: 'Story pipeline', children: [
    { id: 'sg-gen-h', label: 'Text stages', header: true },
    { id: 'sg-storyboard', label: 'Storyboard', href: '/settings/story-gen?section=storyboard' },
    { id: 'sg-characters', label: 'Characters', href: '/settings/story-gen?section=characters' },
    { id: 'sg-locations', label: 'Locations', href: '/settings/story-gen?section=locations' },
    { id: 'sg-wardrobe', label: 'Wardrobe', href: '/settings/story-gen?section=wardrobe' },
    { id: 'sg-base_image', label: 'Appearance', href: '/settings/story-gen?section=base_image' },
    { id: 'sg-img-h', label: 'Image workflows', header: true },
    { id: 'sg-base', label: 'Portrait', href: '/settings/story-gen?section=base' },
    { id: 'sg-style', label: 'Style', href: '/settings/story-gen?section=style' },
    { id: 'sg-sprite', label: 'Sprites', href: '/settings/story-gen?section=sprite' },
    { id: 'sg-scene', label: 'Scene', href: '/settings/story-gen?section=scene' },
  ]}
];

export function settingsTree() {
  return [
    ...MODELS,
    ...STORY_GEN,
    { id: 'system', label: 'System', href: '/settings/system' }
  ];
}

export function charactersTree() {
  const n = chars.list?.length || 0;
  const sel = chars.list?.find((c) => c.key === app.activeChar);
  return [
    { id: 'selected', label: sel ? `Selected · ${sel.name || sel.key}` : 'Selected', href: '/characters/selected' },
    { id: 'search', label: `Browse${n ? ` (${n})` : ''}`, href: '/characters/search' },
    { id: 'import', label: 'Import card', href: '/characters/import' },
    { id: 'personas', label: 'Personas', href: '/characters/personas' }
  ];
}

export function imagesTree() {
  return [];
}

// Stories is a content browser: Library + a named folder per story, whose sections
// nest underneath. Cast & wardrobe is itself a folder of one leaf per cast member
// (deep-links to ?c=<key>), so the tree exercises its full recursion. The wizard is
// NOT in the nav — it's launched from the Library. Config moved to Settings.
export function storiesTree() {
  const storyFolders = (stories.list || []).map((s) => {
    // Cast members come straight off the library payload (no per-key load needed), so
    // the tier is populated even before a story is opened. The primary is starred.
    const castLeaves = (s.cast || []).map((m) => ({
      id: `${s.key}-cast-${m.character}`,
      label: charName(m.character) + (m.primary ? ' ★' : ''),
      href: `/stories/${s.key}/cast?c=${m.character}`
    }));
    return {
      id: `story-${s.key}`, label: s.name || s.key, href: `/stories/${s.key}/overview`, icon: '📖',
      children: [
        { id: `${s.key}-overview`, label: 'Overview', href: `/stories/${s.key}/overview` },
        { id: `${s.key}-backgrounds`, label: 'Backgrounds', href: `/stories/${s.key}/backgrounds` },
        { id: `${s.key}-cast`, label: 'Cast & wardrobe', href: `/stories/${s.key}/cast`, children: castLeaves },
        { id: `${s.key}-edit`, label: '✎ Edit story', href: `/stories/${s.key}/edit` },
        { id: `${s.key}-play`, label: '▶ Play', href: `/stories/${s.key}/play` }
      ]
    };
  });

  return [
    { id: 'library', label: 'Library', href: '/stories', match: 'exact' },
    ...storyFolders
  ];
}

// Active-section entry: returns the node tree for the current top-level section.
export function treeFor(section) {
  switch (section) {
    case 'settings': return settingsTree();
    case 'characters': return charactersTree();
    case 'images': return imagesTree();
    case 'stories': return storiesTree();
    default: return [];
  }
}

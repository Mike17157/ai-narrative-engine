// Per-section folder-tree definitions for TreeNav. Each function returns a plain
// node tree ([{ id, label, href, icon?, disabled?, children?, match? }]) built
// from current store state, so counts stay live. No callbacks — TreeNav renders
// leaves as <a href>, navigation is native. ONE nav model for every section;
// no in-page menu fragments anywhere.

import { chars, charName } from './characters.svelte.js';
import { stories } from './stories.svelte.js';
import { app } from './app.svelte.js';

// Models and Connections are each a folder of three sibling leaves — no in-page switcher.
const MODELS = [
  { id: 'models', label: 'Models', children: [
    { id: 'models-chat', label: 'Chat', href: '/settings/models/chat' },
    { id: 'models-image-prompt', label: 'Image prompt', href: '/settings/models/image-prompt' },
    { id: 'models-image', label: 'Image', href: '/settings/models/image' }
  ]}
];
const CONNECTIONS = [
  { id: 'connections', label: 'Connections', children: [
    { id: 'conn-chat', label: 'Chat', href: '/settings/connections/chat' },
    { id: 'conn-image-prompt', label: 'Image prompt', href: '/settings/connections/image-prompt' },
    { id: 'conn-image', label: 'Image', href: '/settings/connections/image' }
  ]}
];

// Story generation config — relocated from /stories/config. Capped at 2 levels:
// Settings → Story generation → stage. The Generation/Imaging grouping is rendered
// as in-folder HEADERS (header: true), not a navigable third level. Global config
// (story_builder.json + image_roles.json), not per-story.
const STORY_GEN = [
  { id: 'story-gen', label: 'Story generation', children: [
    { id: 'sg-gen-h', label: 'Generation', header: true },
    { id: 'sg-storyboard', label: 'Storyboard', href: '/settings/story-gen?section=storyboard' },
    { id: 'sg-locations', label: 'Scenes', href: '/settings/story-gen?section=locations' },
    { id: 'sg-characters', label: 'Characters', href: '/settings/story-gen?section=characters' },
    { id: 'sg-wardrobe', label: 'Wardrobe', href: '/settings/story-gen?section=wardrobe' },
    { id: 'sg-base_image', label: 'Base features', href: '/settings/story-gen?section=base_image' },
    { id: 'sg-img-h', label: 'Imaging', header: true },
    { id: 'sg-base', label: 'Base render', href: '/settings/story-gen?section=base' },
    { id: 'sg-style', label: 'Style', href: '/settings/story-gen?section=style' },
    { id: 'sg-sprite', label: 'Sprites', href: '/settings/story-gen?section=sprite' },
    { id: 'sg-scene', label: 'Scene render', href: '/settings/story-gen?section=scene' },
    { id: 'sg-chat', label: 'Chat', href: '/settings/story-gen?section=chat' }
  ]}
];

export function settingsTree() {
  return [
    ...MODELS,
    ...CONNECTIONS,
    ...STORY_GEN,
    { id: 'comfyui', label: 'ComfyUI', href: '/settings/comfyui' },
    { id: 'trainer', label: 'Trainer', href: '/settings/trainer' },
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

// Roles moved to Settings ▸ Story generation ▸ Imaging. LoRA keeps its sub-leaves.
export function imagesTree() {
  return [
    { id: 'models', label: 'Models', href: '/images/models' },
    { id: 'graph', label: 'Graph', href: '/images/graph' },
    { id: 'poses', label: 'Poses', href: '/images/poses' },
    { id: 'lora', label: 'LoRA', href: '/images/lora/library' }
  ];
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

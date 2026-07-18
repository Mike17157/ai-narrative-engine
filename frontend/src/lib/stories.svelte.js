// Story UI state. Navigation is route-based (routes/stories/**); this store is data only —
// the library list, the loaded story, the edit clone, and per-story canvas view state.
// Creating from scratch mints one empty persisted card. There is no client-held
// draft and no separate genesis state machine.
import { browser } from '$app/environment';
import { goto } from '$app/navigation';
import { get, post, del } from './api.js';
import { loadChars } from './characters.svelte.js';

const leanStoryMode = import.meta.env.VITE_LEAN_STORY === '1';

// Per-story canvas view state (graph pan/zoom + list-vs-graph mode), persisted
// so swapping story stages and reloading keeps your place. Keyed by story key.
const VIEW_LS = 'loom.storyView';
function loadView() {
  if (!browser) return {};
  try { return JSON.parse(localStorage.getItem(VIEW_LS) || '{}') || {}; } catch { return {}; }
}
function saveView() {
  if (!browser) return;
  try { localStorage.setItem(VIEW_LS, JSON.stringify(stories.view)); } catch { /* quota / disabled */ }
}
function viewFor(key) { return (stories.view[key] ||= { mode: 'graph', viewport: null }); }
export function storyMode(key) { return viewFor(key).mode; }
export function setStoryMode(key, mode) { viewFor(key).mode = mode; saveView(); }
export function storyViewport(key) { return viewFor(key).viewport; }
export function setStoryViewport(key, vp) { viewFor(key).viewport = vp; saveView(); }

export const stories = $state({
  list: [],
  current: null,           // the loaded story for /stories/[key]
  view: loadView(),        // { [key]: { mode, viewport } } — canvas state, persisted
  editing: null,           // editable clone of `current` (the edit page)
  textModels: [],          // for per-stage model pickers
  imageModels: [],         // scene workflows (backgrounds)
  saving: false,
  msg: null,
});

export async function loadStories() {
  try {
    stories.list = await get('/stories');
  } catch { stories.list = []; }
}
export async function loadStory(key) {
  try {
    const story = await get(`/stories/${key}`);
    if (!story || typeof story !== 'object' || Array.isArray(story)) throw new Error('The story server returned no author card');
    try {
      const authoring = await get(`/stories/${key}/interview-history`);
      story.authoring_history = authoring?.history || [];
    } catch { story.authoring_history = []; }
    stories.current = story;
    return stories.current;
  }
  catch { stories.current = null; return null; }
}
export async function loadModels() {
  try {
    stories.textModels = (await get('/text-models?kind=text')).models || [];
    stories.imageModels = (await get('/models')).image || [];
  } catch { /* offline */ }
}

export async function createStory({ name = 'Untitled story', type = 'novel' } = {}) {
  const result = await post('/stories/new', { name, type });
  if (!result.ok || !result.data?.key) {
    throw new Error(result.data?.error || 'Could not create the story card.');
  }
  return result.data.key;
}

// --- saved-story actions (route-based) ------------------------------------ //
export async function deleteStory(key) {
  await del(`/stories/${key}`);
  await loadStories();
  // The lean API intentionally has no global character library; its cast data
  // is always loaded through a specific Story.
  if (!leanStoryMode) await loadChars();
  if (stories.current?.key === key) { stories.current = null; goto('/stories'); }
}

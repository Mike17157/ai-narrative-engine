// Shared character state. The story workspace loads the cast story-scoped; the
// personas gallery (Settings ▸ Personas) loads the global list. The old
// Characters pane (selected / browse / import) was retired — character cards
// are authored inside stories now.
import { get, del } from './api.js';

export const chars = $state({ list: [], importing: false, msg: null });

export async function loadChars(storyKey = null) {
  const path = storyKey ? `/stories/${encodeURIComponent(storyKey)}/cast` : '/characters';
  const result = await get(path);
  chars.list = Array.isArray(result) ? result : [];
}

// Resolve a character key → display name. Falls back to a de-slugified version of the key
// (e.g. "riley_costello" → "Riley Costello") so a raw key never surfaces when chars.list is
// momentarily stale (e.g. right after a cast is generated).
export const prettyKey = (k) => (k || '').replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
export const charName = (k) => chars.list.find((c) => c.key === k)?.name || prettyKey(k);

// Delete a character (also strips it from any story cast server-side).
export async function deleteChar(key) {
  await del(`/characters/${key}`);
  await loadChars();
}

// Short tagline — the card's creator notes, else the first line of the persona.
export const blurb = (c) =>
  (c.fields?.creator_notes || '').split('\n').find((l) => l.trim()) ||
  (c.system || '').split('\n').find((l) => l.trim()) ||
  'No description.';

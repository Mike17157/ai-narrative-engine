// Shared state for the Characters section (owned by routes/characters/+layout.svelte,
// consumed by the selected/search/import pages). Lifted out of the old
// CharactersPanel so the list + import status are shared across sub-routes.
import { goto } from '$app/navigation';
import { get, post, del } from './api.js';
import { loadStories } from './stories.svelte.js';
import { refreshHealth, setActiveChar } from './app.svelte.js';

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

// Selecting a character jumps to its detail page.
export function selectChar(key) { setActiveChar(key); goto('/characters/selected'); }

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

async function finishImport(r, seedStory = false) {
  if (r.data?.ok) {
    await loadChars();
    await refreshHealth();
    setActiveChar(r.data.key);
    chars.msg = { ok: true, text: `✓ Imported ${r.data.name}` };
    if (seedStory) {
      const seeded = await post('/stories/from-cast', {
        name: r.data.name,
        characters: [r.data.key],
        premise: r.data.name ? `A story seeded by ${r.data.name}.` : ''
      });
      if (seeded.data?.ok) {
        await loadStories();
        goto(`/stories/${seeded.data.key}`);
        return;
      }
      chars.msg = { err: true, text: seeded.data?.error || 'Character imported, but story seed failed' };
    }
    goto('/characters/selected');
  } else {
    chars.msg = { err: true, text: r.data?.error || 'import failed' };
  }
}

export async function importFile(file, seedStory = false) {
  chars.importing = true; chars.msg = null;
  try {
    const data_b64 = await readBase64(file);
    await finishImport(await post('/characters/import', { filename: file.name, data_b64 }), seedStory);
  } catch (err) { chars.msg = { err: true, text: String(err) }; }
  chars.importing = false;
}

export async function importUrl(url, seedStory = false) {
  const u = (url || '').trim();
  if (!u || chars.importing) return;
  chars.importing = true; chars.msg = null;
  try {
    await finishImport(await post('/characters/import-url', { url: u }), seedStory);
  } catch (err) { chars.msg = { err: true, text: String(err) }; }
  chars.importing = false;
}

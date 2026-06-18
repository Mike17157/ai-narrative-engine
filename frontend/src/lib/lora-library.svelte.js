// Shared store for the LoRA Library subsystem (cfg + family metadata).
// Owned by the Library page, consumed by ClassifyCard / LibraryCard /
// StackBuilder / TestBench. Classify writes library entries that Library,
// Stacks, and Test all read — so this is one reactive source of truth rather
// than prop-drilled two-way bindings across four cards.
//
// Sibling to lora.svelte.js (which owns the generation/dataset pipeline); the
// two are intentionally separate — the library/stacks config is a different
// concern from the batch-generation runtime state.
import { post } from './api.js';

export const loraLib = $state({
  cfg: { library: [], stacks: [] },
  choices: { checkpoints: [], loras: [] },
  bases: [],          // image workflows: [{key, base, checkpoint, arch, family}]
  scan: { items: [] },   // signature-classified model index
  families: [],          // [{id,label,arch}] registry
  showAll: false,        // shared "show all compatible" toggle (Classify + Stacks)
  saving: false,
  msg: null,
});

// --- family helpers ----------------------------------------------------------
// Sub-family (illustrious/pony/anima/…) from the scan, layered on the tensor
// arch. Folder name is the fallback when a file isn't in the scan yet.
export const normRel = (n) => (n || '').replace(/\\/g, '/');
export const folderFam = (name) => {
  const p = name.split(/[\\/]/);
  return (p.length > 1 ? p[0] : 'unknown').toLowerCase();
};

export const loraFamMap = $derived(Object.fromEntries(
  (loraLib.scan.items || []).filter((i) => i.kind === 'lora')
    .map((i) => [i.rel, i.family || 'unknown'])));
export const baseFamMap = $derived(Object.fromEntries(
  (loraLib.scan.items || []).filter((i) => i.kind === 'checkpoint' || i.kind === 'diffusion')
    .map((i) => [i.rel, i.family || 'unknown'])));
export const famArch = $derived(Object.fromEntries(
  (loraLib.families || []).map((f) => [f.id, (f.arch || '').toLowerCase()])));
export const famLabel = $derived(Object.fromEntries(
  (loraLib.families || []).map((f) => [f.id, f.label])));

export const famOf = (name) => loraFamMap[normRel(name)] || folderFam(name);

// Soft compatibility: 'native' (same family), 'cross' (same arch, different
// sub-family — usually works), or 'incompatible' (different arch).
export function compat(lf, mf) {
  if (!lf || !mf || lf === 'unknown' || mf === 'unknown') return 'cross';
  if (lf === mf) return 'native';
  return (famArch[lf] && famArch[lf] === famArch[mf]) ? 'cross' : 'incompatible';
}

// Reused option lists for the comboboxes.
export const loraItems = $derived((loraLib.choices.loras || []).map((c) => ({ value: c, label: c })));
export const ckItems = $derived([
  { value: '', label: '— workflow default —' },
  ...(loraLib.choices.checkpoints || []).map((c) => ({ value: c, label: c })),
]);
export const baseFamByKey = $derived(Object.fromEntries(loraLib.bases.map((b) => [b.key, b.family])));

// The API stores keys as arrays; the edit UI joins them to comma strings.
// Hydrate one way (load), serialize back on save.
function hydrate(c) {
  return {
    library: (c.library || []).map((l) => ({ ...l, keys: (l.keys || []).join(', ') })),
    stacks: (c.stacks || []).map((s) => ({
      name: s.name, checkpoint: s.checkpoint || '',
      loras: (s.loras || []).map((m) => ({
        name: m.name, weight: m.weight ?? 0.7, role: m.role || 'uncategorized',
        keys: (m.keys || []).join(', '), threshold: m.threshold ?? null,
      })),
    })),
  };
}

export function setCfg(c) { loraLib.cfg = hydrate(c); }

const csv = (s) => (s || '').split(',').map((k) => k.trim()).filter(Boolean);

export async function save() {
  loraLib.saving = true;
  loraLib.msg = { text: 'Saving…' };
  const payload = {
    library: loraLib.cfg.library.filter((l) => l.name)
      .map((l) => ({ name: l.name, type: l.type, weight: +l.weight || 1, enabled: !!l.enabled, comment: l.comment || '' })),
    stacks: loraLib.cfg.stacks.filter((s) => s.name).map((s) => ({
      name: s.name, checkpoint: s.checkpoint || null,
      loras: (s.loras || []).filter((m) => m.name).map((m) => ({
        name: m.name, weight: +m.weight || 0.7, role: m.role || 'uncategorized',
        keys: csv(m.keys), threshold: (m.threshold === null || m.threshold === '') ? null : +m.threshold,
      })),
    })),
  };
  const res = await post('/loras', payload);
  loraLib.msg = res.data?.ok
    ? { ok: true, text: '✓ Saved' }
    : { err: true, text: res.data?.error || 'save failed' };
  loraLib.saving = false;
}

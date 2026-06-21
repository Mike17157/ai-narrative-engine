// Shared state for the LoRA generation pipeline (the prompt-set builder + the
// live batch grid). Owned by the Training section, consumed by PromptSet and
// GenerateCard. The batch + streaming grid survive navigating away because the
// job is server-resident and the Training layout reattaches on mount.
import { get, post } from './api.js';
import { consumeSse } from './sse.js';
import { app } from './app.svelte.js';

export const lora = $state({
  sets: [], curSet: '', newSetName: '',
  promptsText: '', variations: 5,
  genCount: 60, genTheme: 'anime characters, diverse scenes, varied lighting', genBusy: false, genMsg: null,
  zoom: null, // { src, cap } — enlarged image lightbox
  rows: [], progress: { done: 0, total: 0 }, busy: false, cancelling: false, jobStatus: null,
  baseModel: '', saveName: 'my-lora-set', saveMsg: null, choices: {}
});

export const promptList = () => lora.promptsText.split('\n').map((s) => s.trim()).filter(Boolean);
export const selectedCount = () => lora.rows.reduce((n, r) => n + r.cells.filter((c) => c.kept).length, 0);

export async function loadSets() { lora.sets = (await get('/lora/sets')).sets || []; }
export async function loadChoices() { if (!Object.keys(lora.choices).length) lora.choices = await get('/comfy/choices'); }

export async function loadSet(name) {
  const d = await get('/lora/sets/' + encodeURIComponent(name));
  if (d.prompts) { lora.promptsText = d.prompts.join('\n'); lora.curSet = name; }
}
export async function saveAsSet() {
  const name = lora.newSetName.trim();
  if (!name || !promptList().length) return;
  await post('/lora/sets', { name, prompts: promptList() });
  await loadSets(); lora.curSet = name; lora.newSetName = '';
}

export async function fillFromTheme() {
  lora.genBusy = true; lora.genMsg = { text: 'Generating…' };
  const r = await post('/lora/prompts', { count: lora.genCount, theme: lora.genTheme, model: null });
  lora.genBusy = false;
  if (r.data?.prompts) { lora.promptsText = r.data.prompts.join('\n'); lora.genMsg = { ok: true, text: `✓ ${r.data.prompts.length} prompts` }; }
  else lora.genMsg = { err: true, text: r.data?.error || 'failed' };
}
export async function genImages() {
  const ps = promptList();
  if (!ps.length || !app.activeImage) return;
  const r = await post('/lora/generate', { model: app.activeImage, prompts: ps, variations: lora.variations });
  if (r.status === 409) { lora.genMsg = { err: true, text: 'a batch is already running' }; attach(); return; }
  if (r.data?.error) { lora.genMsg = { err: true, text: r.data.error }; return; }
  lora.rows = []; lora.jobStatus = null;
  attach();
}

// Consume the job's replay+live SSE. Safe to call on entering the section
// (rebuilds the grid) or right after starting one. Guarded against double-attach.
export async function attach() {
  if (lora.busy) return;
  lora.busy = true; lora.cancelling = false;
  try {
    const res = await fetch('/api/lora/stream');
    if (res.status === 204 || !res.body) { lora.busy = false; return; }
    await consumeSse(res, (ev) => {
      if (ev.type === 'start') {
        if (!lora.rows.length) lora.rows = ev.prompts.map((p) => ({ prompt: p, cells: Array.from({ length: ev.variations }, () => ({})), selected: null }));
        lora.variations = ev.variations;
        lora.progress = { done: lora.progress.done, total: ev.total };
      } else if (ev.type === 'image') lora.rows[ev.row].cells[ev.col] = { src: ev.src };
      else if (ev.type === 'error' && ev.row != null) lora.rows[ev.row].cells[ev.col] = { error: ev.error || 'failed' };
      else if (ev.type === 'count') lora.progress = { done: ev.done, total: ev.total };
      else if (ev.type === 'done') lora.jobStatus = ev.status || 'done';
    });
  } catch (e) { lora.genMsg = { err: true, text: String(e) }; }
  lora.busy = false; lora.cancelling = false;
}

export async function cancelJob() { lora.cancelling = true; await post('/lora/cancel'); }

export function pick(ri, ci) {
  const c = lora.rows[ri].cells[ci];
  if (!c?.src) return;
  lora.rows[ri].cells[ci] = { ...c, kept: !c.kept };
}

export async function saveSet() {
  const images = lora.rows.flatMap((r) => r.cells.filter((c) => c.kept).map((c) => ({ src: c.src, caption: r.prompt })));
  if (!images.length) { lora.saveMsg = { err: true, text: 'click the images you want to keep first' }; return; }
  lora.saveMsg = { text: 'Saving…' };
  const r = await post('/lora/save', { name: lora.saveName, base_model: lora.baseModel || null, images });
  lora.saveMsg = r.data?.ok ? { ok: true, text: `✓ Saved ${r.data.count} → ${r.data.path}` } : { err: true, text: r.data?.error || 'failed' };
}

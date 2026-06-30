<script>
  // Novel Plot lens: the linear chapter manuscript. Each chapter is a HARNESS (purpose / POV /
  // setting / beats) you author, then DRAFT into prose (Narrative voice) and CONSOLIDATE into a
  // carry-forward recap (Storymaster). Generation is serial + gated — a chapter can only draft once
  // the previous one is drafted. See [[multiformat-story-engine]].
  import { post, put } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let { storyKey, chapters = [], onChange = () => {} } = $props();

  let chs = $state(chapters.map((c) => ({ ...c })));
  let seededKey = null;
  $effect(() => { if (storyKey !== seededKey) { seededKey = storyKey; chs = chapters.map((c) => ({ ...c })); } });

  let busyId = $state('');     // chapter id currently generating
  let err = $state('');
  let openId = $state('');     // expanded chapter (draft preview / editor)

  let saveT = null;
  function persist() { clearTimeout(saveT); saveT = setTimeout(() => put(`/stories/${storyKey}`, { chapters: chs }), 600); }
  function uid() { return 'ch-' + Math.random().toString(36).slice(2, 8); }

  function addChapter() {
    chs = [...chs, { id: uid(), title: '', purpose: '', pov: '', setting: '', beats: [], status: 'outline', draft: '', recap: '' }];
    openId = chs[chs.length - 1].id;
    persist();
  }
  function removeChapter(i) { const id = chs[i].id; chs = chs.filter((_, j) => j !== i); if (openId === id) openId = ''; persist(); }
  function setBeats(c, v) { c.beats = v.split('\n').map((s) => s.trim()).filter(Boolean); chs = [...chs]; persist(); }

  const canDraft = (i) => i === 0 || chs[i - 1]?.status === 'drafted';

  async function draft(i) {
    const c = chs[i]; if (!c || busyId) return;
    busyId = c.id; err = '';
    const r = await post(`/stories/${storyKey}/chapter/${c.id}/draft`, {});
    busyId = '';
    if (r.ok && r.data?.draft) { c.draft = r.data.draft; c.status = 'drafted'; chs = [...chs]; openId = c.id; onChange(); }
    else err = r.data?.error || 'draft failed';
  }
  async function consolidate(i) {
    const c = chs[i]; if (!c || busyId) return;
    busyId = c.id; err = '';
    const r = await post(`/stories/${storyKey}/chapter/${c.id}/consolidate`, {});
    busyId = '';
    if (r.ok && r.data?.recap) { c.recap = r.data.recap; chs = [...chs]; onChange(); }
    else err = r.data?.error || 'consolidate failed';
  }
</script>

<div class="chapters">
  {#each chs as c, i (c.id)}
    <div class="ch" class:open={openId === c.id}>
      <div class="ch-head">
        <span class="num">{i + 1}</span>
        <input class="ip ch-title" bind:value={c.title} oninput={persist} placeholder="Chapter title…" />
        <span class="status {c.status}">{c.status === 'drafted' ? '✓ drafted' : 'outline'}</span>
        <button class="x" onclick={() => (openId = openId === c.id ? '' : c.id)} title="Edit / preview">{openId === c.id ? '▾' : '▸'}</button>
        <button class="x del" onclick={() => removeChapter(i)} title="Remove chapter">×</button>
      </div>

      {#if openId === c.id}
        <div class="ch-body">
          <div class="harness">
            <div class="row2">
              <input class="ip sm" bind:value={c.pov} oninput={persist} placeholder="POV (character)" />
              <input class="ip sm" bind:value={c.setting} oninput={persist} placeholder="Setting" />
            </div>
            <input class="ip" bind:value={c.purpose} oninput={persist} placeholder="Purpose — what this chapter accomplishes" />
            <textarea class="ip" use:autosize={(c.beats || []).join('\n')} value={(c.beats || []).join('\n')}
                      oninput={(e) => setBeats(c, e.currentTarget.value)} placeholder="Beats — one per line"></textarea>
          </div>

          <div class="acts">
            <button class="primary" onclick={() => draft(i)} disabled={busyId === c.id || !canDraft(i)}
              title={canDraft(i) ? '' : 'Draft the previous chapter first'}>
              {busyId === c.id ? 'drafting…' : (c.status === 'drafted' ? '↻ Redraft' : '✎ Draft chapter')}
            </button>
            {#if c.status === 'drafted'}
              <button onclick={() => consolidate(i)} disabled={busyId === c.id}>
                {busyId === c.id ? '…' : (c.recap ? '↻ Re-consolidate' : '🌙 Consolidate')}
              </button>
            {/if}
            {#if !canDraft(i)}<span class="lock">🔒 drafts after chapter {i}</span>{/if}
          </div>

          {#if c.recap}<div class="recap"><span class="rl">Recap →</span> {c.recap}</div>{/if}
          {#if c.draft}<div class="draft">{c.draft}</div>{/if}
        </div>
      {/if}
    </div>
  {:else}
    <div class="empty">No chapters yet. Outline the first one — or ask the Author to.</div>
  {/each}

  {#if err}<div class="cerr">{err}</div>{/if}
  <button class="add" onclick={addChapter}>＋ Add chapter</button>
</div>

<style>
  .chapters { display: flex; flex-direction: column; gap: 8px; }
  .ch { border: 1px solid var(--border); border-radius: var(--radius); background: var(--elev); }
  .ch.open { border-color: var(--border-strong); }
  .ch-head { display: flex; align-items: center; gap: 9px; padding: 8px 10px; }
  .num { flex: none; width: 22px; height: 22px; border-radius: 50%; background: var(--elev-2); color: var(--muted);
         font-size: 11.5px; display: grid; place-items: center; }
  .ch-title { flex: 1; background: none; border: 0; padding: 2px 0; font-size: 14px; font-weight: 540; color: var(--text); }
  .ch-title:focus { outline: none; }
  .status { flex: none; font-size: 11px; padding: 2px 9px; border-radius: 999px; border: 0.5px solid var(--border);
            color: var(--muted); background: var(--elev-2); }
  .status.drafted { color: var(--good); border-color: color-mix(in srgb, var(--good) 40%, transparent); }
  .x { background: none; border: 0; color: var(--muted); font-size: 14px; padding: 2px 6px; cursor: pointer; }
  .x:hover { color: var(--text); background: none; }
  .x.del:hover { color: var(--bad); }
  .ch-body { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 10px; }
  .harness { display: flex; flex-direction: column; gap: 7px; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
  .ip { width: 100%; font: inherit; font-size: 13px; color: var(--text); background: var(--bg);
        border: 1px solid var(--border); border-radius: var(--radius); padding: 7px 10px; }
  .ip:focus { outline: none; border-color: var(--border-strong); }
  .ip.sm { font-size: 12.5px; }
  .acts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .lock { font-size: 11.5px; color: var(--faint); }
  .recap { font-size: 12.5px; color: var(--muted); line-height: 1.5; background: var(--elev-2);
           border-radius: var(--radius); padding: 8px 10px; }
  .recap .rl { color: var(--faint); font-weight: 540; }
  .draft { font-size: 13px; line-height: 1.65; color: var(--text); white-space: pre-wrap;
           border-top: 0.5px solid var(--border); padding-top: 10px; max-height: 380px; overflow: auto; }
  .empty { padding: 22px 4px; color: var(--faint); font-size: 13px; }
  .cerr { font-size: 12px; color: var(--bad); }
  .add { align-self: flex-start; font-size: 12.5px; }
</style>

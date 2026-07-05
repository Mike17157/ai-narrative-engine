<script>
  // SETTING CONDITIONS — the recurring stages the world moves through (seasons, event-states,
  // place-states). Authored vocabulary that situation-keyed character content (`when:<id>`) will
  // switch on. ✨ Generate reasons them from the premise + geography; edit in place. Slice (a):
  // this authors the stage-set; the play-time activation + when-tagged exemplars come next.
  import { get, post, put } from '$lib/api.js';

  let { storyKey, conditions = [], onChange = () => {} } = $props();

  // Proposals live in the story's PENDING store (fields.pending.conditions) — generated
  // output survives navigation and shows in the work queue until reviewed here.
  async function syncPending(items) {
    props = items;
    await put(`/stories/${storyKey}/pending/conditions`, { items });
    window.dispatchEvent(new CustomEvent('queue:refresh'));
  }

  // LINT: how many cast exemplars each stage activates (`when:<id>` tags), + orphaned
  // when: ids whose stage no longer exists — that content is silently dormant forever.
  let usage = $state({});
  let orphans = $state({});
  async function loadUsage() {
    const r = await get(`/stories/${storyKey}/conditions/usage`);
    usage = r?.usage || {}; orphans = r?.orphans || {};
  }
  loadUsage();
  (async () => {   // resume any unreviewed proposals from the pending store
    const r = await get(`/stories/${storyKey}/pending`);
    const items = r?.pending?.conditions?.items;
    if (items?.length && !props.length) props = items;
  })();

  const KINDS = ['seasonal', 'event', 'place'];
  const ICON = { seasonal: '❄', event: '⚔', place: '🚪' };

  let busy = $state(false);
  let err = $state('');
  let props = $state([]);   // generated proposals awaiting review

  let uid = 0;
  const mkId = (nm) => (nm || 'stage').toLowerCase().replace(/[^\w]+/g, '_').replace(/^_|_$/g, '') || `cond_${uid++}`;

  async function generate() {
    if (busy) return;
    busy = true; err = '';
    const r = await post(`/stories/${storyKey}/conditions/generate`, {});
    busy = false;
    if (r.ok && r.data?.conditions) {
      props = r.data.conditions;   // the endpoint already persisted these as pending
      window.dispatchEvent(new CustomEvent('queue:refresh'));
      if (!props.length) err = 'nothing new to propose';
    } else err = r.data?.error || 'generation failed';
  }
  function accept(i) { const c = props[i]; syncPending(props.filter((_, j) => j !== i)); onChange([...(conditions || []), c]); }
  function acceptAll() { const add = props; syncPending([]); onChange([...(conditions || []), ...add]); }
  function reject(i) { syncPending(props.filter((_, j) => j !== i)); }

  function addBlank() { onChange([...(conditions || []), { id: mkId(''), name: '', kind: 'seasonal', description: '', effect: '' }]); }
  function remove(i) { onChange((conditions || []).filter((_, j) => j !== i)); }
  function edit() { onChange([...(conditions || [])]); }   // fields bind directly; nudge a save
</script>

<div class="conds">
  <div class="chead">
    <p class="hint">The stages this world moves through — each a different way to live that brings out
      new sides of the cast. Situational character reactions and secrets key to these.</p>
    <span class="sp"></span>
    <button class="gen" onclick={generate} disabled={busy}>{busy ? '✨ Reasoning…' : '✨ Generate stages'}</button>
  </div>
  {#if err}<div class="err">{err}</div>{/if}

  {#if props.length}
    <div class="props">
      <div class="phead"><b>Proposed stages</b><span class="hint">— review; nothing saved until kept</span>
        <span class="sp"></span><button class="pall" onclick={acceptAll}>✓ Keep all</button></div>
      {#each props as p, i (p.id)}
        <div class="prop">
          <div class="prow"><span class="ic">{ICON[p.kind] || '◆'}</span><b>{p.name}</b>
            <span class="kind">{p.kind}</span><span class="sp"></span>
            <button class="pacc" onclick={() => accept(i)}>✓</button>
            <button class="prej" onclick={() => reject(i)}>×</button></div>
          <div class="pd">{p.description}</div>
          <div class="pe"><span class="lbl">daily life</span> {p.effect}</div>
        </div>
      {/each}
    </div>
  {/if}

  {#if Object.keys(orphans).length}
    <div class="orph">⚠ Situational character content bound to stages that no longer exist
      (dormant until the id is restored or the exemplars re-tagged):
      {#each Object.entries(orphans) as [id, n] (id)}<code>{id}</code> ({n}){/each}
    </div>
  {/if}

  {#each conditions as c, i (c.id || i)}
    <div class="card">
      <div class="crow">
        <span class="ic">{ICON[c.kind] || '◆'}</span>
        <input class="nm" bind:value={c.name} oninput={edit} placeholder="stage name — 'The flood season'" />
        <span class="use" class:zero={!usage[c.id]}
          title="cast exemplars this stage activates (when:{c.id})">⚡ {usage[c.id] || 0}</span>
        <select class="kind-sel" bind:value={c.kind} onchange={edit}>{#each KINDS as k}<option value={k}>{k}</option>{/each}</select>
        <button class="rm" onclick={() => remove(i)} title="Delete">×</button>
      </div>
      <input class="fld" bind:value={c.description} oninput={edit} placeholder="what it is — the objective world-change" />
      <input class="fld" bind:value={c.effect} oninput={edit} placeholder="how it bends daily life — what stops, what people do differently" />
    </div>
  {/each}

  <button class="addb" onclick={addBlank}>＋ Add a stage</button>
</div>

<style>
  .conds { display: flex; flex-direction: column; gap: 8px; }
  .chead { display: flex; align-items: flex-start; gap: 10px; }
  .hint { margin: 0; }
  .sp { flex: 1; }
  .gen { flex: none; font-size: 12px; font-weight: 600; padding: 6px 13px; border-radius: 999px;
         background: none; border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .gen:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .err { font-size: 12px; color: var(--bad, #d0655a); }

  .props { display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-radius: 12px;
           border: 1px dashed var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .phead { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
  .pall { font-size: 11.5px; padding: 4px 11px; border-radius: 999px; background: var(--accent); border: none; color: #fff; font-weight: 700; cursor: pointer; }
  .prop { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; border-radius: 9px; background: var(--elev); border: 1px solid var(--border-soft); }
  .prow { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .kind { font-size: 10px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .pacc, .prej { width: 24px; height: 24px; padding: 0; border-radius: 7px; background: var(--elev-2, var(--bg)); border: 1px solid var(--border-soft); cursor: pointer; }
  .pacc { color: var(--good, #5ec27a); } .prej { color: var(--muted); }
  .pd { font-size: 12px; color: var(--muted); line-height: 1.5; }
  .pe { font-size: 11.5px; color: var(--text); line-height: 1.5; }
  .lbl { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); margin-right: 5px; }

  .card { display: flex; flex-direction: column; gap: 5px; padding: 9px 11px; border-radius: 10px; background: var(--panel); border: 1px solid var(--border-soft); }
  .crow { display: flex; align-items: center; gap: 8px; }
  .ic { flex: none; font-size: 15px; }
  .nm { flex: 1; min-width: 0; font-size: 13px; font-weight: 700; color: var(--text); background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 4px 7px; }
  .nm:hover { border-color: var(--border-soft); } .nm:focus { outline: none; border-color: var(--accent); background: var(--elev); }
  .kind-sel { font-size: 11px; color: var(--muted); background: var(--bg); border: 1px solid var(--border-soft); border-radius: 6px; padding: 4px 6px; }
  .rm { width: 22px; height: 22px; padding: 0; border-radius: 6px; background: none; border: none; color: var(--muted); font-size: 15px; cursor: pointer; }
  .rm:hover { color: var(--bad, #d0655a); }
  .fld { width: 100%; box-sizing: border-box; font-size: 12px; color: var(--text); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 7px; padding: 6px 8px; }
  .fld:focus { outline: none; border-color: var(--accent); }
  .addb { align-self: flex-start; font-size: 12.5px; padding: 6px 12px; border-radius: 8px; background: var(--elev); border: 1px dashed var(--border); color: var(--muted); cursor: pointer; }
  .addb:hover { border-color: var(--accent); color: var(--accent); }
  .use { flex: none; font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev);
         border: 1px solid var(--border-soft); color: var(--accent); cursor: default; }
  .use.zero { color: var(--faint); }
  .orph { font-size: 11.5px; color: var(--bad, #d0655a); padding: 8px 11px; border-radius: 9px;
          border: 1px solid color-mix(in srgb, var(--bad, #d0655a) 35%, transparent);
          background: color-mix(in srgb, var(--bad, #d0655a) 6%, var(--panel)); line-height: 1.6; }
  .orph code { margin: 0 3px; padding: 1px 6px; border-radius: 5px; background: var(--elev); color: var(--text); }
</style>

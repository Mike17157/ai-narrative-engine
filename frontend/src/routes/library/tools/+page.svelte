<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';

  // Tools catalog — what each tool is, its params, and its SOURCE (the real implementation).
  // Tools are CODE (loom/stories/scripts.py + stage_tools.py); the modal lets you edit the source —
  // it's parse-checked, and a backend restart applies it. Grouped by the chat MODE that offers each
  // (read from configs/story_agent.json — the agent's real menu); pipeline-only tools group separately.

  let graph = $state([]);
  let stage = $state([]);
  let loading = $state(true);
  let q = $state('');

  onMount(async () => {
    try { const r = await get('/tools'); graph = r.graph || []; stage = r.stage || []; }
    catch { graph = []; stage = []; }
    loading = false;
  });

  const match = (t) => !q.trim() ||
    (t.fn + ' ' + (t.describe || '') + ' ' + (t.keywords || []).join(' ')).toLowerCase().includes(q.toLowerCase());

  // Group the catalog by the chat MODE that offers each tool (from story_agent.json). A tool can be
  // offered by more than one mode (e.g. generate_image), so it appears under each. Pipeline-only
  // tools (wired by a function book, not chat-callable) group last; anything else is "Unbound".
  let allTools = $derived([...graph, ...stage]);
  const rank = (g) => (g === 'Modes' ? 0 : g === 'Pipeline' ? 1 : 2);
  const groupHint = (g) => g === 'Modes' ? 'tools this mode offers in chat'
    : g === 'Pipeline' ? 'pipeline-only — not chat-callable' : '';
  let groups = $derived.by(() => {
    const by = new Map();
    const unbound = [];
    for (const t of allTools.filter(match)) {
      const ags = t.agents || [];
      if (!ags.length) { unbound.push(t); continue; }
      for (const a of ags) {
        if (!by.has(a.id)) by.set(a.id, { id: a.id, name: a.name, group: a.group, tools: [] });
        by.get(a.id).tools.push(t);
      }
    }
    const arr = [...by.values()].sort((a, b) => rank(a.group) - rank(b.group) || a.name.localeCompare(b.name));
    if (unbound.length) arr.push({ id: '_unbound', name: 'Unbound', group: '', unbound: true, tools: unbound });
    return arr;
  });

  // Inspect modal — click a tool to read/edit its source (params + keywords + used_by alongside).
  let sel = $state(null);
  let editSrc = $state('');
  let checkMsg = $state('');     // '' | 'ok' | 'saved…' | error text
  let saving = $state(false);
  $effect(() => { editSrc = sel?.source || ''; checkMsg = ''; });
  let msgBad = $derived(checkMsg && checkMsg !== 'ok' && !checkMsg.startsWith('saved'));

  async function checkSrc() {
    if (!sel) return;
    const r = await post(`/tools/${encodeURIComponent(sel.fn)}`, { source: editSrc, validate_only: true });
    checkMsg = r.ok ? 'ok' : (r.data?.error ? `line ${r.data.line ?? '?'}: ${r.data.error}` : 'parse error');
  }
  async function saveSrc() {
    if (!sel) return;
    saving = true;
    const r = await post(`/tools/${encodeURIComponent(sel.fn)}`, { source: editSrc });
    saving = false;
    checkMsg = r.ok ? (r.data?.note || 'saved') : (r.data?.error ? `line ${r.data.line ?? '?'}: ${r.data.error}` : 'save failed');
    if (r.ok) sel.source = editSrc;
  }
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape') sel = null; }} />

<div class="wrap">
  <div class="head">
    <div>
      <h1>Tools</h1>
      <p class="sub">Model-callable tools, grouped by the chat <b>mode</b> that offers them (from the
        story agent config). Click one to read or edit its source.</p>
    </div>
    <input class="search" placeholder="Search tools…" bind:value={q} />
  </div>

  {#if loading}
    <div class="empty">Loading…</div>
  {:else if !groups.length}
    <div class="empty">No tools match.</div>
  {:else}
    {#each groups as g (g.id)}
      <section>
        <div class="ghead">
          <h2>{g.name} <span class="cnt">{g.tools.length}</span></h2>
          <span class="ghint">{g.unbound ? 'not offered by any mode and not wired to the pipeline' : groupHint(g.group)}</span>
        </div>
        <div class="grid">
          {#each g.tools as t (g.id + ':' + t.fn)}
            <button class="card" onclick={() => (sel = t)}>
              <div class="crow">
                <code class="fn">{t.fn}</code>
                <span class="badge {t.kind}">{t.kind}</span>
                {#if t.writes}<span class="writes" title="State-doc level it mutates">writes: {t.writes}</span>{/if}
                {#if t.produces}<span class="writes" title="The artifact it yields">produces: {t.produces}</span>{/if}
              </div>
              <p class="desc">{t.describe}</p>
              {#if t.params && Object.keys(t.params).length}
                <ul class="params">
                  {#each Object.entries(t.params) as [k, v]}<li><code>{k}</code> <span class="pdesc">{v}</span></li>{/each}
                </ul>
              {/if}
              {#if (t.keywords || []).length}
                <div class="kws">{#each t.keywords.slice(0, 8) as k}<span class="kw">{k}</span>{/each}</div>
              {/if}
            </button>
          {/each}
        </div>
      </section>
    {/each}
  {/if}
</div>

<!-- ── Inspect modal — source-centric, editable ─────────────────────────────── -->
{#if sel}
  <div class="modal" onclick={(e) => { if (e.target === e.currentTarget) sel = null; }}>
    <div class="sheet">
      <div class="shead">
        <code class="mfn">{sel.fn}</code>
        <span class="badge {sel.kind}">{sel.kind}</span>
        {#if sel.writes}<span class="writes">writes: {sel.writes}</span>{/if}
        {#if sel.produces}<span class="writes">produces: {sel.produces}</span>{/if}
        <button class="x" onclick={() => (sel = null)}>✕</button>
      </div>
      <p class="mdesc">{sel.describe}</p>

      <div class="metagrid">
        {#if sel.params && Object.keys(sel.params).length}
          <div><h4>Parameters</h4>
            <ul class="mparams">{#each Object.entries(sel.params) as [k, v]}<li><code>{k}</code> <span>{v}</span></li>{/each}</ul></div>
        {/if}
        {#if (sel.keywords || []).length}
          <div><h4>Trigger keywords</h4>
            <div class="kws">{#each sel.keywords as k}<span class="kw">{k}</span>{/each}</div></div>
        {/if}
        <div><h4>Offered in</h4>
          {#if (sel.agents || []).length}
            <div class="kws">{#each sel.agents as a}<span class="kw">{a.name}</span>{/each}</div>
          {:else}<span class="none">no mode offers this; not in the pipeline</span>{/if}</div>
      </div>

      <!-- Source: the central element — editable, parse-checked, restart to apply. -->
      <div class="srcsec">
        <div class="srchead">
          <h4>Source</h4>
          {#if checkMsg}<span class="chk" class:bad={msgBad}>{checkMsg === 'ok' ? '✓ parses' : checkMsg}</span>{/if}
          <span class="spacer"></span>
          <button class="sbtn" onclick={checkSrc}>Check</button>
          <button class="sbtn primary" onclick={saveSrc} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        </div>
        <textarea class="srcedit" bind:value={editSrc} spellcheck="false"
          placeholder={sel.source ? '' : '(source unavailable)'}></textarea>
      </div>
    </div>
  </div>
{/if}

<style>
  .wrap { padding: 14px 16px; max-width: 1100px; }
  .head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
  h1 { margin: 0; font-size: 20px; }
  .sub { margin: 4px 0 0; font-size: 12.5px; color: var(--muted); }
  .sub a { color: var(--accent); }
  .search { width: 240px; }
  section { margin-bottom: 22px; }
  .ghead { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
  .ghead h2 { margin: 0; font-size: 14px; }
  .cnt { color: var(--faint); font-weight: 400; font-size: 12px; }
  .ghint { font-size: 11.5px; color: var(--faint); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px; }
  .card { border: 1px solid var(--border); border-radius: 10px; background: var(--elev); padding: 11px 13px; display: flex; flex-direction: column; gap: 7px;
    text-align: left; font: inherit; color: inherit; width: 100%; cursor: pointer; transition: border-color .12s, background .12s; }
  .card:hover { border-color: var(--accent); background: var(--elev-2); }
  .crow { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .fn { font-family: ui-monospace, monospace; font-size: 13px; font-weight: 700; color: var(--text); }
  .badge { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; padding: 1px 6px; border-radius: 6px; }
  .badge.graph { color: rgba(100,210,130,.95); background: rgba(100,210,130,.12); border: 1px solid rgba(100,210,130,.25); }
  .badge.stage { color: #c7a8ff; background: rgba(160,120,255,.12); border: 1px solid rgba(160,120,255,.28); }
  .writes { margin-left: auto; font-size: 10.5px; color: var(--faint); font-family: ui-monospace, monospace; }
  .desc { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.45; }
  .params { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 3px; }
  .params li { font-size: 11.5px; }
  .params code { font-family: ui-monospace, monospace; color: var(--text); background: var(--elev-2); padding: 0 4px; border-radius: 4px; }
  .pdesc { color: var(--faint); }
  .kws { display: flex; flex-wrap: wrap; gap: 4px; }
  .kw { font-size: 10.5px; color: var(--muted); background: var(--elev-2); border: 1px solid var(--border); border-radius: 999px; padding: 1px 7px; }
  .empty { color: var(--faint); font-size: 13px; padding: 8px 2px; }

  /* Inspect modal */
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,.5); display: grid; place-items: center; z-index: 100; padding: 24px; }
  .sheet { width: min(680px, 94vw); max-height: 88vh; overflow: auto; background: var(--bg); border: 1px solid var(--border); border-radius: 14px; padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; box-shadow: var(--shadow); }
  .shead { display: flex; align-items: center; gap: 9px; }
  .mfn { font-family: ui-monospace, monospace; font-size: 16px; font-weight: 700; color: var(--text); }
  .shead .writes { margin-left: 0; }
  .x { margin-left: auto; background: none; border: 0; font-size: 16px; cursor: pointer; opacity: .6; }
  .x:hover { opacity: 1; }
  .mdesc { margin: 0; font-size: 13px; color: var(--muted); line-height: 1.5; }
  h4 { margin: 0 0 5px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .metagrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px;
    border: 1px solid var(--border); border-radius: 10px; padding: 11px 13px; background: var(--elev); }
  .mparams { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .mparams li { font-size: 12px; color: var(--muted); }
  .mparams code { font-family: ui-monospace, monospace; background: var(--elev-2); padding: 0 5px; border-radius: 4px; color: var(--text); margin-right: 6px; }
  .none { font-size: 11.5px; color: var(--faint); font-style: italic; }

  /* Source — the central, editable element */
  .srcsec { display: flex; flex-direction: column; gap: 6px; flex: 1; min-height: 0; }
  .srchead { display: flex; align-items: center; gap: 10px; }
  .spacer { flex: 1; }
  .chk { font-size: 11.5px; color: #8fc7a0; font-family: ui-monospace, monospace; }
  .chk.bad { color: var(--bad, #e88); }
  .sbtn { font-size: 12px; padding: 4px 12px; border-radius: 7px; border: 1px solid var(--border); background: var(--elev); color: var(--muted); cursor: pointer; }
  .sbtn:hover { border-color: var(--accent); color: var(--text); }
  .srcedit { width: 100%; min-height: 340px; resize: vertical; font-family: ui-monospace, monospace; font-size: 12.5px;
    line-height: 1.55; tab-size: 4; background: var(--elev); border: 1px solid var(--border); border-radius: 8px;
    padding: 10px 12px; color: var(--text); white-space: pre; overflow: auto; }
  .srcedit:focus { outline: none; border-color: var(--accent); }
</style>

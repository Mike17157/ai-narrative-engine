<script>
  // The ARC panel — the planned staged progression play steers through (storymaster.generate_arc).
  // Shows the active arc on the story's play thread (stages · purpose · milestone · planned moments,
  // current stage highlighted) and plans a new one from a one-line request (the request IS the template).
  import { get, post } from '$lib/api.js';
  import Section from './Section.svelte';

  let { storyKey } = $props();
  const sid = `play-${storyKey}`;
  let arc = $state(null);
  let req = $state('');
  let busy = $state(false);
  let err = $state('');

  async function load() {
    const r = await get(`/stories/${storyKey}/arc?sid=${sid}`);   // get() returns the JSON itself
    arc = r?.arc || null;
  }
  load();

  async function plan() {
    if (busy || !req.trim()) return;
    busy = true; err = '';
    const r = await post(`/stories/${storyKey}/arc`, { sid, request: req.trim() });
    busy = false;
    if (r.ok) { arc = r.data.arc; req = ''; }
    else err = r.data?.error || 'arc planning failed';
  }
  let cur = $derived(arc ? Math.min(arc.stage ?? 0, arc.stages.length) : 0);
</script>

<Section icon="🎯" title="Arc" count={arc ? `stage ${Math.min(cur + 1, arc.stages.length)}/${arc.stages.length}` : ''} open>
  <p class="hint">The planned progression play steers toward — each stage has a purpose, ONE concrete
    milestone (the player's to perform; the story sets it up, never performs it), and small planned
    moments scenes weave in. Stages advance automatically when a milestone lands on the page.</p>
  {#if arc}
    <div class="aq"><b>{arc.name}</b>{#if arc.question} <span class="q">— {arc.question}</span>{/if}</div>
    {#each arc.stages as s, i (i)}
      <div class="stg" class:cur={i === cur} class:done={i < cur}>
        <span class="si">{i < cur ? '✓' : i + 1}</span>
        <div class="sb">
          <div class="stt">{s.title}</div>
          <div class="spp">{s.purpose}</div>
          <div class="smm">completes when: {s.milestone}</div>
          {#if s.events?.length}
            <div class="sev">{#each s.events as e, j (j)}<span class="ev">{e}</span>{/each}</div>
          {/if}
        </div>
      </div>
    {/each}
  {/if}
  <div class="planrow">
    <input bind:value={req} onkeydown={(e) => e.key === 'Enter' && plan()}
      placeholder={arc ? 'plan a NEW arc (replaces the current one)…'
        : 'one line — e.g. a village romance: he draws up his courage, fights with himself, and asks her out'} />
    <button class="primary sm" onclick={plan} disabled={busy || !req.trim()}>{busy ? 'Planning…' : (arc ? 'Replace arc' : 'Plan arc')}</button>
  </div>
  {#if err}<p class="err">{err}</p>{/if}
</Section>

<style>
  .aq { font-size: 13px; color: var(--text); }
  .aq .q { color: var(--muted); font-style: italic; }
  .stg { display: flex; gap: 10px; padding: 9px 11px; border-radius: 10px;
    border: 1px solid var(--border-soft); background: var(--panel); opacity: .85; }
  .stg.cur { border-color: var(--accent); opacity: 1; background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .stg.done { opacity: .55; }
  .si { width: 20px; height: 20px; flex: none; border-radius: 50%; display: grid; place-items: center;
    font-size: 10.5px; font-weight: 700; background: var(--elev); color: var(--muted); }
  .stg.cur .si { background: var(--accent); color: #0b0e14; }
  .stg.done .si { color: var(--accent); }
  .sb { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
  .stt { font-size: 12.5px; font-weight: 700; color: var(--text); }
  .spp { font-size: 12px; color: var(--muted); line-height: 1.45; }
  .smm { font-size: 11px; color: var(--faint); font-style: italic; }
  .sev { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; }
  .ev { font-size: 10.5px; padding: 2px 8px; border-radius: 999px; background: var(--elev);
    border: 1px solid var(--border-soft); color: var(--muted); }
  .planrow { display: flex; gap: 8px; align-items: center; }
  .planrow input { flex: 1; padding: 7px 10px; font: inherit; font-size: 12.5px; border-radius: 8px;
    background: var(--elev); border: 1px solid var(--border-soft); color: var(--text); }
  .planrow input:focus { outline: none; border-color: var(--accent); }
  .err { margin: 0; font-size: 12px; color: var(--bad); }
</style>

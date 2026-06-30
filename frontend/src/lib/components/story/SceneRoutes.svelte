<script>
  // VN Plot lens ("Routes"): author the scene HARNESSES the runtime plays live — each scene's goal,
  // who's on stage (+ their goals/secrets), tone, and the divergence TRIGGERS out of it (a player
  // choice, or an emergent feature threshold). The triggers' branch targets are the routes; the AI
  // generates the dialogue. The detect→ask→act runtime lives at /scene/{id}/evaluate.
  // See [[multiformat-story-engine]].
  import { put } from '$lib/api.js';
  import { autosize } from '$lib/autosize.js';

  let { storyKey, scenes = [], features = [], startScene = '', cast = [], onChange = () => {} } = $props();

  // $state.snapshot → a plain DEEP copy (structuredClone chokes on Svelte's reactive proxies).
  let scs = $state($state.snapshot(scenes));
  let feats = $state($state.snapshot(features));
  let start = $state(startScene);
  let seededKey = null;
  $effect(() => {
    if (storyKey !== seededKey) {
      seededKey = storyKey;
      scs = $state.snapshot(scenes);
      feats = $state.snapshot(features);
      start = startScene;
    }
  });

  let openId = $state('');
  let saveT = null;
  function persist() {
    clearTimeout(saveT);
    saveT = setTimeout(() => put(`/stories/${storyKey}`, { scenes: scs, features: feats, start_scene: start }), 600);
    onChange();
  }
  const uid = (p) => p + '-' + Math.random().toString(36).slice(2, 7);

  function addScene() { const id = uid('sc'); scs = [...scs, { id, title: '', goal: '', setting: '', tone: '', on_stage: [], triggers: [] }]; if (!start) start = id; openId = id; persist(); }
  function removeScene(i) { const id = scs[i].id; scs = scs.filter((_, j) => j !== i); if (start === id) start = scs[0]?.id || ''; if (openId === id) openId = ''; persist(); }
  function addStage(s) { s.on_stage = [...(s.on_stage || []), { char: '', goal: '', secret: '' }]; scs = [...scs]; persist(); }
  function rmStage(s, j) { s.on_stage = s.on_stage.filter((_, k) => k !== j); scs = [...scs]; persist(); }
  function addTrig(s) { s.triggers = [...(s.triggers || []), { kind: 'emergent', condition: '', branch: '', intent: '' }]; scs = [...scs]; persist(); }
  function rmTrig(s, j) { s.triggers = s.triggers.filter((_, k) => k !== j); scs = [...scs]; persist(); }
  function addFeature() { feats = [...feats, { id: uid('f'), label: '', initial: 0 }]; persist(); }
  function rmFeature(i) { feats = feats.filter((_, j) => j !== i); persist(); }

  let sceneById = $derived(Object.fromEntries(scs.map((s) => [s.id, s.title || s.id])));
</script>

<div class="routes">
  <!-- Tracked features the emergent conditions test -->
  <div class="feats">
    <span class="flbl">Features</span>
    {#each feats as f, i (f.id)}
      <span class="feat">
        <input class="ip fid" bind:value={f.id} oninput={persist} placeholder="id" />
        <input class="ip flabel" bind:value={f.label} oninput={persist} placeholder="label" />
        <input class="ip fnum" type="number" bind:value={f.initial} oninput={persist} title="initial value" />
        <button class="x del" onclick={() => rmFeature(i)} title="Remove">×</button>
      </span>
    {/each}
    <button class="addf" onclick={addFeature}>＋ feature</button>
  </div>

  {#each scs as s, i (s.id)}
    <div class="scene" class:open={openId === s.id}>
      <div class="sc-head">
        <button class="startdot" class:on={start === s.id} onclick={() => { start = s.id; persist(); }} title={start === s.id ? 'Opening scene' : 'Make opening scene'}>▶</button>
        <input class="ip sc-title" bind:value={s.title} oninput={persist} placeholder="Scene title…" />
        <span class="branches">{(s.triggers || []).length} {(s.triggers || []).length === 1 ? 'branch' : 'branches'}</span>
        <button class="x" onclick={() => (openId = openId === s.id ? '' : s.id)}>{openId === s.id ? '▾' : '▸'}</button>
        <button class="x del" onclick={() => removeScene(i)} title="Remove scene">×</button>
      </div>

      {#if openId === s.id}
        <div class="sc-body">
          <input class="ip" bind:value={s.goal} oninput={persist} placeholder="Goal — what this scene must force" />
          <div class="row2">
            <input class="ip sm" bind:value={s.setting} oninput={persist} placeholder="Setting" />
            <input class="ip sm" bind:value={s.tone} oninput={persist} placeholder="Tone" />
          </div>

          <div class="sub">
            <div class="sublbl">On stage <button class="addmini" onclick={() => addStage(s)}>＋</button></div>
            {#each s.on_stage || [] as os, j (j)}
              <div class="stagerow">
                <select class="ip sm" bind:value={os.char} onchange={persist}>
                  <option value="">character…</option>
                  {#each cast as c}<option value={c.key}>{c.name || c.key}</option>{/each}
                </select>
                <input class="ip sm" bind:value={os.goal} oninput={persist} placeholder="goal in scene" />
                <input class="ip sm" bind:value={os.secret} oninput={persist} placeholder="secret" />
                <button class="x del" onclick={() => rmStage(s, j)}>×</button>
              </div>
            {/each}
          </div>

          <div class="sub">
            <div class="sublbl">Divergence triggers <button class="addmini" onclick={() => addTrig(s)}>＋</button></div>
            {#each s.triggers || [] as t, j (j)}
              <div class="trigrow">
                <select class="ip sm kind" bind:value={t.kind} onchange={persist}>
                  <option value="choice">choice</option>
                  <option value="emergent">emergent</option>
                </select>
                <input class="ip sm" bind:value={t.condition} oninput={persist}
                  placeholder={t.kind === 'choice' ? 'option text' : 'feature >= n'} />
                <span class="arrow">→</span>
                <select class="ip sm" bind:value={t.branch} onchange={persist}>
                  <option value="">branch…</option>
                  {#each scs as o}{#if o.id !== s.id}<option value={o.id}>{o.title || o.id}</option>{/if}{/each}
                </select>
                <input class="ip sm" bind:value={t.intent} oninput={persist} placeholder="intent" />
                <button class="x del" onclick={() => rmTrig(s, j)}>×</button>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {:else}
    <div class="empty">No scenes yet. Author the opening scene — or ask the Author to.</div>
  {/each}

  <button class="add" onclick={addScene}>＋ Add scene</button>
</div>

<style>
  .routes { display: flex; flex-direction: column; gap: 8px; }
  .feats { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 8px 10px; border: 1px solid var(--border);
           border-radius: var(--radius); background: var(--elev); }
  .flbl { font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); }
  .feat { display: inline-flex; align-items: center; gap: 4px; }
  .fid { width: 78px; } .flabel { width: 96px; } .fnum { width: 52px; }
  .addf, .addmini { font-size: 11.5px; padding: 3px 9px; }
  .scene { border: 1px solid var(--border); border-radius: var(--radius); background: var(--elev); }
  .scene.open { border-color: var(--border-strong); }
  .sc-head { display: flex; align-items: center; gap: 8px; padding: 8px 10px; }
  .startdot { flex: none; background: none; border: 0; color: var(--faint); font-size: 11px; padding: 2px 5px; border-radius: 6px; cursor: pointer; }
  .startdot.on { color: var(--good); }
  .sc-title { flex: 1; background: none; border: 0; padding: 2px 0; font-size: 14px; font-weight: 540; color: var(--text); }
  .sc-title:focus { outline: none; }
  .branches { font-size: 11px; color: var(--muted); }
  .x { background: none; border: 0; color: var(--muted); font-size: 14px; padding: 2px 6px; cursor: pointer; }
  .x:hover { color: var(--text); background: none; } .x.del:hover { color: var(--bad); }
  .sc-body { padding: 0 12px 12px; display: flex; flex-direction: column; gap: 9px; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; }
  .sub { display: flex; flex-direction: column; gap: 5px; }
  .sublbl { font-size: 11px; text-transform: uppercase; letter-spacing: .4px; color: var(--faint); display: flex; align-items: center; gap: 8px; }
  .stagerow { display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 5px; }
  .trigrow { display: grid; grid-template-columns: 84px 1fr auto 1fr 1fr auto; gap: 5px; align-items: center; }
  .arrow { color: var(--faint); font-size: 12px; }
  .ip { width: 100%; font: inherit; font-size: 13px; color: var(--text); background: var(--bg); border: 1px solid var(--border);
        border-radius: var(--radius); padding: 6px 9px; }
  .ip:focus { outline: none; border-color: var(--border-strong); }
  .ip.sm { font-size: 12px; padding: 5px 8px; }
  .empty { padding: 22px 4px; color: var(--faint); font-size: 13px; }
  .add { align-self: flex-start; font-size: 12.5px; }
</style>

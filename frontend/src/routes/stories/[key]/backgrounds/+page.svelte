<script>
  import { post } from '$lib/api.js';
  import ZoomImage from '$lib/components/ZoomImage.svelte';
  import Combobox from '$lib/components/Combobox.svelte';
  import { stories } from '$lib/stories.svelte.js';
  import { startJob, limitedPost } from '$lib/app.svelte.js';
  import { rget, rensure } from '$lib/renders.svelte.js';

  let st = $derived(stories.current);
  let bgModel = $state('scene');
  let bgModelItems = $derived(stories.imageModels.map((m) => ({ value: m.key, label: m.key })));
  $effect(() => { if (!stories.imageModels.some((m) => m.key === bgModel)) bgModel = stories.imageModels[0]?.key || 'scene'; });

  const N_BG = 5;
  let bgBust = $state(0);
  let bgCancel = {};            // locId -> {cancelled} (non-reactive)
  // Candidates live in the shared render store (keyed per story+location) so they
  // survive navigating away from this page and back.
  const bgKey = (locId) => `bg:${st.key}:${locId}`;

  async function genBgCands(locId) {
    const sk = st.key, k = bgKey(locId);
    const slot = rensure(k); slot.busy = true; slot.cands = []; slot.err = false;
    const tok = (bgCancel[locId] = { cancelled: false });
    const name = st.locations.find((l) => l.id === locId)?.name || locId;
    const job = startJob('Background render', `${st.name} · ${name}`, `stories/${sk}/backgrounds`, N_BG);
    job.onCancel = () => cancelBg(locId);   // stoppable from the Activity card
    for (let i = 0; i < N_BG; i++) {
      if (tok.cancelled) break;
      const r = await limitedPost(`/stories/${sk}/locations/${locId}/background/candidate`, { image_model: bgModel }, {}, job);
      if (tok.cancelled) break;
      if (r.data?.image) { rensure(k).cands = [...rensure(k).cands, r.data.image]; job.done = i + 1; }
      else { rensure(k).err = true; job.status = 'error'; break; }
    }
    if (job.status === 'running') job.status = tok.cancelled ? 'cancelled' : 'done';
    rensure(k).busy = false;
  }
  function cancelBg(locId) { if (bgCancel[locId]) bgCancel[locId].cancelled = true; rensure(bgKey(locId)).busy = false; }

  let promptBusy = $state({});   // locId -> bool (regenerating the bg prompt)
  async function regenPrompt(locId) {
    promptBusy = { ...promptBusy, [locId]: true };
    const r = await post(`/stories/${st.key}/locations/${locId}/regen-prompt`, {});
    promptBusy = { ...promptBusy, [locId]: false };
    if (r.data?.prompt) { const l = st.locations.find((x) => x.id === locId); if (l) l.background_prompt = r.data.prompt; }
  }
  async function pickBg(locId, dataUri) {
    const r = await post(`/stories/${st.key}/locations/${locId}/background/select`, { data: dataUri });
    if (r.data?.url) {
      const l = st.locations.find((x) => x.id === locId);
      if (l) l.background = r.data.url;
      bgBust++;
      rensure(bgKey(locId)).cands = [];
    }
  }
</script>

<div class="page"><div class="col">
  <h4>Locations <span class="lo">— start: {st.start}</span></h4>
  <div class="aesthrow">
    <label>Scene workflow <span class="lo">— 'scene' is a clean landscape graph; the look lives in the workflow (edit in Images → Graph)</span></label>
    <Combobox items={bgModelItems} bind:value={bgModel} placeholder="workflow…" />
  </div>
  <div class="scenes">
    {#each st.locations as l (l.id)}
      {@const rc = rget(bgKey(l.id))}
      <div class="vscene" class:start={l.id === st.start}>
        <div class="locmain">
          <div class="locinfo">
            <div class="vstop"><b>{l.name}</b> <code>{l.id}</code></div>
            {#if l.description}<div class="vcast">{l.description}</div>{/if}
            {#if l.background_prompt}<p class="vopen"><span class="bglbl">bg:</span> {l.background_prompt}</p>{/if}
            <div class="bgbtns">
              <button class="ghost xs" onclick={() => regenPrompt(l.id)} disabled={promptBusy[l.id]} title="regenerate this location's background prompt">{promptBusy[l.id] ? '…' : '✨ regenerate prompt'}</button>
              <button class="ghost xs" onclick={() => genBgCands(l.id)} disabled={rc.busy}>
                {rc.busy ? `Rendering ${rc.cands.length}/${N_BG}…` : (l.background ? `↻ New options` : `🖼 Generate ${N_BG} options`)}
              </button>
              {#if rc.busy}<button class="ghost xs" onclick={() => cancelBg(l.id)}>Stop</button>{/if}
            </div>
          </div>
          {#if l.background}
            <div class="bgthumb"><ZoomImage src={`${l.background}?b=${bgBust}`} caption={l.name} inline /></div>
          {/if}
        </div>
        {#if rc.cands.length}
          <div class="bgcands">
            {#each rc.cands as img, i (i)}
              <div class="bgcand">
                <ZoomImage src={img} caption={`${l.name} option ${i + 1}`} inline />
                <button class="use" onclick={() => pickBg(l.id, img)}>Use this</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    {/each}
  </div>
</div></div>

<style>
  h4 { margin: 4px 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .aesthrow { margin: 0 0 12px; }
  .aesthrow label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 5px; text-transform: uppercase; letter-spacing: .3px; }
  .scenes { display: flex; flex-direction: column; gap: 8px; }
  .vscene { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 10px 12px; }
  .vscene.start { border-color: var(--accent); }
  .locmain { display: flex; gap: 12px; align-items: flex-start; }
  .locinfo { flex: 1; min-width: 0; }
  .bgthumb { width: 160px; flex: none; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .xs { font-size: 11.5px; padding: 3px 9px; border-radius: 7px; }
  .bgbtns { display: flex; gap: 6px; margin-top: 6px; }
  .bgcands { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px; margin-top: 10px; }
  .bgcand { display: flex; flex-direction: column; gap: 4px; }
  .bgcand .use { font-size: 11px; padding: 4px 6px; border-radius: 6px; box-shadow: none; background: var(--elev-2); border: 1px solid var(--border); color: var(--text); }
  .bgcand .use:hover { border-color: var(--accent); color: var(--accent); filter: none; }
  .vstop { display: flex; align-items: center; gap: 8px; font-size: 13.5px; }
  .vstop code { font-size: 11px; color: var(--faint); font-family: ui-monospace, monospace; background: var(--bg); padding: 1px 6px; border-radius: 5px; }
  .vcast { font-size: 12px; color: var(--accent); margin-top: 3px; }
  .vopen { font-size: 12.5px; color: var(--muted); margin: 6px 0; white-space: pre-wrap; }
  .bglbl { color: var(--faint); font-family: ui-monospace, monospace; }
</style>

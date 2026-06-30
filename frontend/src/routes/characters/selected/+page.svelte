<script>
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { app } from '$lib/app.svelte.js';
  import { chars, loadChars } from '$lib/characters.svelte.js';
  import ZoomImage from '$lib/components/shared/ZoomImage.svelte';
  import Portraits from '$lib/components/image/Portraits.svelte';
  import Combobox from '$lib/components/shared/Combobox.svelte';

  let active = $derived(chars.list.find((c) => c.key === app.activeChar) || null);

  // view switcher: edit the card, or work on portraits/assets
  let view = $state('card'); // 'card' | 'portraits'

  // ---------- Card editing ----------
  let edit = $state({ name: '', system: '', greeting: '', appearance: '', playable: false });
  let cardMsg = $state(null);
  let loadedFor = $state(null);
  let cardSnap = $state(null);   // signature of the last loaded/saved card (auto-save baseline)
  let cardTimer = null;

  $effect(() => {
    if (active && active.key !== loadedFor) {
      loadedFor = active.key;
      edit = {
        name: active.name || '',
        system: active.system || '',
        greeting: active.greeting || '',
        appearance: active.fields?.appearance || '',
        playable: !!active.playable,
        homeScenes: (active.home_scenes || []).map((s) => ({ ...s }))
      };
      cardMsg = null;
      refMsg = null;
      cardSnap = JSON.stringify(edit);
    }
  });

  async function saveCard() {
    cardMsg = { text: 'Saving…' };
    const r = await post(`/characters/${active.key}/card`, {
      name: edit.name,
      system: edit.system,
      greeting: edit.greeting,
      playable: edit.playable,
      home_scenes: edit.homeScenes,
      fields: { appearance: edit.appearance }
    });
    if (r.data?.ok) { cardMsg = { ok: true, text: '✓ Saved' }; await loadChars(); }
    else cardMsg = { err: true, text: r.data?.error || 'save failed' };
    cardSnap = JSON.stringify(edit);
  }

  // Auto-save card edits (debounced).
  $effect(() => {
    const cur = JSON.stringify(edit);
    if (!active || cardSnap === null || cur === cardSnap) return;
    clearTimeout(cardTimer);
    cardTimer = setTimeout(saveCard, 700);
  });

  // Home scenes (playable personas only) — optional portable "yours" places.
  const newHomeId = () => `home_${Date.now().toString(36)}`;
  function addHome() { edit.homeScenes = [...edit.homeScenes, { id: newHomeId(), name: '', backstory: '', background_prompt: '' }]; }
  function removeHome(i) { edit.homeScenes = edit.homeScenes.filter((_, j) => j !== i); }

  // Generate a thorough, labelled background (Identity/History/Personality/…)
  // for this character — uses its attached story as context when present.
  let expanding = $state(false);
  async function expandBackground() {
    expanding = true; cardMsg = { text: 'Writing a thorough background…' };
    const r = await post(`/characters/${active.key}/expand-background`, {});
    expanding = false;
    if (r.data?.ok) { edit.system = r.data.system; cardMsg = { ok: true, text: '✓ background expanded & saved' }; await loadChars(); }
    else cardMsg = { err: true, text: r.data?.error || 'expand failed' };
  }

  // ---------- Reference / base image ----------
  let refMsg = $state(null);
  let refBust = $state(0);

  async function uploadRef(e) {
    const f = e.target.files?.[0];
    if (!f || !active) return;
    refMsg = { text: 'Uploading…' };
    const fd = new FormData(); fd.append('file', f);
    const res = await fetch(`/api/characters/${active.key}/reference`, { method: 'POST', body: fd });
    refMsg = res.ok ? { ok: true, text: '✓ base image set' } : { err: true, text: 'upload failed' };
    e.target.value = '';
    if (res.ok) { refBust++; await loadChars(); }
  }
  async function clearRef() {
    if (!active) return;
    await fetch(`/api/characters/${active.key}/reference`, { method: 'DELETE' });
    refBust++; await loadChars(); refMsg = { ok: true, text: '✓ using card art' };
  }
  async function useAsBase(url) {
    if (!active) return;
    refMsg = { text: 'Fetching…' };
    const r = await post(`/characters/${active.key}/reference/from-url`, { url });
    refMsg = r.data?.ok ? { ok: true, text: '✓ base image set' } : { err: true, text: r.data?.error || 'failed' };
    if (r.data?.ok) { refBust++; await loadChars(); }
  }

  // ---------- Workflow picker (which image model the studio renders with) ----------
  let imageModels = $state([]);
  let workflow = $state('');
  let workflowItems = $derived([
    { value: '', label: '— use active image model —' },
    ...imageModels.map((m) => ({ value: m.key, label: m.key }))
  ]);
  onMount(async () => {
    try { imageModels = (await get('/models')).image || []; } catch { /* offline */ }
  });
</script>

{#if active}
  <div class="detail">
    <div class="dtop">
      {#if active.avatar}
        <img class="dav" src={active.avatar} alt={active.name} />
      {:else}
        <div class="dav ph">{(active.name?.[0] || '?').toUpperCase()}</div>
      {/if}
      <div class="dhead">
        <h3>{active.name || active.key}<span class="active-note">✓ Active in chat</span></h3>
        {#if active.fields?.tags?.length}
          <div class="tags">{#each active.fields.tags as t}<span class="tag">{t}</span>{/each}</div>
        {/if}
        {#if active.fields?.creator || active.fields?.character_version}
          <div class="byline">
            {#if active.fields?.creator}by {active.fields.creator}{/if}
            {#if active.fields?.character_version}<span>v{active.fields.character_version}</span>{/if}
          </div>
        {/if}
        <div class="dactions">
          <a class="switch" href="/characters/search">Switch character</a>
        </div>
      </div>
    </div>

    <!-- view switcher -->
    <div class="vtabs">
      <button class:on={view === 'card'} onclick={() => (view = 'card')}>Card</button>
      <button class:on={view === 'portraits'} onclick={() => (view = 'portraits')}>Portraits & assets</button>
    </div>

    {#if view === 'card'}
      <div class="dbody">
        <section class="edit">
          <label class="play-toggle" class:on={edit.playable}>
            <input type="checkbox" bind:checked={edit.playable} />
            <span class="pt-mark">🎭</span>
            <span class="pt-text">
              <strong>Playable — embody this character</strong>
              <span class="pt-sub">Makes this a “you” puppet you can play in any story. Their backstory & lorebook flow into the scene; you drive their choices. Portable across stories.</span>
            </span>
          </label>

          <label>Name</label>
          <input class="fld" bind:value={edit.name} />

          <div class="lblrow">
            <label>Persona / system prompt</label>
            <button class="ghost xsm" onclick={expandBackground} disabled={expanding}>{expanding ? 'Writing…' : '✨ Expand background'}</button>
          </div>
          <textarea class="fld ta" rows="10" bind:value={edit.system}></textarea>

          <label>Appearance <span class="lo">— booru-ish tags used to seed image prompts</span></label>
          <textarea class="fld ta" rows="3" bind:value={edit.appearance}
            placeholder="e.g. 1girl, long pink hair, blue eyes, nurse outfit"></textarea>

          <label>First message / greeting</label>
          <textarea class="fld ta" rows="4" bind:value={edit.greeting}></textarea>

          {#if edit.playable}
            <div class="lblrow">
              <label>🏠 Home scenes <span class="lo">— optional; places that are “yours”, carried into any story</span></label>
              <button class="ghost xsm" onclick={addHome}>+ Add home</button>
            </div>
            {#each edit.homeScenes as h, i (h.id)}
              <div class="home">
                <div class="home-top">
                  <input class="fld" bind:value={edit.homeScenes[i].name} placeholder="Home name (my apartment, the studio, parents’ house…)" />
                  <button class="rmhome" onclick={() => removeHome(i)} title="Remove home">✕</button>
                </div>
                <textarea class="fld ta" rows="2" bind:value={edit.homeScenes[i].backstory} placeholder="What this place is to you — the vibe, what’s here…"></textarea>
                <input class="fld" bind:value={edit.homeScenes[i].background_prompt} placeholder="Background plate — booru tags, no people (optional)" />
              </div>
            {/each}
            {#if !edit.homeScenes.length}
              <p class="lo nohome">No home scenes — you’ll use whatever “home” the story provides.</p>
            {/if}
          {/if}

          <div class="prow">
            <span class:ok={cardMsg?.ok} class:err={cardMsg?.err} class="pm">{cardMsg?.text || 'Auto-saves as you edit'}</span>
          </div>
        </section>

        {#if active.fields?.creator_notes}
          <section><h4>Creator notes</h4><pre>{active.fields.creator_notes}</pre></section>
        {/if}
        {#if active.fields?.mes_example}
          <section><h4>Example dialogue</h4><pre>{active.fields.mes_example}</pre></section>
        {/if}
        {#if active.fields?.alternate_greetings?.length}
          <section>
            <h4>Alternate greetings ({active.fields.alternate_greetings.length})</h4>
            {#each active.fields.alternate_greetings as g}<pre>{g}</pre>{/each}
          </section>
        {/if}
        {#if active.fields?.character_book?.entries?.length}
          <section>
            <h4>Lorebook ({active.fields.character_book.entries.length} entries)</h4>
            {#each active.fields.character_book.entries as e}
              <div class="lentry">
                <div class="lkeys">{(e.keys || e.key || []).join(', ') || e.comment || 'entry'}</div>
                <pre>{e.content || ''}</pre>
              </div>
            {/each}
          </section>
        {/if}
      </div>

    {:else}
      <div class="dbody">
        <!-- Base image + available card images -->
        <section class="assets">
          <h4>Base image <span class="lo">— source for the portrait studio (identity reference)</span></h4>
          <div class="refrow">
            <div class="refimg">
              {#if active.reference}
                <ZoomImage src={`${active.reference}?b=${refBust}`} caption={active.name} inline />
              {:else}
                <div class="noref">no image</div>
              {/if}
            </div>
            <div class="refctl">
              <label class="upbtn">Replace…<input type="file" accept="image/*" onchange={uploadRef} hidden /></label>
              <button class="ghost sm" onclick={clearRef}>Use card art</button>
              {#if refMsg}<span class:ok={refMsg.ok} class:err={refMsg.err} class="pm">{refMsg.text}</span>{/if}
            </div>
          </div>

          {#if active.images?.length}
            <label>Available images <span class="lo">— click “use as base” to pick the identity source</span></label>
            <div class="imggrid">
              {#each active.images as im (im.kind + im.url)}
                <div class="imgcard">
                  <div class="imgthumb">
                    <ZoomImage src={im.url.startsWith('/api') ? `${im.url}?b=${refBust}` : im.url} caption={im.kind} inline />
                  </div>
                  <div class="imgmeta">
                    <span class="ikind ik-{im.kind.replace(' ', '-')}">{im.kind}</span>
                    <span class="idot" class:ok={im.ok === true} class:ext={im.ok === null} class:bad={im.ok === false}>
                      {im.ok === true ? 'present' : im.ok === null ? 'external' : 'missing'}
                    </span>
                  </div>
                  {#if im.ok !== false}
                    <button class="usebtn" onclick={() => useAsBase(im.url.startsWith('/api') ? location.origin + im.url : im.url)}>Use as base</button>
                  {/if}
                </div>
              {/each}
            </div>
          {/if}
        </section>

        <!-- Studio workflow + portrait studio -->
        <section class="wfpick">
          <h4>Render workflow</h4>
          <p class="phint">Which image workflow the studio renders outfits & expressions with. Pick your img2img / IPAdapter workflow for the strongest identity.</p>
          <Combobox items={workflowItems} bind:value={workflow} placeholder="— use active image model —" />
        </section>

        <Portraits charKey={active.key} charName={active.name || active.key} imageModel={workflow} />
      </div>
    {/if}
  </div>
{:else}
  <div class="placeholder">
    <div class="emk">∅</div>
    <p>No character selected.</p>
    <span>Chat runs with a neutral persona.</span>
    <a class="browse" href="/characters/search">Browse characters</a>
  </div>
{/if}

<style>
  .detail { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 16px; overflow: hidden; }
  .dtop { display: flex; gap: 18px; padding: 22px; border-bottom: 1px solid var(--border-soft); }
  .dav { width: 120px; height: 120px; flex: none; border-radius: 14px; object-fit: cover; border: 1px solid var(--border); }
  .dav.ph {
    display: grid; place-items: center; font-size: 48px; font-weight: 700; color: #fff;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
  }
  .dhead { min-width: 0; }
  .dhead h3 { margin: 2px 0 6px; font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .active-note { color: var(--good); font-weight: 600; font-size: 13px; }
  .tags { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 8px; }
  .tag {
    font-size: 11.5px; color: var(--muted); background: var(--elev); border: 1px solid var(--border-soft);
    border-radius: 999px; padding: 2px 9px;
  }
  .byline { font-size: 12px; color: var(--faint); display: flex; gap: 10px; margin-bottom: 12px; }
  .dactions { margin-top: 2px; }
  .switch {
    display: inline-block; text-decoration: none; font-size: 12.5px; font-weight: 500; color: #d4d8e0;
    background: var(--elev); border: 1px solid var(--border); border-radius: 8px; padding: 5px 10px;
  }
  .switch:hover { background: var(--elev-2); }

  .vtabs { display: flex; gap: 4px; padding: 10px 22px 0; border-bottom: 1px solid var(--border-soft); }
  .vtabs button {
    background: none; border: none; color: var(--muted); font-size: 13.5px; font-weight: 600;
    padding: 9px 14px; border-radius: 9px 9px 0 0; border-bottom: 2px solid transparent;
  }
  .vtabs button:hover { color: var(--text); background: var(--elev); }
  .vtabs button.on { color: var(--text); border-bottom-color: var(--accent); }

  .dbody { padding: 16px 22px 22px; }
  .dbody section { margin-top: 16px; }
  .dbody section:first-child { margin-top: 4px; }
  .dbody h4 { margin: 0 0 7px; font-size: 11.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .dbody pre {
    margin: 0 0 8px; white-space: pre-wrap; word-break: break-word; font: inherit; font-size: 13.5px;
    color: var(--text); background: var(--elev); border: 1px solid var(--border-soft); border-radius: 10px; padding: 11px 13px;
  }
  .lentry { margin-bottom: 10px; }
  .lkeys { font-size: 12px; font-weight: 600; color: var(--accent); margin-bottom: 4px; font-family: ui-monospace, monospace; }
  .lentry pre { margin: 0; }

  /* card editor */
  .edit label { display: block; font-size: 11.5px; color: var(--muted); margin: 14px 0 5px; text-transform: uppercase; letter-spacing: .3px; }
  .edit label.play-toggle {
    display: flex; align-items: flex-start; gap: 11px; margin: 0 0 6px; padding: 12px 14px;
    text-transform: none; letter-spacing: 0; cursor: pointer; border-radius: 11px;
    background: var(--elev); border: 1px solid var(--border-soft); transition: border-color .12s, background .12s;
  }
  .play-toggle:hover { border-color: var(--border); }
  .play-toggle.on { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: color-mix(in srgb, var(--accent) 9%, var(--elev)); }
  .play-toggle input { width: 16px; height: 16px; margin-top: 2px; flex: none; accent-color: var(--accent); }
  .pt-mark { font-size: 18px; line-height: 1.2; flex: none; }
  .pt-text { display: flex; flex-direction: column; gap: 3px; }
  .pt-text strong { font-size: 13px; color: var(--text); font-weight: 650; }
  .pt-sub { font-size: 11.5px; color: var(--muted); line-height: 1.45; }
  .lblrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
  .lblrow label { margin-bottom: 0; }
  .xsm { font-size: 11.5px; padding: 4px 10px; border-radius: 7px; }
  .edit label:first-child { margin-top: 0; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .fld {
    width: 100%; padding: 9px 11px; font-size: 13.5px; border-radius: 9px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
  }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }
  .home { border: 1px solid var(--border-soft); border-radius: 10px; padding: 9px 10px; margin-bottom: 8px; background: var(--elev); display: flex; flex-direction: column; gap: 6px; }
  .home-top { display: flex; gap: 7px; align-items: center; }
  .home-top .fld { flex: 1; }
  .rmhome { width: 30px; height: 30px; flex: none; padding: 0; display: grid; place-items: center; font-size: 12px; border-radius: 8px; background: var(--elev-2); border: 1px solid var(--border-soft); color: var(--muted); }
  .rmhome:hover { color: var(--bad); border-color: rgba(255,122,122,.5); }
  .nohome { margin: 0 0 8px; }
  .prow { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
  .pm { font-size: 12.5px; }
  .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }

  /* assets */
  .assets label { display: block; font-size: 11.5px; color: var(--muted); margin: 16px 0 8px; text-transform: uppercase; letter-spacing: .3px; }
  .refrow { display: flex; gap: 14px; align-items: flex-start; }
  .refimg { width: 140px; flex: none; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--panel); }
  .noref { aspect-ratio: 1; display: grid; place-items: center; font-size: 12px; color: var(--faint); }
  .refctl { display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
  .upbtn {
    display: inline-block; cursor: pointer; font-size: 12.5px; font-weight: 600; padding: 6px 12px;
    border-radius: 8px; border: 1px solid var(--border); background: var(--accent); color: #fff;
  }
  .upbtn:hover { filter: brightness(1.08); }

  .imggrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 10px; }
  .imgcard { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 10px; padding: 8px; display: flex; flex-direction: column; gap: 6px; }
  .imgthumb { border-radius: 8px; overflow: hidden; border: 1px solid var(--border); background: var(--bg); }
  .imgmeta { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 11px; }
  .ikind { font-weight: 650; text-transform: capitalize; }
  .ik-avatar { color: #6d8cff; } .ik-reference { color: var(--accent); } .ik-card-link { color: var(--muted); }
  .idot.ok { color: var(--good); } .idot.ext { color: #ffd479; } .idot.bad { color: var(--bad); }
  .usebtn {
    font-size: 11.5px; font-weight: 600; padding: 5px 8px; border-radius: 7px;
    background: var(--elev-2); border: 1px solid var(--border); color: var(--text);
  }
  .usebtn:hover { border-color: var(--accent); color: var(--accent); }

  .wfpick .phint { margin: 0 0 8px; font-size: 12px; color: var(--muted); }

  .placeholder { margin: 60px auto; text-align: center; color: var(--muted); }
  .placeholder .emk {
    width: 48px; height: 48px; margin: 0 auto 14px; border-radius: 13px; display: grid; place-items: center;
    font-size: 22px; color: var(--faint); background: var(--elev-2); border: 1px solid var(--border);
  }
  .placeholder p { margin: 0 0 4px; color: var(--text); font-size: 16px; }
  .browse {
    display: inline-block; margin-top: 14px; text-decoration: none; color: #fff; font-weight: 540;
    background: linear-gradient(180deg, var(--accent), var(--accent-600)); border-radius: var(--radius); padding: 8px 14px;
  }
</style>

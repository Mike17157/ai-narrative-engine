<script>
  // Portrait studio — the character art bench, living INSIDE a story now (the old
  // Characters pane was retired). Pick a cast member, set their identity base image,
  // choose the render workflow, then build outfits & emotion expressions. Renders go
  // through the global /characters/{key}/portraits endpoints (story-scoped variants
  // only exist in the lean build).
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api.js';
  import { chars, loadChars, charName } from '$lib/characters.svelte.js';
  import { stories } from '$lib/stories.svelte.js';
  import ZoomImage from '$lib/components/shared/ZoomImage.svelte';
  import Portraits from '$lib/components/image/Portraits.svelte';
  import Combobox from '$lib/components/shared/Combobox.svelte';

  let storyKey = $derived(stories.current?.key || '');
  let cast = $derived(chars.list || []);

  // Which cast member is on the bench — defaults to the first, persists per story.
  const LS = () => `loom.studio.char.${storyKey}`;
  let selKey = $state('');
  $effect(() => {
    if (!cast.length) { selKey = ''; return; }
    if (selKey && cast.some((c) => c.key === selKey)) return;
    let remembered = '';
    try { remembered = localStorage.getItem(LS()) || ''; } catch { /* ignore */ }
    selKey = cast.some((c) => c.key === remembered) ? remembered : cast[0].key;
  });
  function pick(key) {
    selKey = key;
    try { localStorage.setItem(LS(), key); } catch { /* ignore */ }
  }
  let active = $derived(cast.find((c) => c.key === selKey) || null);

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
    if (res.ok) { refBust++; await loadChars(storyKey); }
  }
  async function clearRef() {
    if (!active) return;
    await fetch(`/api/characters/${active.key}/reference`, { method: 'DELETE' });
    refBust++; await loadChars(storyKey); refMsg = { ok: true, text: '✓ using card art' };
  }
  async function useAsBase(url) {
    if (!active) return;
    refMsg = { text: 'Fetching…' };
    const r = await post(`/characters/${active.key}/reference/from-url`, { url });
    refMsg = r.data?.ok ? { ok: true, text: '✓ base image set' } : { err: true, text: r.data?.error || 'failed' };
    if (r.data?.ok) { refBust++; await loadChars(storyKey); }
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

<div class="page">
  <div class="page-head">
    <div>
      <h2 class="page-title">Portrait studio</h2>
      <p class="page-sub">Identity base image, outfits &amp; emotion expressions for each member of the cast.</p>
    </div>
  </div>

  {#if cast.length}
    <!-- cast picker -->
    <div class="castrow">
      {#each cast as c (c.key)}
        <button class="cchip" class:on={c.key === selKey} onclick={() => pick(c.key)} title={c.name || c.key}>
          {#if c.reference || c.avatar}
            <img src={c.reference || c.avatar} alt={c.name} />
          {:else}
            <span class="cph">{(c.name?.[0] || '?').toUpperCase()}</span>
          {/if}
          <span class="cname">{c.name || c.key}</span>
        </button>
      {/each}
    </div>

    {#if active}
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
        <p class="phint">Which image workflow the studio renders outfits &amp; expressions with. Pick your img2img / IPAdapter workflow for the strongest identity.</p>
        <Combobox items={workflowItems} bind:value={workflow} placeholder="— use active image model —" />
      </section>

      <Portraits charKey={active.key} charName={charName(active.key)} imageModel={workflow} />
    {/if}
  {:else}
    <div class="empty">
      <div class="emk">🎨</div>
      <p>No cast yet.</p>
      <span>Add characters on the <a href={`/stories/${storyKey}/characters`}>Character cards</a> page, then build their portraits here.</span>
    </div>
  {/if}
</div>

<style>
  .page { padding: 24px 28px; display: flex; flex-direction: column; gap: 18px; max-width: 980px; }
  .page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  .page-title { margin: 0 0 4px; font-size: 18px; font-weight: 700; }
  .page-sub { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; max-width: 560px; }

  /* cast picker */
  .castrow { display: flex; flex-wrap: wrap; gap: 8px; }
  .cchip {
    display: flex; align-items: center; gap: 8px; padding: 5px 12px 5px 5px; cursor: pointer;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 999px;
    color: var(--muted); font-size: 12.5px; font-weight: 600; transition: border-color .12s, color .12s;
  }
  .cchip:hover { color: var(--text); border-color: var(--border); }
  .cchip.on { color: var(--text); border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  .cchip img, .cph { width: 28px; height: 28px; border-radius: 50%; object-fit: cover; flex: none; }
  .cph { display: grid; place-items: center; font-size: 13px; font-weight: 700; color: #fff;
         background: linear-gradient(135deg, var(--accent), #9a6dff); }

  section { margin-top: 4px; }
  h4 { margin: 0 0 7px; font-size: 11.5px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }

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
  .pm { font-size: 12.5px; }
  .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }

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

  .empty { margin: 50px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; align-items: center; gap: 8px; max-width: 360px; }
  .emk { width: 52px; height: 52px; display: grid; place-items: center; font-size: 26px; border-radius: 14px; background: var(--elev-2); border: 1px solid var(--border); margin-bottom: 4px; }
  .empty p { margin: 0; color: var(--text); font-size: 15px; }
  .empty span { font-size: 12.5px; line-height: 1.5; }
  .empty a { color: var(--accent); }
</style>

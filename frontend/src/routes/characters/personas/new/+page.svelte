<script>
  // Guided persona wizard — builds a PLAYABLE character card (the portable "you" puppet)
  // in a few lean steps: You → Backstory & look → Done. The card is created up front so
  // every later step edits a real character (reusing /card + /expand-background). Home
  // scene is a forward-reference — it attaches once the story scene/place model lands.
  import { goto } from '$app/navigation';
  import { post } from '$lib/api.js';
  import { setActivePlayerChar } from '$lib/app.svelte.js';
  import { loadChars } from '$lib/characters.svelte.js';

  const STEPS = ['You', 'Backstory & look', 'Done'];
  let step = $state(0);
  let key = $state(null);          // the created character key (null until step 0 commits)

  let name = $state('');
  let seed = $state('');           // a sentence or two — who you are, your life, your vibe
  let system = $state('');         // the full backstory (seed, then expanded)
  let appearance = $state('');
  let homeNote = $state('');       // forward-ref: a free note about "home" until scenes exist

  let busy = $state(false);
  let err = $state(null);
  let saveMsg = $state(null);
  let expanding = $state(false);

  // Step 0 → create the playable card (once), seed its persona, advance.
  async function commitYou() {
    if (!name.trim()) { err = 'Give your persona a name.'; return; }
    err = null;
    if (!key) {
      busy = true;
      const r = await post('/characters/create', { name: name.trim(), system: seed.trim(), playable: true });
      busy = false;
      if (!r.ok || r.data?.error) { err = r.data?.error || 'could not create'; return; }
      key = r.data.key;
      system = seed.trim();
    }
    step = 1;
  }

  async function save() {
    if (!key) return;
    saveMsg = { text: 'Saving…' };
    const r = await post(`/characters/${key}/card`, {
      name: name.trim(), system, playable: true,
      fields: { appearance, home_note: homeNote }
    });
    saveMsg = r.data?.ok ? { ok: true, text: '✓ Saved' } : { err: true, text: r.data?.error || 'save failed' };
  }

  // Debounced auto-save while editing steps 1–2.
  let snap = $state('');
  let timer = null;
  $effect(() => {
    const cur = JSON.stringify({ name, system, appearance, homeNote });
    if (!key || step === 0) { snap = cur; return; }
    if (cur === snap) return;
    clearTimeout(timer);
    timer = setTimeout(() => { snap = cur; save(); }, 700);
  });

  async function expand() {
    if (!key) return;
    expanding = true; saveMsg = { text: 'Writing a thorough backstory…' };
    await save();                                   // expand-background reads the saved seed
    const r = await post(`/characters/${key}/expand-background`, {});
    expanding = false;
    if (r.data?.ok) { system = r.data.system; snap = JSON.stringify({ name, system, appearance, homeNote }); saveMsg = { ok: true, text: '✓ backstory written & saved' }; }
    else saveMsg = { err: true, text: r.data?.error || 'expand failed' };
  }

  async function finish() {
    await save();
    if (key) setActivePlayerChar(key);
    await loadChars();
    goto('/characters/personas');
  }
  function cancel() { goto('/characters/personas'); }
</script>

<div class="wiz">
  <header class="whead">
    <div class="wtitle">
      <h2>New persona</h2>
      <p>Build a “you” you can drop into any story — your backstory rides along, and you puppet their choices.</p>
    </div>
    <button class="ghost sm" onclick={cancel}>Cancel</button>
  </header>

  <ol class="steps">
    {#each STEPS as s, i (s)}
      <li class:on={i === step} class:done={i < step}>
        <span class="dot">{i < step ? '✓' : i + 1}</span>{s}
      </li>
    {/each}
  </ol>

  <div class="panel">
    {#if step === 0}
      <label>Name</label>
      <input class="fld" bind:value={name} placeholder="What should this persona be called?" />

      <label>Who are you? <span class="lo">— a sentence or two; reflect your own life if you like</span></label>
      <textarea class="fld ta" rows="5" bind:value={seed}
        placeholder="e.g. A 29-year-old ceramicist who moved to the coast after a breakup; dry humor, early riser, terrible at saying no."></textarea>

      {#if err}<p class="err">{err}</p>{/if}
      <div class="nav">
        <span></span>
        <button onclick={commitYou} disabled={busy || !name.trim()}>{busy ? 'Creating…' : 'Next →'}</button>
      </div>

    {:else if step === 1}
      <div class="lblrow">
        <label>Backstory <span class="lo">— who you are, in depth</span></label>
        <button class="ghost xsm" onclick={expand} disabled={expanding}>{expanding ? 'Writing…' : '✨ Expand into a full backstory'}</button>
      </div>
      <textarea class="fld ta" rows="12" bind:value={system}
        placeholder="Your seed is here — flesh it out, or hit ✨ to expand it into Identity / History / Personality / Relationships / Voice."></textarea>

      <label>Appearance <span class="lo">— booru-ish tags, used later to render your portrait</span></label>
      <textarea class="fld ta" rows="3" bind:value={appearance}
        placeholder="e.g. 1girl, brown hair, freckles, paint-stained apron, tired eyes"></textarea>

      <div class="nav">
        <button class="ghost" onclick={() => (step = 0)}>← Back</button>
        <span class:ok={saveMsg?.ok} class:err={saveMsg?.err} class="pm">{saveMsg?.text || 'Auto-saves as you edit'}</span>
        <button onclick={() => (step = 2)}>Next →</button>
      </div>

    {:else}
      <div class="done">
        <h3>“{name}” is ready</h3>
        <p>Saved as a playable card. You can pick it as your character in any story’s <b>Playing as</b> menu, and its backstory flows into the scene.</p>

        <label>Home <span class="lo">— a place that’s “yours” (kitchen, studio, apartment…)</span></label>
        <input class="fld" bind:value={homeNote} placeholder="Describe your home spot — it’ll become a real, swappable home scene when story scenes land." />
        <p class="hint">🏠 Home <b>scenes</b> are coming with the story scene/place model — for now this is just a note saved on your card.</p>

        {#if key}
          <a class="link" href={`/characters/selected`} onclick={() => { try { localStorage.setItem('loom.activeChar', key); } catch {} }}>
            Add a portrait & outfits in the character editor →
          </a>
        {/if}

        <div class="nav">
          <button class="ghost" onclick={() => (step = 1)}>← Back</button>
          <button onclick={finish}>Finish & make active</button>
        </div>
      </div>
    {/if}
  </div>
</div>

<style>
  .wiz { max-width: 720px; margin: 0 auto; padding: 24px 28px; }
  .whead { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
  .wtitle h2 { margin: 0 0 4px; font-size: 19px; font-weight: 700; }
  .wtitle p { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.5; }

  .steps { display: flex; gap: 8px; list-style: none; padding: 0; margin: 0 0 18px; }
  .steps li {
    display: flex; align-items: center; gap: 7px; flex: 1; font-size: 12.5px; color: var(--muted);
    padding: 9px 12px; border: 1px solid var(--border-soft); border-radius: 10px; background: var(--elev);
  }
  .steps li.on { color: var(--text); border-color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, var(--elev)); }
  .steps li.done { color: var(--text); }
  .steps .dot {
    width: 20px; height: 20px; flex: none; display: grid; place-items: center; border-radius: 50%;
    font-size: 11px; font-weight: 700; background: var(--elev-2); border: 1px solid var(--border); color: var(--muted);
  }
  .steps li.on .dot { background: var(--accent); color: #fff; border-color: var(--accent); }
  .steps li.done .dot { background: var(--good); color: #fff; border-color: var(--good); }

  .panel { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 16px; padding: 20px 22px; }
  label { display: block; font-size: 11.5px; color: var(--muted); margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .3px; }
  label:first-child { margin-top: 0; }
  .lblrow { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 0; }
  .lblrow label { margin: 0; }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .xsm { font-size: 11.5px; padding: 4px 10px; border-radius: 7px; }
  .fld {
    width: 100%; padding: 9px 11px; font-size: 13.5px; border-radius: 9px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
  }
  .ta { resize: vertical; line-height: 1.5; font-family: inherit; }
  .fld:focus { border-color: var(--accent); box-shadow: 0 0 0 2px var(--accent-glow); outline: none; }

  .nav { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 20px; }
  .pm { font-size: 12.5px; color: var(--muted); }
  .pm.ok { color: var(--good); } .pm.err { color: var(--bad); }
  .err { font-size: 12.5px; margin: 10px 0 0; }

  .done h3 { margin: 0 0 6px; font-size: 16px; font-weight: 700; }
  .done > p { margin: 0 0 4px; font-size: 13px; color: var(--muted); line-height: 1.5; }
  .hint { font-size: 12px; color: var(--faint); margin: 6px 0 0; line-height: 1.5; }
  .link { display: inline-block; margin-top: 16px; font-size: 13px; color: var(--accent); text-decoration: none; }
  .link:hover { text-decoration: underline; }
</style>

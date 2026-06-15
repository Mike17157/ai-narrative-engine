<script>
  import { app, savePersonas, setActivePersona } from '$lib/app.svelte.js';

  let personaPicFor = $state(null);
  let personaPicInput = $state();

  function newPersona() {
    const id = (typeof crypto !== 'undefined' && crypto.randomUUID) ? crypto.randomUUID() : `p${Date.now()}`;
    app.personas = [...app.personas, { id, name: 'New persona', description: '' }];
    setActivePersona(id);
    savePersonas();
  }
  function removePersona(id) {
    app.personas = app.personas.filter((p) => p.id !== id);
    if (!app.personas.length) app.personas = [{ id: 'you', name: 'You', description: '' }];
    if (app.activePersona === id) setActivePersona(app.personas[0].id);
    savePersonas();
  }
  function choosePersonaPic(id) { personaPicFor = id; personaPicInput?.click(); }
  async function onPersonaPic(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !personaPicFor) return;
    const dataUrl = await new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => res(String(r.result));
      r.onerror = () => rej(r.error);
      r.readAsDataURL(file);
    });
    app.personas = app.personas.map((p) => (p.id === personaPicFor ? { ...p, avatar: dataUrl } : p));
    savePersonas();
    personaPicFor = null;
  }
</script>

<div class="hint">Your personas — who <em>you</em> are in the chat (the {'{{'}user{'}}'} side). A persona is just a picture and a short description; the active one is used as you.</div>
<div class="plist">
  {#each app.personas as p (p.id)}
    <div class="prow2" class:sel={p.id === app.activePersona}>
      <button type="button" class="ppic" onclick={() => choosePersonaPic(p.id)} title="Set picture">
        {#if p.avatar}<img src={p.avatar} alt="" />{:else}<span class="ppic-ph">{(p.name?.[0] || '?').toUpperCase()}</span>{/if}
        <span class="ppic-edit">change</span>
      </button>
      <div class="pbody">
        <div class="prow2-head">
          <input class="pname" bind:value={p.name} oninput={savePersonas} placeholder="Name" />
          {#if p.id === app.activePersona}
            <span class="badge">active</span>
          {:else}
            <button class="ghost sm" onclick={() => setActivePersona(p.id)}>Use</button>
          {/if}
          <button class="ghost sm" onclick={() => removePersona(p.id)} title="Delete persona" aria-label="Delete">✕</button>
        </div>
        <textarea class="pdescr" bind:value={p.description} oninput={savePersonas}
          placeholder="A short description of who you are — used as your persona in the chat…"></textarea>
      </div>
    </div>
  {/each}
</div>
<input type="file" accept="image/*" bind:this={personaPicInput} onchange={onPersonaPic} hidden />
<button class="newp" onclick={newPersona}>＋ New persona</button>

<style>
  .plist { display: flex; flex-direction: column; gap: 12px; margin: 16px 0; }
  .prow2 {
    display: flex; gap: 14px; padding: 14px;
    background: var(--elev); border: 1px solid var(--border-soft); border-radius: 12px;
  }
  .prow2.sel { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent-glow); }
  .ppic {
    position: relative; width: 84px; height: 84px; flex: none; padding: 0; overflow: hidden;
    border-radius: 12px; border: 1px solid var(--border); box-shadow: none; cursor: pointer;
    background: linear-gradient(135deg, var(--accent), #9a6dff);
  }
  .ppic:hover { filter: brightness(1.05); }
  .ppic img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .ppic-ph { display: grid; place-items: center; width: 100%; height: 100%; font-size: 32px; font-weight: 700; color: #fff; }
  .ppic-edit {
    position: absolute; left: 0; right: 0; bottom: 0; padding: 2px; text-align: center;
    font-size: 10px; color: #fff; background: rgba(0, 0, 0, .55); opacity: 0; transition: opacity .12s;
  }
  .ppic:hover .ppic-edit { opacity: 1; }
  .pbody { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .prow2-head { display: flex; align-items: center; gap: 10px; }
  .pname { flex: 1; min-width: 0; font-weight: 600; }
  .badge {
    flex: none; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px;
    color: var(--accent); border: 1px solid var(--accent); border-radius: 999px; padding: 2px 8px;
  }
  .pdescr { width: 100%; flex: 1; min-height: 60px; resize: vertical; margin-top: 10px; font-size: 13.5px; }
  .newp { padding: 9px 14px; font-weight: 560; font-size: 13px; }
</style>

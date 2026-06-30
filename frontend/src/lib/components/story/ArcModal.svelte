<script>
  // Arc-details modal: edit an arc's name / dramatic function / mini-ending /
  // rationale, or kick off expansion to chapters. Mirrors SceneModal's chrome so
  // the two read as one family. Field edits return via onSave; expansion is
  // delegated to the page (which owns the streaming flow).
  import Modal from '$lib/components/shared/Modal.svelte';
  let {
    arc = null,
    index = 0,
    expanded = false,   // does the arc already have chapters?
    onSave = null,      // callback(updatedArc)
    onExpand = null,    // callback() — page runs the streaming expand
    onClose = null,
  } = $props();

  let name      = $state('');
  let dramFn    = $state('');
  let miniEnd   = $state('');
  let rationale = $state('');

  $effect(() => {
    if (arc) {
      name      = arc.name || '';
      dramFn    = arc.dramatic_function || '';
      miniEnd   = arc.mini_ending || '';
      rationale = arc.rationale || '';
    }
  });

  function close() { onClose?.(); }
  function accept() {
    onSave?.({ ...arc, name, dramatic_function: dramFn, mini_ending: miniEnd, rationale });
  }
  function expand() { onExpand?.(); close(); }
</script>

<Modal onClose={close} flush width="620px" maxHeight="90vh">
  <div class="dhead">
    <span class="chnum">Arc {index + 1}</span>
    <h3 class="dtitle">{name || 'Untitled Arc'}</h3>
    <button class="x" onclick={close} aria-label="Close">✕</button>
  </div>

  <div class="body">
    <div class="fields">
      <div class="frow half">
        <div>
          <label class="fl">Name</label>
          <input class="fld" bind:value={name} placeholder="Arc name" />
        </div>
        <div>
          <label class="fl">Dramatic function</label>
          <input class="fld" bind:value={dramFn} placeholder="e.g. rising action" />
        </div>
      </div>

      <div class="frow">
        <label class="fl">Mini-ending <span class="lo">— where this arc lands</span></label>
        <textarea class="fld ta" rows="2" bind:value={miniEnd} placeholder="How this arc resolves…"></textarea>
      </div>

      <div class="frow">
        <label class="fl">Rationale <span class="lo">— why this arc exists</span></label>
        <textarea class="fld ta" rows="3" bind:value={rationale} placeholder="The narrative purpose of this arc…"></textarea>
      </div>
    </div>
  </div>

  <div class="foot">
    {#if onExpand}
      <button class="regen-btn" onclick={expand}>{expanded ? '↻ Re-expand chapters' : '⊕ Expand to chapters'}</button>
    {/if}
    <span class="sp"></span>
    <button class="ghost" onclick={close}>Cancel</button>
    <button class="accept-btn" onclick={accept}>✓ Save</button>
  </div>
</Modal>

<style>
  .dhead {
    display: flex; align-items: center; gap: 10px;
    padding: 14px 16px 10px; border-bottom: 1px solid var(--border-soft); flex: none;
  }
  .chnum {
    font-size: 11px; font-weight: 700; color: var(--accent);
    background: rgba(109, 140, 255, .14); border-radius: 999px; padding: 2px 8px; flex: none;
  }
  .dtitle {
    margin: 0; font-size: 15px; font-weight: 700; color: var(--text);
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .x {
    width: 28px; height: 28px; flex: none; padding: 0; border-radius: 7px;
    background: var(--elev); border: 1px solid var(--border); color: var(--muted); font-size: 11px;
  }
  .x:hover { color: var(--text); background: var(--elev-2); }

  .body { padding: 14px 16px; overflow: auto; flex: 1; }
  .fields { display: flex; flex-direction: column; gap: 10px; }
  .frow { display: flex; flex-direction: column; gap: 4px; }
  .frow.half { flex-direction: row; gap: 12px; }
  .frow.half > div { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
  .fl { font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .3px; font-weight: 600; }
  .lo { color: var(--faint); text-transform: none; letter-spacing: 0; font-weight: 400; }
  .fld {
    width: 100%; padding: 7px 9px; font-size: 13px; border-radius: 8px;
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    font-family: inherit; box-sizing: border-box;
  }
  .fld:focus { border-color: var(--accent); outline: none; box-shadow: 0 0 0 2px var(--accent-glow, rgba(109,140,255,.2)); }
  .ta { line-height: 1.5; resize: vertical; }

  .foot {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 16px 12px; border-top: 1px solid var(--border-soft); flex: none;
  }
  .sp { flex: 1; }
  .regen-btn {
    font-size: 12.5px; font-weight: 600; padding: 7px 14px; border-radius: 8px;
    background: var(--elev-2); border: 1px solid var(--border); color: var(--text); cursor: pointer;
  }
  .regen-btn:hover { border-color: var(--accent); color: var(--accent); }
  .accept-btn {
    font-size: 13px; font-weight: 700; padding: 7px 18px; border-radius: 8px;
    background: var(--accent); border: 0; color: #fff; cursor: pointer;
  }
  .accept-btn:hover { filter: brightness(1.1); }
</style>

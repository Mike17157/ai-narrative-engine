<script>
  // Location-grouped cast roster — REPLACES the relationship ring/web. Characters live in
  // SECTIONS by their home location (inferred from / editable against the map). Each character is
  // a card in a row; their bonds show as small chips on the card. Move a card between locations
  // with its home picker; add an existing character straight into a section. See [[story-tab-shell]].
  import { post } from '$lib/api.js';

  let { storyKey = '', cast = [], locations = [], relationships = [], addItems = [],
        onSelect = () => {}, onSetHome = () => {}, onAddHere = () => {},
        onSaveBonds = () => {} } = $props();

  // Stance → chip colour (mirrors RelationshipGraph's scale; kept tiny + local).
  const STANCE = {
    devoted: '#5ec27a', warm: '#8fbf6e', neutral: '#8892a6',
    strained: '#d1953f', hostile: '#d0655a',
  };
  const STANCES = Object.keys(STANCE);

  const nameOf = (k) => cast.find((c) => c.key === k)?.name || k;
  function bondsOf(key) {
    return (relationships || [])
      .filter((r) => r.source === key || r.target === key)
      .map((r) => {
        const out = r.source === key;
        return {
          other: out ? r.target : r.source,
          stance: (out ? r.stance : (r.target_stance || r.stance)) || 'neutral',
          nature: r.nature || '', dir: out ? '→' : '←', rel: r,
        };
      });
  }

  // ── Bond editor — specify a relationship DIRECTLY: the daylight read per side (stance +
  // dynamic), the mundane nature, and the DEPTH fields: potential (the hidden undercurrent)
  // + trajectory (how it turns when the secret surfaces). Opens blank from "+ bond" or loaded
  // from a bond chip. Saves through onSaveBonds (the story PUT).
  let bond = $state(null);   // working copy {id?, source, target, nature, stance, dynamic, ...}
  function newBond(source) {
    const other = cast.find((c) => c.key !== source)?.key || '';
    bond = { source, target: other, nature: '', stance: 'neutral', dynamic: '',
             target_stance: '', target_dynamic: '', potential: '', trajectory: '', note: '' };
  }
  function editBond(r) { bond = { ...r }; }
  function saveBond() {
    if (!bond?.source || !bond?.target || bond.source === bond.target) return;
    const id = bond.id || `r-${bond.source}-${bond.target}`;
    const next = [...(relationships || []).filter((r) => (r.id || `r-${r.source}-${r.target}`) !== id),
                  { ...bond, id }];
    bond = null;
    onSaveBonds(next);
  }
  function deleteBond() {
    const id = bond?.id;
    bond = null;
    if (id) onSaveBonds((relationships || []).filter((r) => r.id !== id));
  }

  // ── ✨ Weave — v4pro proposes the web (daylight surface + hidden undercurrent per bond,
  // grown from the cast's wounds/lies); each proposal is reviewed here before it exists.
  let weaving = $state(false);
  let proposals = $state([]);
  let weaveErr = $state('');
  async function weave() {
    if (weaving || !storyKey) return;
    weaving = true; weaveErr = '';
    const r = await post(`/stories/${storyKey}/weave-bonds`, {});
    weaving = false;
    if (r.ok && r.data?.bonds) {
      proposals = r.data.bonds;
      if (!proposals.length) weaveErr = 'nothing new to propose — the web is woven';
    } else weaveErr = r.data?.error || 'weave failed';
  }
  function acceptProposal(i) {
    const b = proposals[i];
    proposals = proposals.filter((_, j) => j !== i);
    onSaveBonds([...(relationships || []), b]);
  }
  function acceptAll() {
    const add = proposals; proposals = [];
    onSaveBonds([...(relationships || []), ...add]);
  }

  let locIds = $derived(new Set(locations.map((l) => l.id)));
  const membersOf = (id) => cast.filter((c) => c.home === id);
  // Unplaced = no home, or a home pointing at a location that no longer exists.
  let unplaced = $derived(cast.filter((c) => !c.home || !locIds.has(c.home)));

  // Hierarchy: root locations (no parent) are coloured CONTAINERS that hold their children.
  let roots = $derived(locations.filter((l) => !l.parent || !locIds.has(l.parent)));
  const childrenOf = (id) => locations.filter((l) => l.parent === id);
  // Members anchored anywhere inside a root (itself or any descendant) → the header count.
  function totalIn(id) {
    return membersOf(id).length + childrenOf(id).reduce((n, ch) => n + totalIn(ch.id), 0);
  }
  // Distinct hue per root (golden-angle spread) → its container colour, inherited by children.
  const rootHue = (i) => (i * 137 + 25) % 360;

  // Per-section "add here" inline picker (like the arc cast picker).
  let openPicker = $state(null);   // location id whose picker is open ('' = unplaced)
  function pick(locId, key) { openPicker = null; onAddHere(locId, key); }
</script>

<div class="roster">
  <div class="rtop">
    <p class="hint">Characters grouped by where they live. Bonds show as chips — click one to edit
      its depth (surface read · undercurrent · trajectory), or <b>＋ bond</b> on a card.</p>
    <span class="sp"></span>
    <button class="weavebtn" onclick={weave} disabled={weaving}
            title="Propose the relationship web: an innocent daylight read per side + a hidden undercurrent grown from each character's wounds and lies — reviewed before anything is saved">
      {weaving ? '✨ Weaving…' : '✨ Weave bonds'}</button>
  </div>
  {#if weaveErr}<div class="werr">{weaveErr}</div>{/if}

  {#if proposals.length}
    <div class="props">
      <div class="phead">
        <b>Proposed bonds</b><span class="hint">— review each; nothing exists until accepted</span>
        <span class="sp"></span>
        <button class="pall" onclick={acceptAll}>✓ Accept all</button>
      </div>
      {#each proposals as p, i (p.id)}
        <div class="prop">
          <div class="prow">
            <b>{nameOf(p.source)} ⇄ {nameOf(p.target)}</b>
            <span class="pnature">{p.nature}</span>
            <span class="sp"></span>
            <button class="pacc" onclick={() => acceptProposal(i)}>✓</button>
            <button class="prej" onclick={() => (proposals = proposals.filter((_, j) => j !== i))}>×</button>
          </div>
          <div class="pline"><span class="pk">daylight</span>
            {nameOf(p.source)}: <i>{p.dynamic}</i> ({p.stance}) · {nameOf(p.target)}: <i>{p.target_dynamic}</i> ({p.target_stance})</div>
          <div class="pline deep"><span class="pk">undercurrent</span> {p.potential}</div>
          <div class="pline"><span class="pk">trajectory</span> {p.trajectory}</div>
        </div>
      {/each}
    </div>
  {/if}

  {#each roots as root, i (root.id)}
    {@const kids = childrenOf(root.id)}
    <section class="root" style={`--rc: hsl(${rootHue(i)} 60% 60%)`}>
      <header class="rhead">
        <span class="dot"></span>
        <span class="rname">{root.name || root.id}</span>
        <span class="rcount">{totalIn(root.id) || ''}</span>
        <span class="sp"></span>
        {@render addhere(root.id)}
      </header>

      <!-- Characters homed at the root itself -->
      {#if membersOf(root.id).length}
        <div class="cards">{#each membersOf(root.id) as c (c.key)}{@render card(c, root.id)}{/each}</div>
      {/if}

      <!-- Child locations nested inside, sharing the root's colour -->
      {#each kids as kid (kid.id)}
        <div class="kid">
          <header class="khead">
            <span class="kname">↳ {kid.name || kid.id}</span>
            <span class="kcount">{membersOf(kid.id).length || ''}</span>
            <span class="sp"></span>
            {@render addhere(kid.id)}
          </header>
          <div class="cards">
            {#each membersOf(kid.id) as c (c.key)}{@render card(c, kid.id)}{:else}<div class="empty">empty</div>{/each}
          </div>
        </div>
      {/each}

      {#if !membersOf(root.id).length && !kids.length}<div class="empty">No one here yet.</div>{/if}
    </section>
  {/each}

  {#if unplaced.length}
    <section class="root unplaced">
      <header class="rhead">
        <span class="dot"></span>
        <span class="rname">Unplaced</span>
        <span class="rcount">{unplaced.length}</span>
      </header>
      <div class="cards">
        {#each unplaced as c (c.key)}{@render card(c, '')}{/each}
      </div>
    </section>
  {/if}

  {#if !locations.length}
    <div class="none">No locations yet — add some in the <b>Map</b> tab, then place your cast here.</div>
  {/if}
</div>

{#snippet addhere(locId)}
  <div class="addwrap">
    <button class="addbtn" onclick={() => (openPicker = openPicker === locId ? null : locId)}
            title="Place a character here">＋ here</button>
    {#if openPicker === locId}
      <div class="picker">
        {#each addItems as it (it.value)}
          <button class="pick" onclick={() => pick(locId, it.value)}>{it.label}</button>
        {:else}
          <span class="pempty">No unplaced characters to add</span>
        {/each}
      </div>
    {/if}
  </div>
{/snippet}

{#snippet card(c, homeId)}
  <div class="pcard" class:primary={c.primary}>
    <button class="face" onclick={() => onSelect(c.key)} title="Open card">
      {#if c.img}<img src={c.img} alt={c.name} />{:else}<span class="ph">🎭</span>{/if}
    </button>
    <div class="body">
      <button class="nm" onclick={() => onSelect(c.key)}>{c.primary ? '★ ' : ''}{c.name}</button>
      {#if c.role}<div class="role">{c.role}</div>{/if}
      <div class="bonds">
        {#each bondsOf(c.key) as b (b.dir + b.other)}
          <button class="bond" style={`--bc:${STANCE[b.stance] || STANCE.neutral}`}
                onclick={() => editBond(b.rel)}
                title={`${c.name} ${b.dir} ${nameOf(b.other)}${b.nature ? ' — ' + b.nature : ''} (${b.stance})` +
                       (b.rel.potential ? `\nundercurrent: ${b.rel.potential}` : '') +
                       '\nclick to edit'}>
            {b.dir} {nameOf(b.other)}
          </button>
        {/each}
        <button class="bond add" onclick={() => newBond(c.key)} title="Add a bond from this character">＋ bond</button>
      </div>
      <select class="homesel" value={homeId} onchange={(e) => onSetHome(c.key, e.currentTarget.value)}
              title="Move to another location" onclick={(e) => e.stopPropagation()}>
        <option value="">— unplaced —</option>
        {#each locations as l (l.id)}<option value={l.id}>{l.name || l.id}</option>{/each}
      </select>
    </div>
  </div>
{/snippet}

<!-- Bond editor — the relationship's two layers: the daylight read per side, and the depth
     (undercurrent + trajectory) that recolours it once known. -->
{#if bond}
  <div class="bback" role="button" tabindex="-1" onclick={() => (bond = null)} onkeydown={(e) => e.key === 'Escape' && (bond = null)}>
    <div class="bmodal" role="dialog" onclick={(e) => e.stopPropagation()}>
      <div class="bhead">
        <b>{bond.id ? 'Edit bond' : 'New bond'} — {nameOf(bond.source)}</b>
        <span class="sp"></span>
        <button class="bx" onclick={() => (bond = null)}>×</button>
      </div>
      <div class="bbody">
        <div class="brow">
          <label class="bl">with
            <select bind:value={bond.target}>
              {#each cast.filter((c) => c.key !== bond.source) as c (c.key)}
                <option value={c.key}>{c.name}</option>
              {/each}
            </select>
          </label>
          <label class="bl grow">nature <input bind:value={bond.nature} placeholder="the mundane label the world sees — classmate, neighbour…" /></label>
        </div>
        <div class="bsec">Daylight — how it reads on the surface</div>
        <div class="brow">
          <label class="bl">{nameOf(bond.source)} is
            <select bind:value={bond.stance}>{#each STANCES as s}<option value={s}>{s}</option>{/each}</select>
          </label>
          <label class="bl grow">and treats {nameOf(bond.target)}… <input bind:value={bond.dynamic} placeholder="specific, warm or funny where it fits — 'steals her pens daily'" /></label>
        </div>
        <div class="brow">
          <label class="bl">{nameOf(bond.target)} is
            <select bind:value={bond.target_stance}><option value="">(mirrors)</option>{#each STANCES as s}<option value={s}>{s}</option>{/each}</select>
          </label>
          <label class="bl grow">and treats {nameOf(bond.source)}… <input bind:value={bond.target_dynamic} placeholder="the other side's read (empty = mirrors)" /></label>
        </div>
        <div class="bsec deep">Depth — what's underneath</div>
        <label class="bl">undercurrent
          <textarea rows="2" bind:value={bond.potential} placeholder="what is secretly true between them — grown from their wounds and lies, the thing neither says"></textarea>
        </label>
        <label class="bl">trajectory
          <input bind:value={bond.trajectory} placeholder="from → to: how this turns when the hidden thing surfaces" />
        </label>
        <label class="bl">note
          <input bind:value={bond.note} placeholder="history / canon detail (optional)" />
        </label>
      </div>
      <div class="bfoot">
        {#if bond.id}<button class="bdel" onclick={deleteBond}>Delete</button>{/if}
        <span class="sp"></span>
        <button class="bcancel" onclick={() => (bond = null)}>Cancel</button>
        <button class="bsave" onclick={saveBond} disabled={!bond.target || !bond.nature.trim()}>Save bond</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .roster { display: flex; flex-direction: column; gap: 12px; }
  .rtop { display: flex; align-items: flex-start; gap: 10px; }
  .weavebtn { flex: none; font-size: 12px; font-weight: 600; padding: 6px 13px; border-radius: 999px;
              background: none; border: 1px dashed var(--accent); color: var(--accent); cursor: pointer; }
  .weavebtn:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .werr { font-size: 12px; color: var(--bad, #d0655a); }

  /* Weave proposals — review cards: daylight · undercurrent · trajectory */
  .props { display: flex; flex-direction: column; gap: 7px; padding: 10px 12px; border-radius: 12px;
           border: 1px dashed var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .phead { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text); }
  .pall { font-size: 11.5px; padding: 4px 11px; border-radius: 999px; background: var(--accent);
          border: none; color: #fff; font-weight: 700; cursor: pointer; }
  .prop { display: flex; flex-direction: column; gap: 3px; padding: 8px 10px; border-radius: 9px;
          background: var(--elev); border: 1px solid var(--border-soft); }
  .prow { display: flex; align-items: center; gap: 8px; font-size: 12.5px; color: var(--text); }
  .pnature { font-size: 11px; color: var(--muted); font-style: italic; }
  .pacc, .prej { width: 24px; height: 24px; padding: 0; border-radius: 7px; display: grid; place-items: center;
                 background: var(--elev-2, var(--bg)); border: 1px solid var(--border-soft); cursor: pointer; }
  .pacc { color: var(--good, #5ec27a); } .pacc:hover { border-color: var(--good, #5ec27a); }
  .prej { color: var(--muted); } .prej:hover { color: var(--bad); border-color: var(--bad); }
  .pline { font-size: 11.5px; color: var(--muted); line-height: 1.5; }
  .pline.deep { color: var(--text); }
  .pk { display: inline-block; min-width: 82px; font-size: 9.5px; font-weight: 700; text-transform: uppercase;
        letter-spacing: .4px; color: var(--faint); }

  /* Bond editor modal */
  .bback { position: fixed; inset: 0; z-index: 90; background: rgba(0,0,0,.55); display: grid; place-items: center; }
  .bmodal { width: min(560px, 94vw); max-height: 88vh; overflow: auto; background: var(--panel);
            border: 1px solid var(--border); border-radius: 14px; box-shadow: 0 18px 60px rgba(0,0,0,.5); }
  .bhead { display: flex; align-items: center; gap: 10px; padding: 11px 16px; font-size: 13px; color: var(--text);
           border-bottom: 1px solid var(--border-soft); background: color-mix(in srgb, var(--accent) 8%, var(--panel)); }
  .bx { width: 24px; height: 24px; padding: 0; border-radius: 7px; background: none; border: none;
        color: var(--muted); font-size: 16px; cursor: pointer; }
  .bbody { display: flex; flex-direction: column; gap: 9px; padding: 13px 16px; }
  .brow { display: flex; gap: 9px; align-items: flex-end; }
  .bl { display: flex; flex-direction: column; gap: 3px; font-size: 10.5px; font-weight: 700;
        text-transform: uppercase; letter-spacing: .3px; color: var(--faint); }
  .bl.grow { flex: 1; min-width: 0; }
  .bl input, .bl select, .bl textarea { font: inherit; font-size: 12.5px; font-weight: 400; text-transform: none;
        letter-spacing: 0; color: var(--text); background: var(--elev); border: 1px solid var(--border-soft);
        border-radius: 7px; padding: 6px 8px; }
  .bl textarea { resize: vertical; line-height: 1.5; }
  .bl input:focus, .bl select:focus, .bl textarea:focus { outline: none; border-color: var(--accent); }
  .bsec { font-size: 10px; font-weight: 800; text-transform: uppercase; letter-spacing: .6px;
          color: var(--accent); margin-top: 2px; }
  .bsec.deep { color: var(--warn, #d1953f); }
  .bfoot { display: flex; align-items: center; gap: 8px; padding: 11px 16px; border-top: 1px solid var(--border-soft); }
  .bsave { font-size: 12.5px; font-weight: 700; padding: 7px 16px; border-radius: 9px;
           background: var(--accent); border: none; color: #fff; cursor: pointer; }
  .bsave:disabled { opacity: .5; cursor: default; }
  .bcancel { font-size: 12px; padding: 7px 13px; border-radius: 9px; background: none;
             border: 1px solid var(--border-soft); color: var(--muted); cursor: pointer; }
  .bdel { font-size: 12px; padding: 7px 13px; border-radius: 9px; background: none;
          border: 1px solid var(--bad, #d0655a); color: var(--bad, #d0655a); cursor: pointer; }
  .hint { margin: 0 0 2px; }
  .none { padding: 26px 4px; color: var(--faint); font-size: 13px; }

  /* Root location = a coloured container (its --rc hue) that visibly HOLDS its children. */
  .root { --rc: var(--accent); border: 1px solid color-mix(in srgb, var(--rc) 40%, var(--border-soft));
          border-left: 5px solid var(--rc); border-radius: 12px; background: var(--panel);
          overflow: hidden; }
  .root.unplaced { --rc: var(--faint); border-left-style: dashed; }
  .rhead { display: flex; align-items: center; gap: 8px; padding: 9px 12px;
           background: color-mix(in srgb, var(--rc) 12%, var(--panel));
           border-bottom: 1px solid color-mix(in srgb, var(--rc) 22%, transparent); }
  .dot { width: 9px; height: 9px; flex: none; border-radius: 50%; background: var(--rc);
         box-shadow: 0 0 0 3px color-mix(in srgb, var(--rc) 25%, transparent); }
  .rname { font-size: 14px; font-weight: 800; color: var(--text); letter-spacing: .2px; }
  .rcount { font-size: 11px; font-weight: 700; color: var(--rc); }
  .sp { flex: 1; }

  /* Child location = a nested block inside the root, tinted with the parent colour. */
  .kid { margin: 8px 10px 8px 18px; padding: 7px 10px; border-radius: 9px;
         border-left: 3px solid color-mix(in srgb, var(--rc) 55%, transparent);
         background: color-mix(in srgb, var(--rc) 5%, transparent); }
  .khead { display: flex; align-items: center; gap: 7px; margin-bottom: 6px; }
  .kname { font-size: 12px; font-weight: 700; color: color-mix(in srgb, var(--rc) 55%, var(--text)); }
  .kcount { font-size: 10.5px; font-weight: 700; color: var(--faint); }
  /* Root's own cards sit directly under its header (before any children). */
  .rhead + .cards { padding: 10px 12px; }

  .addwrap { position: relative; }
  .addbtn { font-size: 11.5px; padding: 4px 10px; border-radius: 999px; background: var(--elev);
            border: 1px dashed var(--border); color: var(--muted); cursor: pointer; }
  .addbtn:hover { border-color: var(--accent); color: var(--accent); }
  .picker { position: absolute; top: calc(100% + 4px); right: 0; z-index: 40; min-width: 160px;
            background: var(--panel); border: 1px solid var(--border); border-radius: 9px; padding: 4px;
            box-shadow: 0 6px 20px rgba(0,0,0,.28); display: flex; flex-direction: column; gap: 1px;
            max-height: 260px; overflow: auto; }
  .pick { padding: 6px 10px; border-radius: 6px; font-size: 12.5px; color: var(--text);
          background: none; border: none; cursor: pointer; text-align: left; }
  .pick:hover { background: var(--elev); }
  .pempty { font-size: 11.5px; color: var(--faint); padding: 6px 10px; }

  .cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px; }
  .empty { font-size: 12px; color: var(--faint); font-style: italic; padding: 4px 2px; }

  .pcard { display: flex; gap: 9px; padding: 8px; border-radius: 10px; background: var(--elev);
           border: 1px solid var(--border-soft); }
  .pcard.primary { border-color: var(--accent); }
  .face { flex: none; width: 52px; height: 66px; padding: 0; border-radius: 8px; overflow: hidden;
          background: var(--elev-2, var(--bg)); border: 1px solid var(--border-soft); cursor: pointer;
          display: grid; place-items: center; }
  .face img { width: 100%; height: 100%; object-fit: cover; object-position: top; }
  .ph { font-size: 24px; opacity: .5; }
  .body { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
  .nm { font-size: 12.5px; font-weight: 700; color: var(--text); background: none; border: none;
        padding: 0; cursor: pointer; text-align: left; }
  .nm:hover { color: var(--accent); }
  .role { font-size: 10.5px; color: var(--faint); text-transform: uppercase; letter-spacing: .3px; }
  .bonds { display: flex; flex-wrap: wrap; gap: 3px; }
  .bond { font-size: 10px; padding: 1px 6px; border-radius: 999px; white-space: nowrap; cursor: pointer;
          color: var(--bc); border: 1px solid color-mix(in srgb, var(--bc) 45%, transparent);
          background: color-mix(in srgb, var(--bc) 12%, transparent); }
  .bond:hover { border-color: var(--bc); }
  .bond.add { --bc: var(--faint); border-style: dashed; background: none; }
  .bond.add:hover { --bc: var(--accent); }
  .homesel { margin-top: 2px; font-size: 10.5px; color: var(--muted); background: var(--bg);
             border: 1px solid var(--border-soft); border-radius: 6px; padding: 3px 5px; max-width: 100%; }
</style>

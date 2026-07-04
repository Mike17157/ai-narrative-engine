<script>
  // Location-grouped cast roster — REPLACES the relationship ring/web. Characters live in
  // SECTIONS by their home location (inferred from / editable against the map). Each character is
  // a card in a row; their bonds show as small chips on the card. Move a card between locations
  // with its home picker; add an existing character straight into a section. See [[story-tab-shell]].
  let { cast = [], locations = [], relationships = [], addItems = [],
        onSelect = () => {}, onSetHome = () => {}, onAddHere = () => {} } = $props();

  // Stance → chip colour (mirrors RelationshipGraph's scale; kept tiny + local).
  const STANCE = {
    devoted: '#5ec27a', warm: '#8fbf6e', neutral: '#8892a6',
    strained: '#d1953f', hostile: '#d0655a',
  };

  const nameOf = (k) => cast.find((c) => c.key === k)?.name || k;
  function bondsOf(key) {
    return (relationships || [])
      .filter((r) => r.source === key || r.target === key)
      .map((r) => {
        const out = r.source === key;
        return {
          other: out ? r.target : r.source,
          stance: (out ? r.stance : (r.target_stance || r.stance)) || 'neutral',
          nature: r.nature || '', dir: out ? '→' : '←',
        };
      });
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
  <p class="hint">Characters grouped by where they live. Drag isn't needed — use a card's location
    menu to move it, or <b>＋ here</b> to place a character in a section. Bonds show as chips.</p>

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
      {#if bondsOf(c.key).length}
        <div class="bonds">
          {#each bondsOf(c.key) as b (b.dir + b.other)}
            <span class="bond" style={`--bc:${STANCE[b.stance] || STANCE.neutral}`}
                  title={`${c.name} ${b.dir} ${nameOf(b.other)}${b.nature ? ' — ' + b.nature : ''} (${b.stance})`}>
              {b.dir} {nameOf(b.other)}
            </span>
          {/each}
        </div>
      {/if}
      <select class="homesel" value={homeId} onchange={(e) => onSetHome(c.key, e.currentTarget.value)}
              title="Move to another location" onclick={(e) => e.stopPropagation()}>
        <option value="">— unplaced —</option>
        {#each locations as l (l.id)}<option value={l.id}>{l.name || l.id}</option>{/each}
      </select>
    </div>
  </div>
{/snippet}

<style>
  .roster { display: flex; flex-direction: column; gap: 12px; }
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
  .bond { font-size: 10px; padding: 1px 6px; border-radius: 999px; white-space: nowrap;
          color: var(--bc); border: 1px solid color-mix(in srgb, var(--bc) 45%, transparent);
          background: color-mix(in srgb, var(--bc) 12%, transparent); }
  .homesel { margin-top: 2px; font-size: 10.5px; color: var(--muted); background: var(--bg);
             border: 1px solid var(--border-soft); border-radius: 6px; padding: 3px 5px; max-width: 100%; }
</style>

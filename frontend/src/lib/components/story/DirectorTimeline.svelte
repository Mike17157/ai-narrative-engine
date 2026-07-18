<script>
  /*
   * A deliberately small interaction boundary for authoring scene timing.
   * DirectorBoard owns the private preview; this component owns drag/drop,
   * keyboard-friendly placement controls, and the lane presentation.
   */
  let { lanes = [], locations = [], onselect = null, onschedule = null, onlocation = null, scheduling = false } = $props();

  let draggedScene = $state(null);
  let draggedFrom = $state('');
  let dropTarget = $state('');
  let moveMessage = $state('');

  const scheduleSlots = $derived(lanes.filter((lane) => lane.id !== 'unplaced'));

  function choose(item) {
    if (typeof onselect === 'function') onselect(item);
  }

  /*
   * Keep the authoring surface decoupled from the conversation implementation.
   * A callback still carries the old `kind`/`scene` fields, but now includes a
   * stable, serialisable target that a caller can use to open a section or
   * item-scoped editorial thread.  `field` is deliberately metadata beside
   * the item identity: a scene remains the thing being edited, even when the
   * author clicks its public surface rather than its title.
   */
  function editorialTarget(section, itemKind, id, field = '') {
    return {
      section,
      item: { kind: itemKind, id: String(id || '') },
      ...(field ? { field } : {})
    };
  }

  function sceneSelection(scene, lane, field = '') {
    const label = field
      ? `${scene.title} — ${field.replace(/[_-]+/g, ' ')}`
      : scene.title;
    const summaries = {
      public_surface: scene.publicSurface,
      private_logic: scene.privateLogic,
      trigger: scene.trigger,
      requires: scene.requires?.join(' · '),
      participants: scene.participants?.join(' · '),
      entity_window: scene.entityPeriods?.join(' · '),
      evidence: scene.evidence?.join(' · '),
      schedule: scene.slots?.join(' · ') || 'Not placed in time yet',
      location: scene.location || 'No declared location'
    };
    return {
      kind: field ? 'scene-field' : 'scene',
      id: scene.id,
      field,
      section: 'first_day',
      target: editorialTarget('first_day', 'scene', scene.id, field),
      label,
      summary: summaries[field] || scene.publicSurface || scene.title,
      slot: lane.id,
      scene
    };
  }

  function periodSelection(period, lane, field = '') {
    const label = period.label || period.id;
    return {
      kind: field ? 'entity-period-field' : 'entity-period',
      id: period.id,
      field,
      section: 'time_system',
      target: editorialTarget('time_system', 'entity_period', period.id, field),
      label: field ? `${label} — ${field.replace(/[_-]+/g, ' ')}` : label,
      summary: field === 'constraint'
        ? period.constraint
        : field === 'capabilities'
          ? period.capabilities?.join(' · ')
          : `${lane.label}: ${period.state}`,
      slot: lane.id,
      period
    };
  }

  function beginDrag(event, scene, lane) {
    if (scheduling || scene.id === 'opening-state') {
      event.preventDefault();
      return;
    }
    draggedScene = scene;
    draggedFrom = lane.id;
    dropTarget = '';
    event.dataTransfer?.setData('text/plain', scene.id);
    if (event.dataTransfer) event.dataTransfer.effectAllowed = 'move';
  }

  function clearDrag() {
    draggedScene = null;
    draggedFrom = '';
    dropTarget = '';
  }

  function allowDrop(event, lane) {
    if (!draggedScene || lane.id === draggedFrom || scheduling) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    dropTarget = `time:${lane.id}`;
  }

  function leaveDrop(lane) {
    if (dropTarget === `time:${lane.id}`) dropTarget = '';
  }

  function allowLocationDrop(event, location) {
    if (!draggedScene || scheduling || location?.id === draggedScene.locationId) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    dropTarget = `location:${location.id}`;
  }

  function leaveLocationDrop(location) {
    if (dropTarget === `location:${location.id}`) dropTarget = '';
  }

  function move(scene, slot) {
    if (!scene || slot === undefined || scheduling || scene.id === 'opening-state') return;
    moveMessage = slot === null
      ? `Returning ${scene.title} to unplaced scenes.`
      : `Scheduling ${scene.title} for ${slot}.`;
    if (typeof onschedule === 'function') onschedule({ scene, slot });
  }

  function moveLocation(scene, location) {
    if (!scene || !location || scheduling || scene.id === 'opening-state') return;
    moveMessage = `Moving ${scene.title} to ${location}.`;
    if (typeof onlocation === 'function') onlocation({ scene, location });
  }

  function drop(event, lane) {
    event.preventDefault();
    const scene = draggedScene;
    const source = draggedFrom;
    clearDrag();
    if (!scene || lane.id === source) return;
    move(scene, lane.id === 'unplaced' ? null : lane.id);
  }

  function dropLocation(event, location) {
    event.preventDefault();
    const scene = draggedScene;
    clearDrag();
    if (!scene || !location?.id || location.id === scene.locationId) return;
    moveLocation(scene, location.id);
  }

  function selectSlot(event, scene) {
    const slot = event.currentTarget.value;
    event.currentTarget.value = '';
    move(scene, slot === '__unplaced__' ? null : slot);
  }

  function selectLocation(event, scene) {
    const location = event.currentTarget.value;
    event.currentTarget.value = '';
    moveLocation(scene, location);
  }

  function sceneId(scene) {
    return String(scene?.id || 'scene');
  }
</script>

<section class="timeline-section" aria-label="First-day possibility timeline">
  <div class="section-head">
    <div>
      <span class="eyebrow">First day</span>
      <h3>Possibility timeline</h3>
      <p>Drag an unplaced scene into a time slot. Scenes stay possible until their stated conditions make them real.</p>
    </div>
    <span class="legend"><i class="public-key"></i> Player-facing <i class="private-key"></i> Director-only</span>
  </div>

  <div class="timeline" aria-busy={scheduling}>
    {#each lanes as lane (lane.id)}
      <section
        class="time-lane"
        class:unplaced={lane.id === 'unplaced'}
        class:drop-target={dropTarget === `time:${lane.id}`}
        aria-label={lane.label}
        ondragover={(event) => allowDrop(event, lane)}
        ondragleave={() => leaveDrop(lane)}
        ondrop={(event) => drop(event, lane)}
      >
        <header class="lane-head"><span>{lane.label}</span><small>{lane.scenes.length} scene{lane.scenes.length === 1 ? '' : 's'}</small></header>
        {#if lane.id === 'unplaced'}
          <p class="drop-hint">Drag a scene into morning, evening, or night.</p>
        {:else if dropTarget === `time:${lane.id}`}
          <p class="drop-hint active">Drop to schedule for {lane.label}.</p>
        {/if}
        {#if lane.periods.length}
          <div class="entity-lane" aria-label={`${lane.label} entity activity`}>
            {#each lane.periods as period (period.id)}
              <button type="button" class="entity-period" onclick={() => choose(periodSelection(period, lane))}>
                <span class="entity-state">{period.state}</span>
                <b>{period.label || period.id}</b>
                {#if period.capabilities.length}<small>{period.capabilities.join(' · ')}</small>{/if}
                {#if period.constraint}<em>{period.constraint}</em>{/if}
              </button>
            {/each}
          </div>
        {/if}
        <div class="scene-stack">
          {#each lane.scenes as scene (sceneId(scene))}
            <article class:has-private={Boolean(scene.privateLogic || scene.entityAction)} class:dragging={draggedScene?.id === scene.id} class="scene">
              <button
                type="button"
                class="scene-open scene-root"
                draggable={scene.id !== 'opening-state' && !scheduling}
                onclick={() => choose(sceneSelection(scene, lane))}
                ondragstart={(event) => beginDrag(event, scene, lane)}
                ondragend={clearDrag}
                aria-label={`${scene.title}. ${scene.id === 'opening-state' ? 'Opening state.' : `Drag to schedule or edit.`}`}
              >
                <span class="scene-meta">
                  <span>{#if scene.location}{scene.location}{:else}Possible scene{/if}</span>
                  <span class="scene-badges">
                    {#if scene.scheduleState === 'unplaced'}<i class="needs-time">Needs a time gate</i>{:else if scene.scheduleState === 'invalid'}<i class="needs-time">Invalid time gate</i>{/if}
                    {#if scene.entityAction}<i>Entity action</i>{/if}
                  </span>
                </span>
                <strong>{scene.title}</strong>
              </button>
              {#if scene.publicSurface}
                <button type="button" class="scene-segment surface" onclick={() => choose(sceneSelection(scene, lane, 'public_surface'))}>
                  <b>On stage</b><span>{scene.publicSurface}</span>
                </button>
              {/if}
              {#if scene.privateLogic}
                <button type="button" class="scene-segment private" onclick={() => choose(sceneSelection(scene, lane, 'private_logic'))}>
                  <b>Behind the screen</b><span>{scene.privateLogic}</span>
                </button>
              {/if}
              {#if scene.trigger || scene.requires.length || scene.participants.length || scene.evidence.length || scene.entityPeriods.length}
                <div class="scene-details">
                  {#if scene.trigger}<button type="button" class="scene-detail" onclick={() => choose(sceneSelection(scene, lane, 'trigger'))}><b>When</b><span>{scene.trigger}</span></button>{/if}
                  {#if scene.requires.length}<button type="button" class="scene-detail" onclick={() => choose(sceneSelection(scene, lane, 'requires'))}><b>Needs</b><span>{scene.requires.join(' · ')}</span></button>{/if}
                  {#if scene.participants.length}<button type="button" class="scene-detail" onclick={() => choose(sceneSelection(scene, lane, 'participants'))}><b>Present</b><span>{scene.participants.join(' · ')}</span></button>{/if}
                  {#if scene.entityPeriods.length}<button type="button" class="scene-detail" onclick={() => choose(sceneSelection(scene, lane, 'entity_window'))}><b>Entity window</b><span>{scene.entityPeriods.join(' · ')}</span></button>{/if}
                  {#if scene.evidence.length}<button type="button" class="scene-detail" onclick={() => choose(sceneSelection(scene, lane, 'evidence'))}><b>Leaves behind</b><span>{scene.evidence.join(' · ')}</span></button>{/if}
                </div>
              {/if}
              {#if scene.id !== 'opening-state'}
                <div class="move-controls">
                  <label class="move-control">
                    <span>Schedule</span>
                    <select disabled={scheduling} onfocus={() => choose(sceneSelection(scene, lane, 'schedule'))} onchange={(event) => selectSlot(event, scene)} aria-label={`Schedule ${scene.title}`}>
                      <option value="">Move to…</option>
                      {#if lane.id !== 'unplaced'}
                        <option value="__unplaced__">Unplaced</option>
                      {/if}
                      {#each scheduleSlots as slot (slot.id)}
                        <option value={slot.id} disabled={slot.id === lane.id}>{slot.label}</option>
                      {/each}
                    </select>
                  </label>
                  {#if locations.length}
                    <label class="move-control">
                      <span>Location</span>
                      <select disabled={scheduling} onfocus={() => choose(sceneSelection(scene, lane, 'location'))} onchange={(event) => selectLocation(event, scene)} aria-label={`Move ${scene.title} to a declared location`}>
                        <option value="">Move to…</option>
                        {#each locations as location (location.id)}
                          <option value={location.id} disabled={location.id === scene.locationId}>{location.label}</option>
                        {/each}
                      </select>
                    </label>
                  {/if}
                </div>
              {/if}
            </article>
          {:else}
            <p class="empty-lane">{lane.id === 'unplaced' ? 'Nothing needs a time slot.' : 'Drop an unplaced scene here.'}</p>
          {/each}
        </div>
      </section>
    {/each}
  </div>
  {#if locations.length}
    <section class="location-tray" aria-label="Move scenes between declared locations">
      <div class="location-tray-head">
        <span class="eyebrow">Scene location</span>
        <p>Drag a movable scene onto a place to move it there. Its time gate stays intact.</p>
      </div>
      <div class="location-drops">
        {#each locations as location (location.id)}
          <div
            class="location-drop"
            class:drop-target={dropTarget === `location:${location.id}`}
            role="group"
            aria-label={`Drop a movable scene at ${location.label}`}
            ondragover={(event) => allowLocationDrop(event, location)}
            ondragleave={() => leaveLocationDrop(location)}
            ondrop={(event) => dropLocation(event, location)}
          >
            <b>{location.label}</b>
            {#if location.description}<small>{location.description}</small>{:else}<small>Drop a scene here</small>{/if}
          </div>
        {/each}
      </div>
    </section>
  {/if}
  <p class="sr-only" aria-live="polite">{moveMessage}</p>
</section>

<style>
  .timeline-section { display: grid; gap: 12px; }
  .section-head { display: flex; align-items: end; justify-content: space-between; gap: 14px; }
  .eyebrow { display: block; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
  h3, p { margin: 0; } h3 { margin-top: 3px; font-size: 16px; letter-spacing: -.015em; }
  .section-head p { margin-top: 5px; color: var(--muted); font-size: 12.5px; line-height: 1.45; }
  .legend { display: inline-flex; align-items: center; flex: none; gap: 6px; color: var(--faint); font-size: 10.5px; white-space: nowrap; } .legend i { display: inline-block; width: 8px; height: 8px; margin-left: 4px; border: 1px solid var(--border-strong); } .legend .public-key { background: var(--elev-2); } .legend .private-key { background: color-mix(in srgb, #9c77e7 42%, var(--elev)); border-color: color-mix(in srgb, #9c77e7 55%, var(--border)); }
  .timeline { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; align-items: stretch; } .time-lane { min-width: 0; display: grid; align-content: start; gap: 8px; min-height: 188px; padding: 10px; border: 1px solid var(--border-soft); background: var(--panel); transition: border-color .15s, background .15s, box-shadow .15s; } .time-lane.unplaced { border-style: dashed; background: color-mix(in srgb, var(--panel) 88%, var(--elev)); } .time-lane.drop-target { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, var(--panel)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 40%, transparent); }
  .lane-head { display: flex; align-items: baseline; justify-content: space-between; gap: 14px; padding-bottom: 7px; border-bottom: 1px solid var(--border-soft); } .lane-head > span { font-size: 12px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; } .lane-head small { color: var(--faint); font-size: 10px; }
  .drop-hint, .empty-lane { margin: 2px 0; color: var(--faint); font-size: 11.5px; font-style: italic; line-height: 1.35; } .drop-hint.active { color: var(--accent); font-style: normal; }
  .entity-lane { display: grid; gap: 5px; } .entity-period { display: grid; gap: 2px; padding: 8px; border: 1px solid color-mix(in srgb, #9c77e7 44%, var(--border)); background: color-mix(in srgb, #9c77e7 10%, var(--elev)); text-align: left; } .entity-period:hover { border-color: #9c77e7; } .entity-state { color: #c6b4f5; font-size: 9px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; } .entity-period b { font-size: 12px; } .entity-period small, .entity-period em { color: var(--muted); font-size: 10.5px; font-style: normal; line-height: 1.3; }
  .scene-stack { display: grid; gap: 7px; } .scene { display: grid; gap: 0; min-width: 0; border: 1px solid var(--border-soft); border-radius: 0; background: var(--elev); transition: opacity .15s, border-color .15s, background .15s; } .scene:hover { border-color: var(--border-strong); background: var(--elev-2); } .scene.has-private { border-left: 2px solid #9c77e7; } .scene.dragging { opacity: .48; }
  .scene-open { display: grid; gap: 6px; width: 100%; padding: 9px; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .scene-open:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; } .scene-open[draggable="true"] { cursor: grab; } .scene-open[draggable="true"]:active { cursor: grabbing; }
  .scene-meta, .scene-badges { display: flex; justify-content: space-between; gap: 7px; } .scene-meta { color: var(--faint); font-size: 9.5px; font-weight: 700; letter-spacing: .055em; text-transform: uppercase; } .scene-badges { justify-content: flex-end; flex-wrap: wrap; } .scene-meta i { color: #c6b4f5; font-style: normal; } .scene-meta i.needs-time { color: var(--warn); } .scene-open > strong { font-size: 13px; line-height: 1.3; }
  .scene-segment { display: grid; gap: 2px; width: 100%; padding: 0 9px 8px; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 11.5px; line-height: 1.4; text-align: left; cursor: pointer; } .scene-segment:hover { color: var(--text); background: color-mix(in srgb, var(--accent) 5%, transparent); } .scene-segment:focus-visible, .scene-detail:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; } .scene-segment b, .scene-details b { color: var(--faint); font-size: 9px; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; } .scene-segment.private { margin: 1px 7px 7px; width: calc(100% - 14px); padding: 7px; border: 1px solid color-mix(in srgb, #9c77e7 32%, var(--border)); background: color-mix(in srgb, #9c77e7 8%, transparent); } .scene-segment.private:hover { background: color-mix(in srgb, #9c77e7 14%, var(--elev)); } .scene-segment.private b { color: #c6b4f5; }
  .scene-details { display: grid; gap: 3px; padding: 5px 9px 0; border-top: 1px solid var(--border-soft); } .scene-detail { display: grid; grid-template-columns: 72px 1fr; gap: 6px; width: 100%; padding: 2px 0; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 10.5px; line-height: 1.35; text-align: left; cursor: pointer; } .scene-detail:hover { color: var(--text); } .scene-detail > span { min-width: 0; }
  .move-controls { display: grid; gap: 4px; margin: 0 9px 9px; padding-top: 7px; border-top: 1px solid var(--border-soft); } .move-control { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--faint); font-size: 9px; font-weight: 800; letter-spacing: .055em; text-transform: uppercase; } .move-control select { max-width: 130px; border: 1px solid var(--border-soft); border-radius: 4px; padding: 3px 5px; background: var(--panel); color: var(--muted); font: inherit; font-size: 10px; letter-spacing: normal; text-transform: none; cursor: pointer; } .move-control select:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
  .location-tray { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--border-soft); background: color-mix(in srgb, var(--panel) 88%, var(--elev)); } .location-tray-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; } .location-tray-head .eyebrow { flex: none; } .location-tray-head p { color: var(--muted); font-size: 11px; line-height: 1.35; text-align: right; } .location-drops { display: grid; grid-template-columns: repeat(auto-fit, minmax(155px, 1fr)); gap: 6px; } .location-drop { display: grid; gap: 2px; min-height: 46px; padding: 8px; border: 1px dashed var(--border-strong); background: var(--elev); transition: border-color .15s, background .15s, box-shadow .15s; } .location-drop b { font-size: 11px; line-height: 1.25; } .location-drop small { color: var(--faint); font-size: 10px; line-height: 1.3; } .location-drop.drop-target { border-style: solid; border-color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, var(--elev)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent); } .location-drop.drop-target b { color: var(--accent); }
  .sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }
  @media (max-width: 620px) { .section-head, .location-tray-head { align-items: flex-start; flex-direction: column; } .legend { white-space: normal; } .location-tray-head p { text-align: left; } .timeline { grid-template-columns: 1fr; } }
</style>

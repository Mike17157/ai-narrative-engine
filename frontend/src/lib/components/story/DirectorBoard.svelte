<script>
  /*
   * An author-only projection of the compiled story scenario.  This component
   * deliberately never reads the public story-card endpoint: director facts
   * (private scene logic, gates, knowledge) must stay behind the dedicated
   * /director-preview boundary.
   *
   * Props:
   *   storyKey  required story key
   *   onselect       optional callback receiving legacy selection fields plus
   *                  { section, target: { section, item, field? } }
   *   onstorychange  optional callback receiving the refreshed safe card
   *   onarchitect    optional callback receiving a non-blocking Architect
   *                  reconciliation after a placement succeeds
   */
  import DirectorTimeline from './DirectorTimeline.svelte';

  let { storyKey, onselect = null, onstorychange = null, onarchitect = null } = $props();

  const DEFAULT_SLOTS = ['morning', 'evening', 'night'];
  let preview = $state(null);
  let loading = $state(false);
  let scheduling = $state(false);
  let error = $state('');
  let requestId = 0;

  const board = $derived.by(() => normalisePreview(preview));

  function isRecord(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function list(value) {
    if (Array.isArray(value)) return value.filter((item) => item !== null && item !== undefined);
    if (value === null || value === undefined || value === '') return [];
    return [value];
  }

  function firstText(...values) {
    for (const value of values) {
      if (typeof value === 'string' && value.trim()) return value.trim();
      if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    }
    return '';
  }

  function label(value) {
    return String(value || '')
      .replace(/[_-]+/g, ' ')
      .replace(/\b\w/g, (character) => character.toUpperCase());
  }

  function readableList(value) {
    return list(value)
      .map((item) => {
        if (typeof item === 'string' || typeof item === 'number') return String(item).trim();
        if (!isRecord(item)) return '';
        return firstText(item.name, item.title, item.label, item.character, item.id, item.summary, item.text);
      })
      .filter(Boolean);
  }

  function gateList(value) {
    if (!isRecord(value)) return readableList(value);
    const out = [];
    const flags = isRecord(value.flags) ? value.flags : {};
    for (const [name, expected] of Object.entries(flags)) {
      const expectation = typeof expected === 'boolean'
        ? (expected ? 'set' : 'not set')
        : firstText(expected) || readableList(expected).join(' · ');
      out.push(expectation ? `${label(name)}: ${expectation}` : label(name));
    }
    for (const [name, requirement] of Object.entries(value)) {
      if (name === 'flags') continue;
      const entries = readableList(requirement);
      if (entries.length) out.push(`${label(name)}: ${entries.join(' · ')}`);
    }
    return out;
  }

  function normaliseSlots(value) {
    const raw = readableList(value)
      .flatMap((item) => item.toLowerCase().split(/[,/|]/).map((part) => part.trim()))
      .filter(Boolean);
    return [...new Set(raw)];
  }

  function knowledgeEntries(value) {
    if (!value) return [];
    if (Array.isArray(value)) {
      return value.flatMap((item, index) => {
        if (typeof item === 'string') return [{ id: `knowledge-${index}`, name: '', summary: item }];
        if (!isRecord(item)) return [];
        const name = firstText(item.name, item.character, item.person, item.id, item.subject);
        const summary = firstText(item.summary, item.fact, item.known, item.text, item.note, item.value);
        return summary ? [{ id: `${name || 'knowledge'}-${index}`, name, summary }] : [];
      });
    }
    if (!isRecord(value)) return [{ id: 'knowledge', name: '', summary: String(value) }];
    return Object.entries(value).flatMap(([name, facts], index) => {
      const summaries = readableList(facts);
      if (!summaries.length && typeof facts === 'string') summaries.push(facts);
      return summaries.map((summary, itemIndex) => ({
        id: `${name}-${index}-${itemIndex}`,
        name: label(name),
        summary
      }));
    });
  }

  function privateEventMap(plan) {
    const events = list(plan?.events || plan?.private_events);
    return new Map(events.filter(isRecord).map((event) => [
      String(event.scene_id || event.id || ''), event
    ]).filter(([id]) => id));
  }

  function normaliseScene(raw, index, privateByScene) {
    const scene = isRecord(raw) ? raw : { title: String(raw || '') };
    const id = firstText(scene.id, scene.scene_id, scene.source_event_id, `scene-${index}`);
    const privateEvent = privateByScene.get(id) || {};
    // The compiler gives an invalid legacy scene a fallback slot to keep its
    // contract inspectable. The author-only preview tells us whether that slot
    // was actually authored, so the board never presents a guessed time as a
    // real schedule.
    const scheduleState = firstText(
      scene.schedule_state, scene.scheduleState,
      scene.time_explicit === false ? 'unplaced' : ''
    ) || 'scheduled';
    const timeExplicit = scene.time_explicit !== false && scheduleState === 'scheduled';
    const slots = timeExplicit
      ? normaliseSlots(scene.slots || scene.slot || scene.when || privateEvent.slots)
      : [];
    const publicSurface = firstText(
      scene.public_surface, scene.public, scene.visible, scene.surface,
      scene.summary, scene.description, scene.event
    );
    const privateLogic = firstText(
      scene.private_logic, scene.private, scene.hidden, scene.director_note,
      scene.director_notes, privateEvent.hidden, privateEvent.private_logic,
      privateEvent.note
    );
    const locationId = firstText(scene.location, scene.place);
    return {
      id,
      title: firstText(scene.title, scene.name, scene.label, scene.event, publicSurface, label(id)),
      slots,
      locationId,
      location: label(firstText(scene.location_label, locationId)),
      participants: readableList(scene.participants || scene.present || scene.cast).map(label),
      publicSurface,
      privateLogic,
      trigger: firstText(scene.trigger, privateEvent.trigger),
      evidence: readableList(scene.evidence || privateEvent.evidence),
      requires: gateList(scene.requires || scene.gates || scene.conditions),
      knowledge: knowledgeEntries(scene.knowledge || scene.knowledge_summary || privateEvent.knowledge),
      entityPeriods: readableList(scene.entity_period_ids || scene.entity_period || privateEvent.entity_period_ids || privateEvent.entity_period),
      entityAction: Boolean(scene.entity_action ?? privateEvent.entity_action),
      scheduleState,
      timeExplicit
    };
  }

  function openingScene(raw) {
    const opening = isRecord(raw) ? raw : {};
    const state = isRecord(opening.state) ? opening.state : {};
    const slot = firstText(state.time);
    const location = firstText(opening.location_label, state.location);
    const participants = readableList(state.present);
    if (!slot && !location && !participants.length) return null;
    return {
      id: 'opening-state',
      title: 'Opening position',
      slots: normaliseSlots(slot),
      locationId: location,
      location,
      participants,
      publicSurface: location ? `The story begins at ${location}.` : 'The story begins from this authored state.',
      privateLogic: '',
      trigger: '',
      evidence: [],
      requires: [],
      knowledge: [],
      entityPeriods: [],
      entityAction: false,
      scheduleState: 'scheduled',
      timeExplicit: true
    };
  }

  function scenesFromTimeline(value) {
    if (Array.isArray(value)) {
      if (value.some((item) => isRecord(item) && Array.isArray(item.scenes))) {
        return value.flatMap((lane) => list(lane.scenes).map((scene) => ({
          ...(isRecord(scene) ? scene : { title: String(scene || '') }),
          slots: scene?.slots || lane.slot || lane.id || lane.when
        })));
      }
      return value;
    }
    if (!isRecord(value)) return [];
    return Object.entries(value).flatMap(([slot, scenes]) => list(scenes).map((scene) => ({
      ...(isRecord(scene) ? scene : { title: String(scene || '') }),
      slots: scene?.slots || slot
    })));
  }

  function normalisePeriod(raw, index) {
    const period = isRecord(raw) ? raw : { id: `period-${index}` };
    return {
      id: firstText(period.id, period.name, `period-${index}`),
      slots: normaliseSlots(period.slots || period.slot || period.when),
      state: firstText(period.state, period.mode, 'active'),
      capabilities: readableList(period.capabilities || period.can),
      constraint: firstText(period.constraint, period.limit, period.rule)
    };
  }

  function normaliseLocations(value) {
    const seen = new Set();
    return list(value).flatMap((raw) => {
      if (!isRecord(raw)) return [];
      const id = firstText(raw.id, raw.key, raw.slug);
      if (!id || seen.has(id)) return [];
      seen.add(id);
      return [{
        id,
        label: firstText(raw.name, raw.label, raw.title, id),
        description: firstText(raw.description, raw.summary)
      }];
    });
  }

  function normaliseLoop(raw) {
    const policy = isRecord(raw) ? raw : {};
    const reset = isRecord(policy.reset) ? policy.reset : {};
    const memory = isRecord(reset.memory) ? reset.memory : {};
    const restart = policy.restart;
    const restartText = typeof restart === 'string'
      ? restart
      : firstText(restart?.summary, restart?.location && restart?.time
        ? `${label(restart.time)} at ${label(restart.location)}` : '', restart?.location, restart?.time);
    return {
      enabled: Boolean(policy.enabled),
      executable: policy.executable !== false,
      trigger: firstText(policy.trigger, policy.reset_on),
      restart: restartText,
      preserves: readableList(reset.preserve_runtime || policy.preserves || policy.persist),
      remembers: readableList(memory.preserve_for || policy.memory || policy.remembers),
      clearOthers: Boolean(memory.clear_for_others),
      endCondition: firstText(policy.end_condition, policy.end),
      victims: policy.victims_return_after_end
    };
  }

  function normaliseReadiness(raw) {
    const candidate = isRecord(raw?.readiness) ? raw.readiness : raw || {};
    const issues = list(candidate.issues || raw?.issues).filter(isRecord);
    const blockers = list(candidate.blockers).filter(isRecord);
    const warnings = list(candidate.warnings).filter(isRecord);
    const compact = (items) => {
      const grouped = new Map();
      for (const issue of items) {
        const key = `${issue.section || ''}:${issue.code || ''}:${issue.message || ''}`;
        const prior = grouped.get(key);
        if (prior) prior.count += 1;
        else grouped.set(key, { ...issue, count: 1 });
      }
      return [...grouped.values()];
    };
    return {
      ready: typeof candidate.ready === 'boolean' ? candidate.ready : !issues.some((issue) => issue.severity === 'error'),
      blockers: compact(blockers.length ? blockers : issues.filter((issue) => issue.severity === 'error')),
      warnings: compact(warnings.length ? warnings : issues.filter((issue) => issue.severity && issue.severity !== 'error'))
    };
  }

  function displayName(value) {
    const text = String(value || '').trim();
    return /[_-]/.test(text) ? label(text) : text;
  }

  function normaliseTheme(raw, source) {
    const theme = isRecord(raw) ? raw : {};
    const tags = Array.isArray(raw) ? readableList(raw)
      : readableList(theme.tags || theme.themes || source?.themes || source?.theme_tags);
    return {
      statement: typeof raw === 'string'
        ? raw.trim()
        : firstText(theme.statement, theme.label, theme.theme, theme.central_question, theme.summary,
          source?.theme_statement, source?.theme_text),
      dramaticQuestion: firstText(theme.dramatic_question, theme.question,
        source?.dramatic_question, source?.theme_question),
      pressure: firstText(theme.pressure, theme.story_pressure,
        source?.theme_pressure, source?.pressure),
      tags
    };
  }

  function normaliseArc(raw, index) {
    const record = isRecord(raw) ? raw : {};
    const embedded = isRecord(record.arc) ? record.arc : {};
    const detail = { ...record, ...embedded };
    const owner = firstText(detail.character, detail.owner, detail.person, detail.character_name,
      detail.character_key, detail.name);
    // An arc record's `name` is often the arc title, while its `owner` is the
    // person whose change it tracks. Prefer the person whenever one is named.
    const name = firstText(detail.character, detail.owner, detail.person, detail.character_name, detail.character_key,
      detail.name, detail.title, `Character ${index + 1}`);
    return {
      id: firstText(detail.id, detail.thread_id, detail.character_id, detail.character_key, detail.key, owner, `character-arc-${index}`),
      name: displayName(name),
      owner: displayName(owner),
      promise: firstText(detail.arc_promise, detail.promise, detail.promise_of_change,
        detail.arc_statement, detail.arc_summary, detail.dramatic_question, detail.title),
      externalWant: firstText(detail.external_want, detail.want, detail.goal),
      blindSpot: firstText(detail.blind_spot, detail.lie, detail.misbelief,
        detail.false_belief, detail.self_deception),
      protectivePattern: firstText(detail.protective_pattern, detail.defense, detail.coping_pattern),
      need: firstText(detail.unacknowledged_need, detail.need, detail.truth_to_earn,
        detail.true_need),
      truth: firstText(detail.truth_to_earn, detail.truth, detail.realisation),
      stakes: firstText(detail.stakes_of_avoidance, detail.cost_of_avoidance, detail.stakes),
      pressure: firstText(detail.current_pressure, detail.pressure, detail.current_conflict,
        detail.test, detail.pressure_points?.[0]?.public_pressure),
      turn: firstText(detail.possible_turn, detail.turn, detail.next_turn, detail.inflection,
        detail.turning_points?.[0]?.public_surface, detail.possible_outcomes?.[0]),
      relationships: readableList(detail.relationship_targets || detail.relationships || detail.targets),
      hasDetail: Object.keys(detail).some((key) => !['id', 'name', 'owner', 'character', 'character_name', 'key'].includes(key)
        && Boolean(detail[key]))
    };
  }

  function mergeArc(first, second) {
    const preferred = first.hasDetail ? first : second;
    const other = preferred === first ? second : first;
    const value = (key) => preferred[key] || other[key] || '';
    return {
      id: preferred.id || other.id,
      name: value('name'),
      owner: value('owner'),
      promise: value('promise'),
      externalWant: value('externalWant'),
      blindSpot: value('blindSpot'),
      protectivePattern: value('protectivePattern'),
      need: value('need'),
      truth: value('truth'),
      stakes: value('stakes'),
      pressure: value('pressure'),
      turn: value('turn'),
      relationships: preferred.relationships.length ? preferred.relationships : other.relationships,
      hasDetail: first.hasDetail || second.hasDetail
    };
  }

  function threadsFromArcs(value) {
    return list(value).filter(isRecord).flatMap((parent, parentIndex) => {
      const threads = list(parent.character_threads || parent.threads).filter(isRecord);
      if (!threads.length) return [parent];
      return threads.map((thread, threadIndex) => ({
        ...thread,
        id: thread.id || thread.thread_id || `${parent.id || parentIndex}-thread-${threadIndex}`,
        // Carry the arc's promise and pressure down to each person without
        // clobbering the character record's own name or more precise turn.
         arc_promise: thread.arc_promise || parent.arc_promise || parent.promise,
         external_want: thread.external_want || parent.external_want || parent.want,
         current_pressure: thread.current_pressure || parent.current_pressure || parent.pressure
          || parent.turning_point_pressure || thread.pressure_points?.[0]?.public_pressure,
         possible_turn: thread.possible_turn || parent.possible_turn || parent.turn
          || parent.turning_point || parent.turning_points?.[0]?.public_surface
          || thread.possible_outcomes?.[0],
         relationship_targets: thread.relationship_targets || parent.relationship_targets
          || parent.relationships || parent.pressures,
        character: thread.character || thread.owner || thread.person || parent.owner || parent.character,
        arc_name: parent.name || parent.title,
        arc_id: parent.id
      }));
    });
  }

  function normaliseArcDesign(raw, contract) {
    const candidate = isRecord(raw.arc_design) ? raw.arc_design
      : isRecord(contract.arc_design) ? contract.arc_design
        : isRecord(raw.theme_arc) ? raw.theme_arc
          : isRecord(contract.theme_arc) ? contract.theme_arc
            : isRecord(raw.character_arcs) ? raw.character_arcs
              : isRecord(contract.character_arcs) ? contract.character_arcs : {};
    const canonicalThemes = list(candidate.themes).filter(isRecord);
    const theme = normaliseTheme(canonicalThemes[0] || candidate.theme || raw.theme || contract.theme, candidate);
    const records = [
      ...threadsFromArcs(candidate.arcs),
      ...list(candidate.characters),
      ...list(candidate.character_arcs),
      ...list(candidate.character_threads),
      ...threadsFromArcs(raw.arcs),
      ...list(raw.character_arcs),
      ...list(raw.character_threads),
      ...threadsFromArcs(contract.arcs),
      ...list(contract.character_arcs),
      ...list(contract.character_threads)
    ].filter(isRecord);
    const arcs = records.map(normaliseArc).filter((arc) => arc.name || arc.owner || arc.hasDetail);
    const byPerson = new Map();
    for (const arc of arcs) {
      const key = (arc.owner || arc.name || arc.id).toLowerCase().replace(/[^a-z0-9]+/g, '');
      byPerson.set(key, byPerson.has(key) ? mergeArc(byPerson.get(key), arc) : arc);
    }
    const merged = [...byPerson.values()];
    return {
      theme,
      arcs: merged,
      hasData: Boolean(theme.statement || theme.dramaticQuestion || theme.pressure || theme.tags.length || merged.length)
    };
  }

  function normalisePreview(raw) {
    if (!isRecord(raw)) return null;
    const contract = isRecord(raw.contract) ? raw.contract
      : isRecord(raw.scenario) ? raw.scenario
        : isRecord(raw.preview) ? raw.preview : raw;
    const directorPlan = isRecord(contract.director_plan) ? contract.director_plan
      : isRecord(raw.director_plan) ? raw.director_plan : {};
    const sourceTimeline = raw.timeline || contract.timeline || contract.scene_catalog || raw.scene_catalog || [];
    const privateByScene = privateEventMap(directorPlan);
    const scenes = scenesFromTimeline(sourceTimeline)
      .map((scene, index) => normaliseScene(scene, index, privateByScene));
    const opening = openingScene(raw.opening || contract.opening);
    const openingMatch = scenes.find((scene) => scene.id === (raw.opening || contract.opening)?.scene_id);
    // The compiler can recover an opening state from its first event even when
    // that event has no authored time gate. Keep the recovered starting point
    // visible, but leave the actual event unplaced instead of pretending it is
    // scheduled in every part of the day.
    if (opening && (!openingMatch || !openingMatch.slots.length)) scenes.unshift(opening);
    const slotCandidates = normaliseSlots(raw.time_slots || raw.slots || contract.time_slots || contract.slots || raw.time_system?.slots || contract.time_system?.slots);
    const slots = [...new Set([...DEFAULT_SLOTS, ...slotCandidates, ...scenes.flatMap((scene) => scene.slots)])];
    const entityPeriods = list(raw.entity_periods || contract.entity_periods || directorPlan.entity_periods || raw.time_system?.entity_periods)
      .map(normalisePeriod);
    const locations = normaliseLocations(raw.locations || contract.locations);
    const knowledge = knowledgeEntries(raw.knowledge || contract.knowledge || raw.knowledge_summaries)
      .concat(scenes.flatMap((scene) => scene.knowledge));
    const distinctKnowledge = knowledge.filter((entry, index, entries) =>
      entries.findIndex((candidate) => candidate.name === entry.name && candidate.summary === entry.summary) === index);
    return {
      objective: firstText(raw.objective, contract.objective, directorPlan.objective),
      slots: [
        ...slots.map((slot) => ({
        id: slot,
        label: label(slot),
        scenes: scenes.filter((scene) => scene.slots.includes(slot)),
        periods: entityPeriods.filter((period) => period.slots.includes(slot))
        })),
        ...(scenes.some((scene) => !scene.slots.length)
          ? [{
              id: 'unplaced', label: 'Unplaced',
              scenes: scenes.filter((scene) => !scene.slots.length), periods: []
            }]
          : [])
      ],
      locations,
      entityPeriods,
      knowledge: distinctKnowledge,
      loop: normaliseLoop(raw.loop_policy || contract.loop_policy || raw.loop || contract.loop),
      readiness: normaliseReadiness(raw.readiness ? raw : contract),
      arcDesign: normaliseArcDesign(raw, contract)
    };
  }

  async function load() {
    if (!storyKey) return;
    const currentRequest = ++requestId;
    loading = true;
    error = '';
    try {
      const response = await fetch(`/api/stories/${encodeURIComponent(storyKey)}/director-preview`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || 'Could not load the director preview.');
      if (currentRequest === requestId) preview = data;
    } catch (cause) {
      if (currentRequest === requestId) {
        preview = null;
        error = cause?.message || 'Could not load the director preview.';
      }
    } finally {
      if (currentRequest === requestId) loading = false;
    }
  }

  function choose(item) {
    if (typeof onselect === 'function') onselect(item);
  }

  /*
   * The director is a view over several story-card sections.  Expose a
   * single, predictable target shape from every interactive detail so the
   * caller can create a dedicated edit conversation without having to infer
   * intent from DOM labels.  The legacy `kind` values stay intact on root
   * controls; `field` adds precision for the new per-detail controls.
   */
  function editorialTarget(section, itemKind, id, field = '') {
    return {
      section,
      item: { kind: itemKind, id: String(id || '') },
      ...(field ? { field } : {})
    };
  }

  function selection({ kind, section, itemKind, id, field = '', title = '', summary = '', ...detail }) {
    return {
      kind,
      id: String(id || ''),
      section,
      field,
      target: editorialTarget(section, itemKind, id, field),
      label: title,
      summary,
      ...detail
    };
  }

  function themeSelection(field = '', tag = '') {
    const summary = field === 'dramatic_question'
      ? board?.arcDesign?.theme?.dramaticQuestion
      : field === 'pressure'
        ? board?.arcDesign?.theme?.pressure
        : field === 'tag'
          ? tag
          : board?.arcDesign?.theme?.statement;
    return selection({
      kind: field ? 'theme-field' : 'theme',
      section: 'arcs',
      itemKind: 'theme',
      id: field === 'tag' ? `theme-tag-${tag}` : 'theme',
      field,
      title: field ? `Theme — ${label(field)}` : 'Theme',
      summary
    });
  }

  function arcSelection(arc, field = '') {
    const fields = {
      promise: arc.promise,
      external_want: arc.externalWant,
      blind_spot: arc.blindSpot,
      need: arc.need,
      pressure: arc.pressure,
      turn: arc.turn,
      protective_pattern: arc.protectivePattern,
      truth: arc.truth,
      stakes: arc.stakes,
      relationships: arc.relationships?.join(' · ')
    };
    return selection({
      kind: field ? 'character-arc-field' : 'character-arc',
      section: 'arcs',
      itemKind: 'arc',
      id: arc.id,
      field,
      title: field ? `${arc.name || arc.owner} — ${label(field)}` : (arc.name || arc.owner),
      summary: fields[field] || arc.promise || arc.pressure || arc.name,
      arc
    });
  }

  function loopSelection(field = '') {
    const fields = {
      trigger: board?.loop?.trigger,
      restart: board?.loop?.restart,
      preserves: board?.loop?.preserves?.join(' · '),
      remembers: board?.loop?.remembers?.join(' · '),
      clear_others: board?.loop?.clearOthers ? 'Everyone else forgets the loop.' : '',
      end_condition: board?.loop?.endCondition
    };
    return selection({
      kind: field ? 'loop-field' : 'loop',
      section: 'world',
      itemKind: 'world_rule',
      id: 'loop',
      field,
      title: field ? `Loop policy — ${label(field)}` : 'Loop policy',
      summary: fields[field] || (board?.loop?.enabled ? 'The story uses an executable reset.' : 'No reset loop is set.')
    });
  }

  function periodSelection(period, field = '') {
    const fields = {
      slots: period.slots?.map(label).join(' · '),
      state: period.state,
      capabilities: period.capabilities?.join(' · '),
      constraint: period.constraint
    };
    return selection({
      kind: field ? 'entity-period-field' : 'entity-period',
      section: 'time_system',
      itemKind: 'entity_period',
      id: period.id,
      field,
      title: field ? `${label(period.id)} — ${label(field)}` : label(period.id),
      summary: fields[field] || `${period.slots?.map(label).join(' · ') || 'No time window'} — ${period.state}`,
      period
    });
  }

  function knowledgeSelection(item) {
    return selection({
      kind: 'knowledge',
      section: 'cast',
      itemKind: 'knowledge',
      id: item.id,
      title: item.name || 'Scene knowledge',
      summary: item.summary,
      knowledge: item
    });
  }

  function readinessSection(issue) {
    const source = String(issue?.section || issue?.path || '').toLowerCase();
    if (/(scene|event|opening|day|timeline|director_plan)/.test(source)) return 'first_day';
    if (/(time|period|schedule|entity)/.test(source)) return 'time_system';
    if (/(arc|theme|character_thread)/.test(source)) return 'arcs';
    if (/(cast|character|relationship|bond)/.test(source)) return 'cast';
    return 'world';
  }

  function readinessSelection(issue, index, severity) {
    const id = firstText(issue?.id, issue?.code, issue?.path, `readiness-${severity}-${index}`);
    const section = readinessSection(issue);
    return selection({
      kind: 'readiness',
      section,
      itemKind: 'readiness',
      id,
      field: issue?.path || '',
      title: label(issue?.section || issue?.path?.split('.')?.[0] || 'Story readiness'),
      summary: issue?.message || issue?.fix || 'This part of the story needs a concrete decision.',
      issue
    });
  }

  async function reconcileArchitectSceneMove(scene, before = {}) {
    if (!storyKey || !scene?.id) return;
    try {
      const response = await fetch(`/api/stories/${encodeURIComponent(storyKey)}/architect/turn`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event: {
            type: 'scene_moved',
            scene_id: scene.id,
            // The reconciler reads the new placement from the persisted card.
            // It only needs the old slot to know whether a linked arc time was
            // a mirror or an intentional dramatic offset.
            before: {
              slot: before.slot || '',
              location: before.location || ''
            }
          }
        })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return;
      // This optional, model-free pass never holds up drag/drop.  It can only
      // sync a derived arc-time mirror or leave a protected question for the
      // primary Architect; it does not rewrite the scene the author moved.
      if (data?.story && typeof onstorychange === 'function') onstorychange(data.story);
      if (typeof onarchitect === 'function') onarchitect(data);
    } catch {
      // The direct placement has already persisted.  Reconciliation can be
      // recovered by the Architect's normal persisted-state bootstrap, so an
      // unavailable optional follow-up must never turn a successful drag into
      // a user-visible failure.
    }
  }

  async function scheduleScene({ scene, slot }) {
    if (!storyKey || !scene?.id || slot === undefined || scheduling) return;
    scheduling = true;
    error = '';
    try {
      const response = await fetch(
        `/api/stories/${encodeURIComponent(storyKey)}/director/scenes/${encodeURIComponent(scene.id)}/schedule`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ slot })
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || 'Could not schedule this scene.');
      const before = { slot: scene.slots?.[0] || '', location: scene.locationId || '' };
      preview = data.preview || data.director_preview || data;
      if (data?.story && typeof onstorychange === 'function') onstorychange(data.story);
      void reconcileArchitectSceneMove(scene, before);
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } catch (cause) {
      error = cause?.message || 'Could not schedule this scene.';
    } finally {
      scheduling = false;
    }
  }

  async function moveSceneLocation({ scene, location }) {
    if (!storyKey || !scene?.id || !location || scheduling) return;
    scheduling = true;
    error = '';
    try {
      const response = await fetch(
        `/api/stories/${encodeURIComponent(storyKey)}/director/scenes/${encodeURIComponent(scene.id)}/placement`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ location })
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data?.error || 'Could not move this scene to that location.');
      const before = { slot: scene.slots?.[0] || '', location: scene.locationId || '' };
      preview = data.preview || data.director_preview || data;
      if (data?.story && typeof onstorychange === 'function') onstorychange(data.story);
      void reconcileArchitectSceneMove(scene, before);
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } catch (cause) {
      error = cause?.message || 'Could not move this scene to that location.';
    } finally {
      scheduling = false;
    }
  }

  $effect(() => {
    if (storyKey) load();
  });

  $effect(() => {
    if (typeof window === 'undefined') return;
    const refresh = (event) => {
      if (!event?.detail?.key || event.detail.key === storyKey) load();
    };
    window.addEventListener('story:refresh', refresh);
    return () => window.removeEventListener('story:refresh', refresh);
  });
</script>

<section class="director-board" aria-label="Director board">
  <header class="board-head">
    <div>
      <span class="eyebrow">Author only</span>
      <h2>Director</h2>
      <p>Plan what can happen without leaking the machinery into the narrator’s card.</p>
    </div>
    <button class="refresh" type="button" onclick={load} disabled={loading}>
      {loading ? 'Refreshing…' : 'Refresh'}
    </button>
  </header>

  {#if error}
    <div class="load-error" role="alert">
      <strong>Director preview unavailable</strong>
      <span>{error}</span>
      <button type="button" onclick={load}>Try again</button>
    </div>
  {:else if loading && !board}
    <p class="loading">Building the author-only preview…</p>
  {:else if board}
    <section class:ready={board.readiness.ready} class="readiness" aria-label="Play readiness">
      <div class="readiness-title">
        <span class="status-dot"></span>
        <div>
          <span class="eyebrow">Runtime readiness</span>
          <strong>{board.readiness.ready ? 'Ready to run' : `${board.readiness.blockers.length} decision${board.readiness.blockers.length === 1 ? '' : 's'} still block play`}</strong>
        </div>
      </div>
      {#if board.readiness.blockers.length || board.readiness.warnings.length}
        <div class="readiness-list">
          {#each board.readiness.blockers as issue, index (`block-${issue.code || issue.path || 'issue'}-${index}`)}
            <button type="button" class="issue blocker" onclick={() => choose(readinessSelection(issue, index, 'blocker'))}>
              <b>{label(issue.section || issue.path?.split('.')?.[0] || 'Needs a decision')}</b>
              <span>{issue.message || 'This part of the plan needs a concrete rule.'}{#if issue.count > 1} <em>({issue.count} scenes)</em>{/if}</span>
              {#if issue.fix}<small>{issue.fix}</small>{/if}
            </button>
          {/each}
          {#each board.readiness.warnings as issue, index (`warn-${issue.code || issue.path || 'issue'}-${index}`)}
            <button type="button" class="issue warning" onclick={() => choose(readinessSelection(issue, index, 'warning'))}>
              <b>{label(issue.section || issue.path?.split('.')?.[0] || 'Worth deciding')}</b>
              <span>{issue.message || 'This detail is still open.'}{#if issue.count > 1} <em>({issue.count} scenes)</em>{/if}</span>
            </button>
          {/each}
        </div>
      {/if}
    </section>

    <DirectorTimeline lanes={board.slots} locations={board.locations} onselect={choose} onschedule={scheduleScene} onlocation={moveSceneLocation} {scheduling} />

    <section class:empty={!board.arcDesign.hasData} class="arc-lens" aria-label="Theme and character arcs">
      <div class="section-head arc-head">
        <button type="button" class="section-intro" onclick={() => choose(themeSelection())}>
          <span class="eyebrow">Character pressure</span>
          <h3>Theme and possible turns</h3>
          <p>These are authorial pressures, not facts the narrator should announce.</p>
        </button>
      </div>
      <div class="theme-frame">
        <button type="button" class="theme-label eyebrow" onclick={() => choose(themeSelection())}>Theme</button>
        <div>
          <button type="button" class="theme-statement" onclick={() => choose(themeSelection('statement'))}>
            <strong>{board.arcDesign.theme.statement || 'No thematic statement has been distilled yet.'}</strong>
          </button>
          {#if board.arcDesign.theme.dramaticQuestion}<button type="button" class="theme-field" onclick={() => choose(themeSelection('dramatic_question'))}><b>The question</b><span>{board.arcDesign.theme.dramaticQuestion}</span></button>{/if}
          {#if board.arcDesign.theme.pressure}<button type="button" class="theme-field" onclick={() => choose(themeSelection('pressure'))}><b>The pressure</b><span>{board.arcDesign.theme.pressure}</span></button>{/if}
          {#if board.arcDesign.theme.tags.length}<div class="theme-tags">{#each board.arcDesign.theme.tags as tag (tag)}<button type="button" onclick={() => choose(themeSelection('tag', tag))}>{tag}</button>{/each}</div>{/if}
        </div>
      </div>
      {#if board.arcDesign.arcs.length}
        <div class="arc-cards">
          {#each board.arcDesign.arcs as arc (arc.id)}
            <article class="arc-card">
              <button type="button" class="arc-root" onclick={() => choose(arcSelection(arc))}>
                <div class="arc-card-head">
                <div><span class="eyebrow">Character</span><strong>{arc.name || arc.owner}</strong></div>
                </div>
              </button>
              {#if arc.relationships.length}<button type="button" class="arc-targets" onclick={() => choose(arcSelection(arc, 'relationships'))}>Against / with {arc.relationships.join(' · ')}</button>{/if}
              {#if arc.promise || arc.externalWant}
                <div class="arc-promise">
                  {#if arc.promise}<button type="button" onclick={() => choose(arcSelection(arc, 'promise'))}><b>Arc promise</b><span>{arc.promise}</span></button>{/if}
                  {#if arc.externalWant}<button type="button" onclick={() => choose(arcSelection(arc, 'external_want'))}><b>Wants</b><span>{arc.externalWant}</span></button>{/if}
                </div>
              {/if}
              <div class="arc-fields">
                <button type="button" onclick={() => choose(arcSelection(arc, 'blind_spot'))}><b>Blind spot</b><em class:missing={!arc.blindSpot}>{arc.blindSpot || 'Not written'}</em></button>
                <button type="button" onclick={() => choose(arcSelection(arc, 'need'))}><b>Unacknowledged need</b><em class:missing={!arc.need}>{arc.need || 'Not written'}</em></button>
                <button type="button" onclick={() => choose(arcSelection(arc, 'pressure'))}><b>Current pressure</b><em class:missing={!arc.pressure}>{arc.pressure || 'Not written'}</em></button>
                <button type="button" onclick={() => choose(arcSelection(arc, 'turn'))}><b>Possible turn</b><em class:missing={!arc.turn}>{arc.turn || 'Not written'}</em></button>
              </div>
              {#if arc.protectivePattern || arc.truth || arc.stakes}
                <div class="arc-undercurrent">
                  {#if arc.protectivePattern}<button type="button" onclick={() => choose(arcSelection(arc, 'protective_pattern'))}><b>Protects themself by</b><span>{arc.protectivePattern}</span></button>{/if}
                  {#if arc.truth}<button type="button" onclick={() => choose(arcSelection(arc, 'truth'))}><b>Truth to earn</b><span>{arc.truth}</span></button>{/if}
                  {#if arc.stakes}<button type="button" onclick={() => choose(arcSelection(arc, 'stakes'))}><b>Cost of avoiding it</b><span>{arc.stakes}</span></button>{/if}
                </div>
              {/if}
            </article>
          {/each}
        </div>
      {:else}
        <p class="arc-empty">No character arc is authored yet. Give each person a pressure and a possible turn before asking scenes to carry their change.</p>
      {/if}
    </section>

    <div class="supporting-grid">
      <section class="support-card loop-card" aria-label="Loop policy">
        <span class="eyebrow">Loop policy</span>
        {#if board.loop.enabled}
          <button type="button" class="support-heading" onclick={() => choose(loopSelection())}><h3>{board.loop.executable ? 'Executable reset' : 'Reset still needs a rule'}</h3></button>
          <div class="support-fields">
            {#if board.loop.trigger}<button type="button" onclick={() => choose(loopSelection('trigger'))}><b>Trigger</b><span>{board.loop.trigger}</span></button>{/if}
            {#if board.loop.restart}<button type="button" onclick={() => choose(loopSelection('restart'))}><b>Returns to</b><span>{board.loop.restart}</span></button>{/if}
            {#if board.loop.preserves.length}<button type="button" onclick={() => choose(loopSelection('preserves'))}><b>State retained</b><span>{board.loop.preserves.join(' · ')}</span></button>{/if}
            {#if board.loop.remembers.length}<button type="button" onclick={() => choose(loopSelection('remembers'))}><b>Memory retained</b><span>{board.loop.remembers.join(' · ')}</span></button>{/if}
            {#if board.loop.clearOthers}<button type="button" onclick={() => choose(loopSelection('clear_others'))}><b>Everyone else</b><span>Forgets the loop.</span></button>{/if}
            {#if board.loop.endCondition}<button type="button" onclick={() => choose(loopSelection('end_condition'))}><b>Ends when</b><span>{board.loop.endCondition}</span></button>{/if}
          </div>
        {:else}
          <button type="button" class="support-heading" onclick={() => choose(loopSelection())}><h3>No reset loop</h3></button>
          <button type="button" class="support-empty" onclick={() => choose(loopSelection())}>This story has no loop policy to enforce.</button>
        {/if}
      </section>

      <section class="support-card entity-card" aria-label="Entity periods">
        <span class="eyebrow">Entity activity</span>
        <button type="button" class="support-heading" onclick={() => choose(selection({ kind: 'entity-periods', section: 'time_system', itemKind: 'entity_period', id: 'entity-periods', title: 'Entity activity', summary: board.entityPeriods.length ? 'Allowed entity windows.' : 'No special activity windows.' }))}><h3>{board.entityPeriods.length ? 'Allowed windows' : 'No special windows'}</h3></button>
        {#if board.entityPeriods.length}
          <ul class="period-summary">
            {#each board.entityPeriods as period (period.id)}
              <li>
                <button type="button" class="period-summary-open" onclick={() => choose(periodSelection(period))}>
                  <b>{label(period.id)}</b>
                  <span>{period.slots.map(label).join(' · ')} — {period.state}</span>
                </button>
                {#if period.capabilities.length}<button type="button" class="period-detail" onclick={() => choose(periodSelection(period, 'capabilities'))}>Can: {period.capabilities.join(' · ')}</button>{/if}
                {#if period.constraint}<button type="button" class="period-detail" onclick={() => choose(periodSelection(period, 'constraint'))}>Bound by: {period.constraint}</button>{/if}
              </li>
            {/each}
          </ul>
        {:else}
          <button type="button" class="support-empty" onclick={() => choose(selection({ kind: 'entity-periods', section: 'time_system', itemKind: 'entity_period', id: 'entity-periods', title: 'Entity activity', summary: 'Declare a window only when an entity needs a hard time constraint.' }))}>Declare a window only when an entity needs a hard time constraint.</button>
        {/if}
      </section>

      <section class="support-card knowledge-card" aria-label="Knowledge lens">
        <span class="eyebrow">Knowledge lens</span>
        <button type="button" class="support-heading" onclick={() => choose(selection({ kind: 'knowledge', section: 'cast', itemKind: 'knowledge', id: 'knowledge', title: 'Knowledge lens', summary: 'What individual characters and scenes can carry forward.' }))}><h3>Who can carry what forward</h3></button>
        {#if board.knowledge.length}
          <ul class="knowledge-list">
            {#each board.knowledge as item (item.id)}
              <li><button type="button" class="knowledge-open" onclick={() => choose(knowledgeSelection(item))}><b>{item.name || 'Scene knowledge'}</b><span>{item.summary}</span></button></li>
            {/each}
          </ul>
        {:else}
          <button type="button" class="support-empty" onclick={() => choose(selection({ kind: 'knowledge', section: 'cast', itemKind: 'knowledge', id: 'knowledge', title: 'Knowledge lens', summary: 'No private knowledge has been assigned to a character or scene yet.' }))}>No private knowledge has been assigned to a character or scene yet.</button>
        {/if}
      </section>
    </div>
  {/if}
</section>

<style>
  .director-board { display: grid; gap: 18px; min-width: 0; color: var(--text); }
  .board-head, .section-head, .readiness-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
  .eyebrow { display: block; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; }
  h2, h3, p { margin: 0; } h2 { margin-top: 2px; font-size: clamp(25px, 3vw, 34px); letter-spacing: -.035em; } h3 { margin-top: 3px; font-size: 16px; letter-spacing: -.015em; }
  .board-head p, .section-head p { margin-top: 5px; color: var(--muted); font-size: 12.5px; line-height: 1.45; }
  .refresh { flex: none; padding: 6px 9px; background: transparent; color: var(--muted); font-size: 11px; }
  .loading { color: var(--muted); font-size: 13px; }
  .load-error { display: grid; grid-template-columns: auto 1fr auto; gap: 8px 12px; align-items: center; padding: 12px; border: 1px solid color-mix(in srgb, var(--bad) 42%, var(--border)); background: color-mix(in srgb, var(--bad) 8%, var(--panel)); color: var(--muted); font-size: 12px; }
  .load-error strong { color: var(--bad); } .load-error button { padding: 5px 8px; font-size: 11px; }

  .readiness { padding: 13px 14px; border: 1px solid color-mix(in srgb, var(--warn) 38%, var(--border)); background: color-mix(in srgb, var(--warn) 6%, var(--panel)); }
  .readiness.ready { border-color: color-mix(in srgb, var(--good) 45%, var(--border)); background: color-mix(in srgb, var(--good) 7%, var(--panel)); }
  .readiness-title { justify-content: flex-start; align-items: center; } .readiness-title strong { display: block; margin-top: 2px; font-size: 14px; }
  .status-dot { width: 9px; height: 9px; flex: none; border-radius: 50%; background: var(--warn); box-shadow: 0 0 0 4px color-mix(in srgb, var(--warn) 13%, transparent); } .readiness.ready .status-dot { background: var(--good); box-shadow: 0 0 0 4px color-mix(in srgb, var(--good) 13%, transparent); }
  .readiness-list { display: grid; gap: 5px; margin-top: 11px; } .issue { display: grid; gap: 2px; width: 100%; padding: 8px 9px; border: 1px solid var(--border-soft); border-radius: 7px; background: color-mix(in srgb, var(--panel) 74%, transparent); text-align: left; color: var(--muted); font-size: 11.5px; line-height: 1.35; }
  .issue:hover { border-color: var(--border-strong); } .issue b { color: var(--warn); font-size: 9px; letter-spacing: .06em; text-transform: uppercase; } .issue.warning b { color: var(--muted); } .issue em { color: var(--faint); font-size: 10px; font-style: normal; white-space: nowrap; } .issue small { color: var(--faint); font-size: 10.5px; }


  .arc-lens { display: grid; gap: 10px; padding: 13px; border: 1px solid color-mix(in srgb, #72a6e8 32%, var(--border)); background: color-mix(in srgb, #72a6e8 5%, var(--panel)); } .arc-lens.empty { border-style: dashed; } .arc-head { align-items: end; }
  .section-intro { display: grid; gap: 0; max-width: 620px; padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .section-intro:hover h3 { color: #c9ddff; } .section-intro:focus-visible, .theme-label:focus-visible, .theme-statement:focus-visible, .theme-field:focus-visible, .theme-tags button:focus-visible, .arc-root:focus-visible, .arc-targets:focus-visible, .arc-promise button:focus-visible, .arc-fields button:focus-visible, .arc-undercurrent button:focus-visible, .support-heading:focus-visible, .support-fields button:focus-visible, .period-summary button:focus-visible, .knowledge-open:focus-visible, .support-empty:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .theme-frame { display: grid; grid-template-columns: minmax(84px, .24fr) minmax(0, 1fr); gap: 12px; padding: 10px; border: 1px solid var(--border-soft); background: color-mix(in srgb, var(--panel) 82%, transparent); } .theme-label { align-self: start; padding: 2px 0 0; border: 0; background: transparent; text-align: left; cursor: pointer; } .theme-frame > div { min-width: 0; } .theme-statement { display: block; width: 100%; padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .theme-frame strong { display: block; font-size: 14px; line-height: 1.38; } .theme-statement:hover strong { color: #c9ddff; } .theme-field { display: grid; grid-template-columns: 88px 1fr; gap: 8px; width: 100%; margin-top: 7px; padding: 0; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 11.5px; line-height: 1.4; text-align: left; cursor: pointer; } .theme-field:hover { color: var(--text); } .theme-field b { color: var(--faint); font-size: 9px; letter-spacing: .055em; text-transform: uppercase; } .theme-tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 9px; } .theme-tags button { padding: 3px 6px; border: 1px solid color-mix(in srgb, #72a6e8 35%, var(--border)); border-radius: 0; background: transparent; color: #b8d4ff; font-size: 10px; line-height: 1.1; cursor: pointer; } .theme-tags button:hover { border-color: #72a6e8; background: color-mix(in srgb, #72a6e8 11%, transparent); }
  .arc-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 8px; } .arc-card { display: grid; gap: 9px; min-width: 0; padding: 11px; border: 1px solid var(--border-soft); border-radius: 0; background: var(--elev); color: var(--text); text-align: left; } .arc-card:hover { border-color: color-mix(in srgb, #72a6e8 55%, var(--border)); background: var(--elev-2); } .arc-root { padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .arc-root:hover strong { color: #c9ddff; } .arc-card-head { display: flex; justify-content: space-between; gap: 9px; align-items: start; } .arc-card-head strong { display: block; margin-top: 2px; font-size: 14px; } .arc-targets { max-width: 100%; padding: 0; border: 0; background: transparent; color: var(--faint); font-size: 10px; line-height: 1.3; text-align: left; cursor: pointer; } .arc-targets:hover { color: var(--text); }
  .arc-promise { display: grid; gap: 5px; padding: 7px; border-left: 2px solid #72a6e8; background: color-mix(in srgb, #72a6e8 7%, transparent); } .arc-promise button, .arc-undercurrent button { display: grid; gap: 2px; padding: 0; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 11px; line-height: 1.35; text-align: left; cursor: pointer; } .arc-promise button:hover, .arc-undercurrent button:hover { color: var(--text); } .arc-promise b, .arc-fields b, .arc-undercurrent b { color: var(--faint); font-size: 9px; font-weight: 800; letter-spacing: .055em; text-transform: uppercase; }
  .arc-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; } .arc-fields button { display: grid; gap: 3px; min-width: 0; padding: 0; border: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; } .arc-fields button:hover em { color: var(--text); } .arc-fields em { color: var(--muted); font-size: 11px; font-style: normal; line-height: 1.35; overflow-wrap: anywhere; } .arc-fields em.missing { color: var(--faint); font-style: italic; } .arc-undercurrent { display: grid; gap: 5px; padding-top: 8px; border-top: 1px solid var(--border-soft); } .arc-empty { margin: 0; color: var(--faint); font-size: 11.5px; font-style: italic; line-height: 1.45; }

  .supporting-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; } .support-card { min-width: 0; padding: 12px; border: 1px solid var(--border-soft); background: var(--panel); } .support-card h3 { margin-top: 4px; } .support-heading { display: block; width: 100%; padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .support-heading:hover h3 { color: #c9ddff; } .support-fields { display: grid; gap: 8px; margin-top: 12px; } .support-fields button { display: grid; gap: 2px; width: 100%; padding: 0; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 11.5px; line-height: 1.35; text-align: left; cursor: pointer; } .support-fields button:hover span { color: var(--text); } .support-fields b { color: var(--faint); font-size: 9px; font-weight: 800; letter-spacing: .055em; text-transform: uppercase; } .support-empty { width: 100%; margin-top: 5px; padding: 0; border: 0; background: transparent; color: var(--muted); font: inherit; font-size: 12.5px; line-height: 1.45; text-align: left; cursor: pointer; } .support-empty:hover { color: var(--text); }
  .period-summary, .knowledge-list { display: grid; gap: 7px; margin: 12px 0 0; padding: 0; list-style: none; } .period-summary li, .knowledge-list li { display: grid; gap: 2px; padding-top: 7px; border-top: 1px solid var(--border-soft); } .period-summary li:first-child, .knowledge-list li:first-child { padding-top: 0; border-top: 0; } .period-summary-open, .knowledge-open { display: grid; gap: 2px; width: 100%; padding: 0; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; } .period-summary-open:hover b, .knowledge-open:hover b { color: #c9ddff; } .period-summary b, .knowledge-list b { font-size: 11px; } .period-summary span, .knowledge-list span { color: var(--muted); font-size: 11px; line-height: 1.35; } .period-detail { padding: 0; border: 0; background: transparent; color: var(--faint); font-size: 10.5px; line-height: 1.35; text-align: left; cursor: pointer; } .period-detail:hover { color: var(--text); }

  @media (max-width: 940px) { .supporting-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .knowledge-card { grid-column: 1 / -1; } }
  @media (max-width: 620px) { .board-head, .section-head { align-items: flex-start; flex-direction: column; } .theme-frame { grid-template-columns: 1fr; gap: 6px; } .theme-field { grid-template-columns: 1fr; gap: 2px; } .arc-fields { grid-template-columns: 1fr; } .supporting-grid { grid-template-columns: 1fr; } .knowledge-card { grid-column: auto; } .load-error { grid-template-columns: 1fr; } }
</style>

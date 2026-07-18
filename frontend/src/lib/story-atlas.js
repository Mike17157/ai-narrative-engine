/**
 * Public-story projection for the Story Atlas.
 *
 * This is deliberately a presentation projection, rather than a second story
 * model.  It reads only the compact, narrator-safe surface that already lives
 * on the story card and returns four small dramatic kernels.  Consumers can
 * also hand the Atlas pre-built kernels when their story schema is richer.
 */

const HIDDEN_BLOCK = /\[\[\s*(?:model[-_\s]?)?hidden\s*\]\][\s\S]*?\[\[\s*\/\s*(?:model[-_\s]?)?hidden\s*\]\]/gi;
const HIDDEN_MARKER = /\[\[\s*\/?\s*(?:model[-_\s]?)?hidden\s*\]\]/gi;

const asRecord = (value) => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
const asList = (value) => Array.isArray(value) ? value.filter(Boolean) : [];

export function atlasText(value, fallback = '') {
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (typeof value !== 'string') return fallback;
  const cleaned = value
    .replace(HIDDEN_BLOCK, '')
    .replace(HIDDEN_MARKER, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned && !/^author[- ]only$/i.test(cleaned) ? cleaned : fallback;
}

function firstText(...values) {
  for (const value of values) {
    const found = atlasText(value);
    if (found) return found;
  }
  return '';
}

function slug(value, fallback) {
  const text = atlasText(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return text || fallback;
}

function nodeFor(graph, id, section) {
  return asList(graph?.nodes).find((node) => node?.id === id || node?.section === section) || {};
}

function statusFor(graph, id, section, established) {
  return atlasText(nodeFor(graph, id, section).status) || (established ? 'ready' : 'empty');
}

function targetFor(graph, id, section) {
  const target = asRecord(nodeFor(graph, id, section).target);
  return Object.keys(target).length ? target : { section };
}

function indexCastDetails(story) {
  const index = new Map();
  // Public-card projections put this at the top level, while a few older
  // in-memory cards still carry it under `fields`.  The Atlas is only a
  // reader, so accepting both keeps its compact cast view stable during a
  // story's migration.
  const details = story?.cast_details ?? story?.fields?.cast_details;
  if (Array.isArray(details)) {
    for (const detail of details) {
      const record = asRecord(detail);
      const key = firstText(record.character, record.key, record.id, record.name);
      if (key) index.set(key.toLowerCase(), record);
    }
  } else {
    for (const [key, detail] of Object.entries(asRecord(details))) index.set(key.toLowerCase(), asRecord(detail));
  }
  return index;
}

function coreRecords(story) {
  const source = asRecord(story?.fields).character_cores;
  if (Array.isArray(story?.fields?.character_cores)) {
    return story.fields.character_cores.map((value) => typeof value === 'string' ? { core: value } : asRecord(value));
  }
  return Object.entries(asRecord(source)).map(([key, value]) => {
    if (typeof value === 'string') return { character: key, core: value };
    const record = asRecord(value);
    return { ...record, character: record.character || key };
  });
}

function indexCharacterCores(story) {
  const index = new Map();
  for (const core of coreRecords(story)) {
    const key = firstText(core.character, core.owner, core.key, core.id, core.name);
    if (key) index.set(key.toLowerCase(), core);
  }
  return index;
}

function characterRows(story) {
  const detailIndex = indexCastDetails(story);
  const coreIndex = indexCharacterCores(story);
  const seen = new Set();
  const rows = [];

  const push = (candidate, index) => {
    const member = typeof candidate === 'string' ? { character: candidate } : asRecord(candidate);
    const key = firstText(member.character, member.key, member.id, member.name, `character-${index}`);
    const detail = detailIndex.get(key.toLowerCase()) || detailIndex.get(atlasText(member.name).toLowerCase()) || {};
    const core = coreIndex.get(key.toLowerCase()) || coreIndex.get(atlasText(detail.name).toLowerCase()) || {};
    const label = firstText(member.name, detail.name, core.name, key, 'Unnamed character');
    const unique = slug(key || label, `character-${index}`);
    if (seen.has(unique)) return;
    seen.add(unique);

    const role = firstText(member.role, detail.role, core.role);
    const connection = firstText(member.connection, detail.connection, core.connection, core.present_connection);
    const coreText = firstText(core.core, core.tension, core.public_tension);
    const want = firstText(core.want, core.surface_want, core.goal);
    const belief = firstText(core.surface_belief, core.starting_belief);
    const strategy = firstText(core.protective_strategy, core.approach);
    const limitation = firstText(core.limitation, core.visible_limit);
    const visibleTell = firstText(core.visible_tell);
    // A concrete, ordinary backstory (fields.character_wounds) — private author material,
    // the same tier as an arc's truth/blind_spot; surfaced here for the author only.
    const wound = firstText(detail.wound);
    const summary = coreText || want || belief || strategy || limitation || connection || role || 'Their present place in the story still needs definition.';
    const facts = [
      role && `Role · ${role}`,
      coreText && `Core · ${coreText}`,
      wound && `Backstory · ${wound}`,
      want && `Wants · ${want}`,
      belief && `Believes · ${belief}`,
      strategy && `Protects themself by · ${strategy}`,
      limitation && `Limitation · ${limitation}`,
      visibleTell && `Visible tell · ${visibleTell}`,
      connection && `Connection · ${connection}`
    ].filter(Boolean);

    rows.push({
      id: `character:${slug(key || label, `character-${index}`)}`,
      label,
      summary,
      facts,
      target: { section: 'cast', item: { kind: 'character', id: key } }
    });
  };

  asList(story?.cast).forEach(push);
  for (const core of coreRecords(story)) {
    const key = firstText(core.character, core.owner, core.key, core.id, core.name);
    if (!asList(story?.cast).some((member) => firstText(typeof member === 'string' ? member : asRecord(member).character, typeof member === 'string' ? member : asRecord(member).name).toLowerCase() === key.toLowerCase())) push(core, rows.length);
  }
  return rows;
}

function themeLabel(outline, id) {
  const match = asList(outline?.themes).find((theme) => {
    const record = asRecord(theme);
    return firstText(record.id, record.key, record.label).toLowerCase() === atlasText(id).toLowerCase();
  });
  return firstText(asRecord(match).label, asRecord(match).name, id);
}

function arcRows(story) {
  const outline = asRecord(story?.fields?.arc_outline);
  return asList(outline.arcs).map((value, index) => {
    const arc = asRecord(value);
    const owner = firstText(arc.owner, arc.character, arc.name, `Arc ${index + 1}`);
    const isProtagonist = firstText(arc.owner).toLowerCase() === 'player';
    const theme = firstText(arc.theme, themeLabel(outline, arc.theme_id), arc.theme_id);
    const question = firstText(arc.dramatic_question, arc.question);
    const title = (isProtagonist ? '★ ' : '') + firstText(arc.title, arc.name, owner);
    const summary = question || theme || 'The character pressure has not been sketched yet.';
    const facts = [
      isProtagonist && 'The protagonist’s own arc — everything else relates back to it.',
      theme && `Theme · ${theme}`,
      question && `Dramatic question · ${question}`
    ].filter(Boolean);
    const arcId = firstText(arc.id, arc.key, arc.owner, `arc-${index}`);
    return {
      id: `arc:${slug(arcId, `arc-${index}`)}`,
      label: title,
      summary,
      facts,
      target: { section: 'arcs', item: { kind: 'arc', id: arcId } }
    };
  });
}

function sceneRows(story) {
  const plan = asRecord(story?.fields?.first_day_plan);
  const castDetails = indexCastDetails(story);
  return asList(plan.events).map((value, index) => {
    const event = typeof value === 'string' ? { visible: value } : asRecord(value);
    const sceneId = firstText(event.id, event.key, event.title, event.label, `scene-${index}`);
    const title = firstText(event.title, event.label, event.visible, `Possible scene ${index + 1}`);
    const visible = firstText(event.visible, event.public_surface, event.summary, event.hook);
    const when = firstText(event.when, event.time, event.slot);
    const location = firstText(event.location, event.place);
    const participants = asList(event.participants).map((person) => atlasText(person)).filter(Boolean);
    const themes = [
      firstText(event.theme),
      ...asList(event.themes).map((theme) => atlasText(typeof theme === 'string' ? theme : asRecord(theme).label))
    ].filter(Boolean);
    const tone = firstText(event.tone);
    const roles = Object.entries(asRecord(event.roles)).map(([character, role]) => {
      const roleText = atlasText(role);
      const name = firstText(castDetails.get(character.toLowerCase())?.name, character);
      return roleText ? `${name} · ${roleText}` : '';
    }).filter(Boolean);
    const facts = [
      when && `When · ${when}`,
      location && `Where · ${location}`,
      participants.length && `Present · ${participants.join(', ')}`,
      themes.length && `Theme · ${themes.join(', ')}`,
      tone && `Tone · ${tone}`,
      roles.length && `Scene roles · ${roles.join('; ')}`
    ].filter(Boolean);
    return {
      id: `scene:${slug(sceneId, `scene-${index}`)}`,
      label: title,
      summary: visible || 'The visible pressure for this possible scene still needs a sentence.',
      facts,
      target: { section: 'first_day', item: { kind: 'scene', id: sceneId } }
    };
  });
}

function blankKernel({ id, section, eyebrow, label, empty }) {
  return { id, section, eyebrow, label, summary: empty, status: 'empty', target: { section }, details: [] };
}

/**
 * A read-only census of what's sealed from the narrator, across the several
 * independent mechanisms that each protect a different layer ([[hidden]]
 * text blocks, relationship potential/trajectory, secret-tier character
 * facets). Counts and booleans only — this must never surface the sealed
 * content itself, only that it exists.
 */
function protectionSignals(story) {
  const details = [];
  let raw = '';
  try { raw = JSON.stringify(story || {}); } catch { raw = ''; }
  if (raw && raw.search(HIDDEN_MARKER) !== -1) {
    details.push({
      id: 'director:hidden-notes', label: 'Author-only notes',
      summary: 'This card carries [[hidden]] text that is stripped before any model — narrator, co-author, or otherwise — ever sees it.',
      facts: []
    });
  }
  const sealedBonds = asList(story?.relationships)
    .filter((value) => { const r = asRecord(value); return atlasText(r.potential) || atlasText(r.trajectory); }).length;
  if (sealedBonds) {
    details.push({
      id: 'director:sealed-bonds', label: 'Sealed relationship undercurrents',
      summary: `${sealedBonds} bond${sealedBonds === 1 ? '' : 's'} carr${sealedBonds === 1 ? 'ies' : 'y'} a potential the narrator can only feel the weight of, never state outright.`,
      facts: []
    });
  }
  const secretFacets = Number(story?.protected_facet_count) || 0;
  if (secretFacets) {
    details.push({
      id: 'director:secret-facets', label: 'Sealed character depth',
      summary: `${secretFacets} character detail${secretFacets === 1 ? '' : 's'} exist at the secret tier — never handed to a narrator, only felt through its behavioral shadow.`,
      facts: []
    });
  }
  return details;
}

function worldRows(story) {
  const world = asRecord(story?.world);
  const locations = asList(story?.locations).map((value, index) => {
    const place = asRecord(value);
    const id = firstText(place.id, place.key, place.name, `place-${index}`);
    const label = firstText(place.name, id, `Place ${index + 1}`);
    const summary = firstText(place.description, place.summary, 'A place available to the story.');
    const history = firstText(place.history);
    const facts = history ? [`History · ${history}`] : [];
    return { id: `place:${slug(id, `place-${index}`)}`, label, summary, facts, target: { section: 'world', item: { kind: 'location', id } } };
  });
  const facts = [
    firstText(world.genre) && `Genre · ${firstText(world.genre)}`,
    firstText(world.tone) && `Tone · ${firstText(world.tone)}`,
    firstText(world.setting) && `Setting · ${firstText(world.setting)}`,
    firstText(world.atmosphere) && `Atmosphere · ${firstText(world.atmosphere)}`,
    firstText(world.history) && `History · ${firstText(world.history)}`
  ].filter(Boolean);
  if (facts.length) return [{ id: 'world:foundation', label: 'World foundation', summary: firstText(world.setting, world.atmosphere, world.background, world.genre), facts, target: { section: 'world' } }, ...locations];
  return locations;
}

/**
 * Shape a public story card into readable, compact kernels.
 * Explicit `kernels` are handled by StoryAtlas itself; this function only
 * knows ordinary story-card and control-graph shapes.
 */
export function buildStoryAtlasKernels({ story = null, graph = null } = {}) {
  const source = asRecord(story);
  const world = worldRows(source);
  const characters = characterRows(source);
  const arcs = arcRows(source);
  const scenes = sceneRows(source);
  const premise = atlasText(source.premise);
  const fallback = (id, section, empty) => firstText(nodeFor(graph, id, section).summary, empty);

  const setting = world.length
    ? {
        id: 'world', section: 'world', eyebrow: 'World', label: 'Where this story lives',
        summary: firstText(source?.world?.setting, source?.world?.atmosphere, source?.world?.background, fallback('world', 'world', 'The world establishes the opening’s texture and limits.')),
        status: statusFor(graph, 'world', 'world', true), target: targetFor(graph, 'world', 'world'), details: world
      }
    : blankKernel({ id: 'world', section: 'world', eyebrow: 'World', label: 'Where this story lives', empty: fallback('world', 'world', 'Give the opening a place, texture, and a few rules it must obey.') });

  const opening = premise
    ? {
        id: 'opening', section: 'premise', eyebrow: 'Premise', label: 'Opening', summary: premise,
        status: statusFor(graph, 'opening', 'premise', true), target: targetFor(graph, 'opening', 'premise'),
        details: [{ id: 'opening:premise', label: 'The first public moment', summary: premise, facts: [], target: { section: 'premise', item: { kind: 'opening_state', id: 'premise' } } }]
      }
    : blankKernel({ id: 'opening', section: 'premise', eyebrow: 'Premise', label: 'Opening', empty: fallback('opening', 'premise', 'Set the first public image and immediate situation.') });

  const cast = characters.length
    ? {
        id: 'cast', section: 'cast', eyebrow: 'Characters', label: 'People in play',
        summary: characters.length === 1 ? `${characters[0].label} is carrying the immediate human pressure.` : `${characters.length} people can create social pressure in the opening.`,
        status: statusFor(graph, 'cast', 'cast', true), target: targetFor(graph, 'cast', 'cast'), details: characters
      }
    : blankKernel({ id: 'cast', section: 'cast', eyebrow: 'Characters', label: 'People in play', empty: fallback('cast', 'cast', 'Name the people whose choices can put pressure on the opening.') });

  const arc = arcs.length
    ? {
        id: 'arcs', section: 'arcs', eyebrow: 'Arcs', label: 'Pressure underneath',
        summary: arcs.length === 1 ? arcs[0].summary : `${arcs.length} character pressures are present in the story.`,
        status: statusFor(graph, 'arcs', 'arcs', true), target: targetFor(graph, 'arcs', 'arcs'), details: arcs
      }
    : blankKernel({ id: 'arcs', section: 'arcs', eyebrow: 'Arcs', label: 'Pressure underneath', empty: fallback('arcs', 'arcs', 'Give a character a pressure they cannot simply explain away.') });

  const scene = scenes.length
    ? {
        id: 'scenes', section: 'first_day', eyebrow: 'Possible scenes', label: 'What can happen next',
        summary: scenes.length === 1 ? scenes[0].summary : `${scenes.length} possible scenes can turn the opening into play.`,
        status: statusFor(graph, 'scenes', 'first_day', true), target: targetFor(graph, 'scenes', 'first_day'), details: scenes
      }
    : blankKernel({ id: 'scenes', section: 'first_day', eyebrow: 'Possible scenes', label: 'What can happen next', empty: fallback('scenes', 'first_day', 'Sketch one possible scene with a visible pressure and player-facing choice.') });

  const directorNode = nodeFor(graph, 'director', 'time_system');
  const protectionDetails = protectionSignals(source);
  const hasDirector = Boolean(directorNode?.id)
    || Boolean(asRecord(source?.time_system).entity_periods && asList(source?.time_system?.entity_periods).length)
    || Boolean(asRecord(source?.world).entity && Object.keys(asRecord(source?.world).entity).length)
    || Boolean(asRecord(source?.world).loop && Object.keys(asRecord(source?.world).loop).length)
    || protectionDetails.length > 0;
  const director = hasDirector
    ? {
        id: 'director', section: 'time_system', eyebrow: 'Sealed mechanics', label: 'Director layer',
        summary: firstText(directorNode?.summary, 'Protected causality, timing, and knowledge boundaries support this story.'),
        status: statusFor(graph, 'director', 'time_system', true), target: targetFor(graph, 'director', 'time_system'), details: protectionDetails, protected: true
      }
    : null;

  return [setting, opening, cast, arc, scene, director].filter(Boolean);
}

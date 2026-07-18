<script>
  import InlineEditableText from './InlineEditableText.svelte';
  import { isStoryHostDesktop } from '$lib/story-host-client';

  let { story = null, onedit = null, onfocus = null, onselecttarget = null, onsavefield = null } = $props();

  const sections = [
    { id: 'overview', label: 'Overview' },
    { id: 'scenes', label: 'Scenes' },
    { id: 'characters', label: 'Characters' },
    { id: 'arcs', label: 'Storylines' },
    { id: 'world', label: 'World' }
  ];

  let section = $state('overview');
  let selectedId = $state('');

  const text = (value, fallback = '') => typeof value === 'string' && value.trim() ? value.trim() : fallback;
  const title = (value) => text(value).replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
  const values = (value) => Array.isArray(value) ? value.filter(Boolean) : [];
  const characterKey = (member) => typeof member === 'string' ? member : text(member?.character || member?.key);
  const characterName = (key) => characters.find((character) => character.id === key)?.name || title(key);
  const locationName = (key) => locations.find((location) => location.id === key)?.name || title(key);
  const sceneTitle = (scene) => text(scene?.title || scene?.label, title(scene?.id) || 'Possible scene');
  const storylineTitle = (arc) => text(arc?.title || arc?.name, arc?.owner ? `${characterName(arc.owner)}'s storyline` : 'Storyline');

  let locations = $derived(values(story?.locations).map((location) => ({
    ...location,
    id: text(location?.id || location?.name),
    name: text(location?.name, title(location?.id))
  })).filter((location) => location.id));

  let characters = $derived.by(() => {
    const details = new Map(values(story?.cast_details).map((character) => [text(character?.character || character?.key), character]));
    const cast = values(story?.cast).map((member) => {
      const id = characterKey(member);
      const detail = details.get(id) || {};
      return {
        ...detail,
        ...(typeof member === 'object' ? member : {}),
        id,
        name: text(detail.name, title(id)),
        core: text(detail.core || story?.fields?.character_cores?.[id])
      };
    }).filter((character) => character.id);
    const entity = story?.entity_character;
    if (entity && typeof entity === 'object') {
      cast.push({ ...entity, id: text(entity.character, '__story_entity__'), isEntity: true });
    }
    return cast;
  });

  let scenes = $derived.by(() => {
    const day = values(story?.fields?.first_day_plan?.events);
    const authored = values(story?.scenes);
    const result = new Map();
    for (const [index, scene] of [...day, ...authored].entries()) {
      const id = text(scene?.id, `scene-${index + 1}`);
      result.set(id, { ...result.get(id), ...scene, id });
    }
    return [...result.values()];
  });

  let arcs = $derived.by(() => {
    const authorOutline = values(story?.fields?.author_arc_outline?.arcs);
    // A full author-card outline wins over the narrator-safe fallback. Mixing
    // the two would duplicate every storyline because the thin outline has no
    // stable arc id or title.
    const outline = authorOutline.length ? authorOutline : values(story?.fields?.arc_outline?.arcs);
    const authored = values(story?.arcs);
    const result = new Map();
    for (const [index, arc] of [...outline, ...authored].entries()) {
      const owner = text(arc?.owner || arc?.character || arc?.character_id);
      const themeRecord = arc?.theme && typeof arc.theme === 'object' ? arc.theme : null;
      const legacyTheme = values(arc?.themes)[0];
      const theme = text(themeRecord?.label || arc?.theme || arc?.label || legacyTheme || arc?.theme_id);
      const themeQuestion = text(themeRecord?.question || arc?.theme_question);
      const question = text(arc?.dramatic_question || arc?.question || themeQuestion);
      const id = text(arc?.id, owner && theme ? `${owner}-${theme}` : `arc-${index + 1}`);
      result.set(id, { ...result.get(id), ...arc, id, owner, theme, themeQuestion, question });
    }
    return [...result.values()];
  });

  let selectedScene = $derived(scenes.find((scene) => scene.id === selectedId) || scenes[0] || null);
  let selectedCharacter = $derived(characters.find((character) => character.id === selectedId) || characters[0] || null);
  let selectedArc = $derived(arcs.find((arc) => arc.id === selectedId) || arcs[0] || null);
  let selectedLocation = $derived(locations.find((location) => location.id === selectedId) || locations[0] || null);

  function sceneParticipants(scene) {
    const participants = values(scene?.participants || scene?.characters || scene?.present)
      .map((participant) => typeof participant === 'string' ? participant : text(participant?.character || participant?.id))
      .filter(Boolean);
    for (const role of values(scene?.roles)) {
      const id = text(role?.character || role?.id);
      if (id && !participants.includes(id)) participants.push(id);
    }
    return participants;
  }

  function characterScenes(id) {
    if (id === '__story_entity__') {
      const linked = new Set(values(story?.entity_character?.scene_ids));
      return scenes.filter((scene) => linked.has(scene.id) || scene?.entity_action === true || text(scene?.entity_period) || values(scene?.entity_periods).length || text(scene?.knowledge?.entity));
    }
    return scenes.filter((scene) => sceneParticipants(scene).includes(id));
  }

  function characterArcs(id) {
    return arcs.filter((arc) => arc.owner === id || values(arc?.characters || arc?.participants || arc?.cast).includes(id));
  }

  function arcCharacters(arc) {
    return [...new Set([arc?.owner, ...values(arc?.characters || arc?.participants || arc?.cast)].filter(Boolean))];
  }

  function arcScenes(arc) {
    const explicit = new Set(values(arc?.scenes || arc?.scene_ids || arc?.beats).map((item) => typeof item === 'string' ? item : text(item?.scene || item?.scene_id || item?.id)));
    const owners = arcCharacters(arc);
    return scenes.filter((scene) => explicit.has(scene.id) || owners.some((owner) => sceneParticipants(scene).includes(owner)));
  }

  function sceneArcs(scene) {
    const explicit = new Set(values(scene?.arcs || scene?.arc_ids).map((item) => typeof item === 'string' ? item : text(item?.id)));
    const participants = sceneParticipants(scene);
    return arcs.filter((arc) => explicit.has(arc.id) || participants.includes(arc.owner));
  }

  function select(nextSection, id = '') {
    const candidates = nextSection === 'scenes' ? scenes
      : nextSection === 'characters' ? characters
        : nextSection === 'arcs' ? arcs
          : nextSection === 'world' ? locations
            : [];
    // The card shows the first available item immediately after a tab switch.
    // Make that same item the Architect target rather than leaving the
    // conversation attached to an abstract section the author cannot see.
    const selectedItem = candidates.find((item) => item.id === id)
      || candidates.find((item) => item.id === selectedId)
      || candidates[0]
      || null;
    const resolvedId = selectedItem?.id || '';
    section = nextSection;
    selectedId = resolvedId;
    // Overview is the story's opening-facing surface (premise plus the
    // immediate situation), not the mystery-rule editor. Treat its Architect
    // conversation as a focused premise edit; the World tab remains the
    // place for setting and rules.
    const focus = { scenes: 'first_day', characters: 'cast', arcs: 'arcs', world: 'world', overview: 'overview' }[nextSection] || 'world';
    onfocus?.(focus);
    const selected = selectedItem;
    const kind = { scenes: 'scene', characters: 'character', arcs: 'arc', world: 'location' }[nextSection];
    const target = selected && kind ? targetFor(kind, selected) : {
      section: focus,
      label: nextSection === 'overview' ? 'Story overview' : sectionLabel(nextSection),
      summary: nextSection === 'overview'
        ? text(story?.premise, 'Edit the world, premise, and opening position.')
        : `Edit the story's ${sectionLabel(nextSection).toLowerCase()} directly.`
    };
    onselecttarget?.(target);
  }

  function targetFor(kind, item) {
    return kind === 'scene'
      ? { section: 'first_day', item: { kind: 'scene', id: item.id }, label: sceneTitle(item), summary: text(item.visible || item.hook || item.trigger) }
      : kind === 'character'
        ? item.isEntity
          ? { section: 'world', label: item.name, summary: text(item.core || item.role || item.connection, 'Edit this story entity as one character-like actor.') }
          : { section: 'cast', item: { kind: 'character', id: item.id }, label: item.name, summary: text(item.core || item.role || item.connection) }
        : kind === 'arc'
          ? { section: 'arcs', item: { kind: 'arc', id: item.id }, label: storylineTitle(item), summary: text(item.question, item.owner ? `Storyline pressure for ${characterName(item.owner)}.` : 'Storyline.') }
          : { section: 'world', item: { kind: 'location', id: item.id }, label: item.name, summary: text(item.description) };
  }

  function sectionCount(id) {
    return ({ scenes: scenes.length, characters: characters.length, arcs: arcs.length, world: locations.length })[id] || 0;
  }

  function sectionLabel(id) {
    return sections.find((item) => item.id === id)?.label || title(id);
  }

  function objectRows(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value).map(([key, raw]) => {
      if (typeof raw === 'string' || typeof raw === 'number' || typeof raw === 'boolean') return { key, value: String(raw) };
      if (Array.isArray(raw)) return { key, value: raw.map((item) => typeof item === 'string' ? item : text(item?.label || item?.name || item?.id)).filter(Boolean).join(' · ') };
      return { key, value: text(raw?.description || raw?.label || raw?.name || raw?.state) };
    }).filter((row) => row.value);
  }

  const informationStructure = {
    scene: {
      order: ['hook', 'trigger', 'theme', 'tone', 'evidence', 'hidden'],
      exclude: ['id', 'title', 'label', 'visible', 'summary', 'when', 'location', 'participants', 'characters', 'present', 'roles', 'arcs', 'arc_ids', 'knowledge'],
      labels: { hook: 'Player hook', hidden: 'Protected story logic' }
    },
    character: {
      order: ['personality', 'appearance', 'background', 'connection', 'knowledge', 'schedule'],
      exclude: ['id', 'character', 'key', 'name', 'core', 'role', 'private', 'isEntity', 'scene_ids', 'schedule'],
      labels: { connection: 'Story connection' }
    },
    arc: {
      order: ['goal', 'conflict', 'stakes', 'change'],
      exclude: ['id', 'title', 'name', 'label', 'owner', 'character', 'character_id', 'theme', 'theme_id', 'themeQuestion', 'theme_question', 'question', 'dramatic_question', 'tension', 'summary', 'description', 'characters', 'participants', 'scenes', 'scene_ids', 'beats']
    },
    location: {
      order: ['background_prompt'],
      exclude: ['id', 'name', 'description'],
      labels: { background_prompt: 'Visual direction' }
    }
  };

  function structuredRows(value, kind) {
    const structure = informationStructure[kind] || { order: [], exclude: [], labels: {} };
    const order = new Map(structure.order.map((key, index) => [key, index]));
    return objectRows(value)
      .filter((row) => !structure.exclude.includes(row.key))
      .map((row, index) => ({
        ...row,
        label: kind === 'character' && value?.isEntity
          ? ({ personality: 'Behavior', background: 'Limits', connection: 'Objective', knowledge: 'Knowledge' }[row.key] || title(row.key))
          : structure.labels?.[row.key] || title(row.key),
        protected: row.key === 'hidden',
        rank: order.has(row.key) ? order.get(row.key) : structure.order.length + index
      }))
      .sort((a, b) => a.rank - b.rank);
  }

  function save(path, value) {
    return onsavefield?.({ path, value });
  }

  function saveCharacterField(character, field, value) {
    if (character?.isEntity) {
      const entityField = { personality: 'tactic', background: 'limitations', connection: 'objective' }[field] || field;
      return save(['world', 'entity', entityField], value);
    }
    return onsavefield?.({ resource: 'character', id: character.id, field, value });
  }

  function saveStructured(kind, item, field, value) {
    if (kind === 'scene') return save(['fields', 'first_day_plan', 'events', item.id, field], value);
    if (kind === 'character') return saveCharacterField(item, field, value);
    if (kind === 'location') return save(['locations', item.id, field], value);
    throw new Error('This text is derived; use the Architect to change its structure.');
  }

  function canEditStructured(kind, field) {
    if (kind === 'scene') return ['hook', 'trigger', 'theme', 'tone', 'evidence', 'hidden'].includes(field);
    // `knowledge` remains a protected/non-public field in the narrow desktop
    // cast-text capability. Preserve its existing browser editor while making
    // it visibly read-only in the local Story card instead of offering a save
    // control that the host must reject.
    if (kind === 'character') return ['personality', 'appearance', 'background', 'connection'].includes(field)
      || (field === 'knowledge' && !isStoryHostDesktop());
    if (kind === 'location') return field === 'background_prompt';
    return false;
  }
</script>

<section class="explorer" aria-label="Relational story card">
  <header class="explorer-head">
    <nav aria-label="Story card views">
      {#each sections as item}
        <button type="button" class:active={section === item.id} onclick={() => select(item.id)}>{item.label}{#if item.id !== 'overview'}<small>{sectionCount(item.id)}</small>{/if}</button>
      {/each}
    </nav>
  </header>

  {#if section === 'overview'}
    <div class="overview">
      <article class="premise-card">
        <span>Story setting</span>
        <p><InlineEditableText value={story?.world?.setting || story?.world?.background || locationName(story?.start) || ''} placeholder="Where does this story truly live?" label="story setting" onsave={(value) => save(['world', 'setting'], value)} /></p>
        {#if story?.world?.atmosphere || story?.genre}<small>{story?.world?.atmosphere || story.genre}</small>{/if}
      </article>
      <article class="opening-card setting-detail">
        <span>Setting texture</span>
        <dl>
          <div><dt>Biome</dt><dd><InlineEditableText value={story?.world?.biome || ''} placeholder="Not authored yet" label="biome" onsave={(value) => save(['world', 'biome'], value)} /></dd></div>
          <div><dt>Culture</dt><dd><InlineEditableText value={story?.world?.culture || ''} placeholder="Not authored yet" label="culture" onsave={(value) => save(['world', 'culture'], value)} /></dd></div>
          <div><dt>Ordinary life</dt><dd><InlineEditableText value={story?.world?.customs || ''} placeholder="Not authored yet" label="ordinary life" onsave={(value) => save(['world', 'customs'], value)} /></dd></div>
          <div><dt>Material era</dt><dd><InlineEditableText value={story?.world?.technology || ''} placeholder="Not authored yet" label="material era" onsave={(value) => save(['world', 'technology'], value)} /></dd></div>
        </dl>
      </article>
      <article class="premise-card">
        <span>Premise</span>
        <p><InlineEditableText value={story?.premise || ''} placeholder="The opening premise has not been established yet." label="premise" onsave={(value) => save(['premise'], value)} /></p>
        <div class="tags">
          {#if story?.type}<i>{story.type}</i>{/if}
          {#if story?.tone}<i>{story.tone}</i>{/if}
        </div>
      </article>
      <article class="opening-card">
        <span>Opening position</span>
        <dl>
          <div><dt>Where</dt><dd>{locationName(story?.fields?.first_day_plan?.opening_location || story?.start) || 'Not set'}</dd></div>
          <div><dt>When</dt><dd>{title(story?.fields?.first_day_plan?.opening_time) || 'Not set'}</dd></div>
          <div><dt>Present</dt><dd>{values(story?.fields?.first_day_plan?.opening_present).map(characterName).join(', ') || 'Not set'}</dd></div>
          <div><dt>Objective</dt><dd><InlineEditableText value={story?.fields?.first_day_plan?.objective || ''} placeholder="Not set" label="opening objective" onsave={(value) => save(['fields', 'first_day_plan', 'objective'], value)} /></dd></div>
        </dl>
      </article>
    </div>
  {:else}
    <div class="entity-layout">
      <aside class="selector" aria-label={`${sectionLabel(section)} selector`}>
        <span>{sectionLabel(section)}</span>
        {#if section === 'scenes'}
          {#each scenes as scene}<button type="button" class:active={selectedScene?.id === scene.id} onclick={() => select('scenes', scene.id)}><b>{sceneTitle(scene)}</b><small>{[title(scene.when), locationName(scene.location)].filter(Boolean).join(' · ') || 'Unplaced'}</small></button>{/each}
        {:else if section === 'characters'}
          {#each characters as character}<button type="button" class:active={selectedCharacter?.id === character.id} onclick={() => select('characters', character.id)}><b>{character.name}</b><small>{text(character.role || character.connection, 'Character card')}</small></button>{/each}
        {:else if section === 'arcs'}
          {#each arcs as arc}<button type="button" class:active={selectedArc?.id === arc.id} onclick={() => select('arcs', arc.id)}><b>{storylineTitle(arc)}</b><small>{text(arc.theme, 'Theme not authored yet')}</small></button>{/each}
        {:else}
          {#each locations as location}<button type="button" class:active={selectedLocation?.id === location.id} onclick={() => select('world', location.id)}><b>{location.name}</b><small>{text(location.description, 'Story location')}</small></button>{/each}
        {/if}
        {#if (section === 'scenes' && !scenes.length) || (section === 'characters' && !characters.length) || (section === 'arcs' && !arcs.length) || (section === 'world' && !locations.length)}
          <p class="empty">Nothing has been authored here yet.</p>
        {/if}
      </aside>

      <article class="detail">
        {#if section === 'scenes' && selectedScene}
          <header><div><span>Scene</span><h3>{sceneTitle(selectedScene)}</h3><p>{[title(selectedScene.when), locationName(selectedScene.location)].filter(Boolean).join(' · ')}</p></div></header>
          <section class="hero-copy"><span>Visible situation</span><p><InlineEditableText value={selectedScene.visible || ''} placeholder="The visible situation has not been written yet." label="visible situation" onsave={(value) => saveStructured('scene', selectedScene, 'visible', value)} /></p></section>
          {#if structuredRows(selectedScene, 'scene').length}<dl class="structured-info">{#each structuredRows(selectedScene, 'scene') as row}<div class:protected={row.protected}><dt>{row.label}</dt><dd>{#if canEditStructured('scene', row.key)}<InlineEditableText value={row.value} label={row.label} onsave={(value) => saveStructured('scene', selectedScene, row.key, value)} />{:else}{row.value}{/if}</dd></div>{/each}</dl>{/if}
          <section class="connections"><span>Characters in this scene</span><div>{#each sceneParticipants(selectedScene) as id}<button type="button" onclick={() => select('characters', id)}>{characterName(id)}</button>{/each}</div></section>
          {#if values(selectedScene.roles).length}<section class="roles"><span>Scene roles</span>{#each selectedScene.roles as role}<div><b>{characterName(role.character)}</b><p>{role.role}</p></div>{/each}</section>{/if}
          {#if sceneArcs(selectedScene).length}<section class="connections"><span>Related storylines</span><div>{#each sceneArcs(selectedScene) as arc}<button type="button" onclick={() => select('arcs', arc.id)}>{storylineTitle(arc)}</button>{/each}</div></section>{/if}
          {#if objectRows(selectedScene.knowledge).length}<section class="roles"><span>Knowledge after this scene</span>{#each objectRows(selectedScene.knowledge) as row}<div><b>{characterName(row.key)}</b><p>{row.value}</p></div>{/each}</section>{/if}
        {:else if section === 'characters' && selectedCharacter}
          <header><div><span>Character</span><h3><InlineEditableText value={selectedCharacter.name} placeholder="Unnamed character" label="character name" multiline={false} onsave={(value) => selectedCharacter.isEntity ? save(['world', 'entity', 'name'], value) : saveCharacterField(selectedCharacter, 'name', value)} /></h3><p><InlineEditableText value={selectedCharacter.role || ''} placeholder="Story character" label="character role" onsave={(value) => saveCharacterField(selectedCharacter, 'role', value)} /></p></div></header>
          <section class="hero-copy"><span>Character core</span><p><InlineEditableText value={selectedCharacter.core || ''} placeholder="This character core has not been distilled yet." label="character core" onsave={(value) => selectedCharacter.isEntity ? save(['world', 'entity', 'description'], value) : save(['fields', 'character_cores', selectedCharacter.id], value)} /></p></section>
          {#if structuredRows(selectedCharacter, 'character').length}<dl class="structured-info">{#each structuredRows(selectedCharacter, 'character') as row}<div><dt>{row.label}</dt><dd>{#if canEditStructured('character', row.key)}<InlineEditableText value={row.value} label={row.label} onsave={(value) => saveStructured('character', selectedCharacter, row.key, value)} />{:else}{row.value}{/if}</dd></div>{/each}</dl>{/if}
          {#if selectedCharacter.isEntity && values(selectedCharacter.schedule).length}
            <section class="roles"><span>Activity windows</span>{#each selectedCharacter.schedule as period}<div><b>{title(period.id)} · {title(period.state)}</b><p><InlineEditableText value={period.constraint || ''} placeholder="No constraint established" label={`${title(period.id)} constraint`} onsave={(value) => save(['time_system', 'entity_periods', period.id, 'constraint'], value)} /></p></div>{/each}</section>
          {/if}
          <section class="connections"><span>Scenes featuring {selectedCharacter.name}</span><div>{#each characterScenes(selectedCharacter.id) as scene}<button type="button" onclick={() => select('scenes', scene.id)}>{sceneTitle(scene)}</button>{:else}<small>No scene currently names this character.</small>{/each}</div></section>
          <section class="connections"><span>Storylines involving {selectedCharacter.name}</span><div>{#each characterArcs(selectedCharacter.id) as arc}<button type="button" onclick={() => select('arcs', arc.id)}>{storylineTitle(arc)}</button>{:else}<small>No storyline currently names this character.</small>{/each}</div></section>
        {:else if section === 'arcs' && selectedArc}
          <header><div><span>Storyline</span><h3>{storylineTitle(selectedArc)}</h3><p>{selectedArc.owner ? `Centered on ${characterName(selectedArc.owner)}` : 'Story-wide pressure'}</p></div></header>
          <section class="hero-copy"><span>Theme</span><p>{text(selectedArc.theme, 'This storyline has not named the question it is testing yet.')}</p></section>
          <section class="hero-copy"><span>Dramatic question</span><p>{text(selectedArc.question || selectedArc.tension || selectedArc.summary || selectedArc.description, 'This storyline is established, but its dramatic question has not been written yet.')}</p></section>
          {#if structuredRows(selectedArc, 'arc').length}<dl class="structured-info">{#each structuredRows(selectedArc, 'arc') as row}<div><dt>{row.label}</dt><dd>{row.value}</dd></div>{/each}</dl>{/if}
          <section class="connections"><span>Characters in this storyline</span><div>{#each arcCharacters(selectedArc) as id}<button type="button" onclick={() => select('characters', id)}>{characterName(id)}</button>{:else}<small>No character is attached yet.</small>{/each}</div></section>
          <section class="connections"><span>Scenes in this storyline</span><div>{#each arcScenes(selectedArc) as scene}<button type="button" onclick={() => select('scenes', scene.id)}>{sceneTitle(scene)}</button>{:else}<small>No scene is connected yet.</small>{/each}</div></section>
        {:else if section === 'world' && selectedLocation}
          <header><div><span>Location</span><h3><InlineEditableText value={selectedLocation.name} placeholder="Unnamed location" label="location name" multiline={false} onsave={(value) => save(['locations', selectedLocation.id, 'name'], value)} /></h3><p>{selectedLocation.id === story?.start ? 'Opening location' : 'Story location'}</p></div></header>
          <section class="hero-copy"><span>Description</span><p><InlineEditableText value={selectedLocation.description || ''} placeholder="This location has not been described yet." label="location description" onsave={(value) => save(['locations', selectedLocation.id, 'description'], value)} /></p></section>
          {#if structuredRows(selectedLocation, 'location').length}<dl class="structured-info">{#each structuredRows(selectedLocation, 'location') as row}<div><dt>{row.label}</dt><dd>{#if canEditStructured('location', row.key)}<InlineEditableText value={row.value} label={row.label} onsave={(value) => saveStructured('location', selectedLocation, row.key, value)} />{:else}{row.value}{/if}</dd></div>{/each}</dl>{/if}
          <section class="connections"><span>Scenes at {selectedLocation.name}</span><div>{#each scenes.filter((scene) => scene.location === selectedLocation.id) as scene}<button type="button" onclick={() => select('scenes', scene.id)}>{sceneTitle(scene)}</button>{:else}<small>No scene is placed here yet.</small>{/each}</div></section>
          {#if objectRows(story?.world).length}<section class="roles"><span>World facts</span>{#each objectRows(story.world) as row}<div><b>{title(row.key)}</b><p>{row.value}</p></div>{/each}</section>{/if}
          {#if values(story?.time_system?.entity_periods).length}<button class="director-link" type="button" onclick={() => onedit?.({ id: 'time_system', target: { section: 'time_system', label: 'World schedule', summary: 'Change the protected schedule through the Story Architect.' } })}>Edit schedule with Architect</button>{/if}
        {/if}
      </article>
    </div>
  {/if}
</section>

<style>
  .explorer { display: grid; grid-template-rows: auto minmax(0, 1fr); min-width: 0; min-height: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 2px; background: var(--panel); scrollbar-width: thin; }
  .explorer-head { position: sticky; top: 0; z-index: 3; padding: 0 12px; border-bottom: 1px solid var(--border-soft); background: color-mix(in srgb, var(--panel) 94%, transparent); backdrop-filter: blur(12px); }
  .explorer-head > div > span, .premise-card > span, .opening-card > span, .selector > span, .hero-copy > span, .connections > span, .roles > span { color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }
  .explorer-head nav { display: flex; gap: 0; overflow: hidden; }
  .explorer-head nav button { display: inline-flex; flex: 1 1 0; align-items: center; justify-content: center; gap: 7px; min-width: 0; border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: 11px 8px 9px; background: transparent; color: var(--muted); font: inherit; font-size: 10px; font-weight: 750; cursor: pointer; }
  .explorer-head nav button:hover { background: color-mix(in srgb, var(--elev) 68%, transparent); color: var(--text); }
  .explorer-head nav button:focus-visible { outline: none; background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--text); }
  .explorer-head nav small { display: grid; place-items: center; min-width: 17px; height: 16px; border: 1px solid var(--border-soft); border-radius: 2px; color: var(--faint); font-size: 8px; line-height: 1; }
  .explorer-head nav button.active small { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); color: var(--accent); }
  .explorer-head nav button.active { border-color: var(--accent); color: var(--text); }
  .overview { display: grid; grid-template-columns: minmax(0, 1fr); align-content: start; gap: 12px; min-height: 0; overflow-y: auto; padding: 16px; scrollbar-width: none; -ms-overflow-style: none; }
  .premise-card, .opening-card { padding: 16px; border: 1px solid var(--border-soft); border-radius: 2px; background: var(--elev); }
  .premise-card p { width: 100%; margin: 9px 0 0; color: var(--text); font-size: 15px; line-height: 1.55; }
  .tags { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 13px; }.tags i { padding: 4px 7px; border: 1px solid var(--border-soft); border-radius: 999px; color: var(--muted); font-size: 9px; font-style: normal; }
  .opening-card { grid-column: 1 / -1; }.opening-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 22px; margin: 9px 0 0; }.opening-card dl div { padding: 8px 0; border-top: 1px solid var(--border-soft); }.opening-card dt { color: var(--faint); font-size: 9px; text-transform: uppercase; }.opening-card dd { margin: 3px 0 0; color: var(--text); font-size: 11px; line-height: 1.35; }
  .entity-layout { display: grid; grid-template-columns: 210px minmax(0, 1fr); min-height: 0; height: 100%; overflow: hidden; }
  .selector { min-width: 0; min-height: 0; overflow-y: auto; padding: 13px 9px; border-right: 1px solid var(--border-soft); background: color-mix(in srgb, var(--bg) 25%, var(--panel)); scrollbar-width: none; -ms-overflow-style: none; }
  .selector > span { display: block; padding: 0 7px 7px; }.selector button { display: grid; gap: 3px; width: 100%; margin: 2px 0; border: 1px solid transparent; border-radius: 2px; padding: 9px 8px; background: transparent; color: var(--text); text-align: left; font: inherit; cursor: pointer; }.selector button:hover { background: var(--elev); }.selector button.active { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: color-mix(in srgb, var(--accent) 10%, var(--elev)); box-shadow: inset 2px 0 0 var(--accent); }.selector b { overflow: hidden; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.selector small { display: -webkit-box; overflow: hidden; color: var(--faint); font-size: 9px; line-height: 1.25; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }.empty { padding: 8px; color: var(--faint); font-size: 10px; }
  .detail { min-width: 0; min-height: 0; overflow-y: auto; padding: 18px; scrollbar-width: none; -ms-overflow-style: none; }.detail > header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-bottom: 14px; border-bottom: 1px solid var(--border-soft); }.detail > header span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }.detail > header h3 { margin: 3px 0; color: var(--text); font-size: 23px; letter-spacing: -.035em; }.detail > header p { margin: 0; color: var(--muted); font-size: 10px; }.detail > header button, .director-link { flex: none; border: 1px solid var(--accent); border-radius: 7px; padding: 6px 8px; background: color-mix(in srgb, var(--accent) 13%, var(--elev)); color: var(--text); font: inherit; font-size: 9px; font-weight: 850; cursor: pointer; }
  .overview::-webkit-scrollbar, .selector::-webkit-scrollbar, .detail::-webkit-scrollbar { display: none; }
  .hero-copy { margin-top: 14px; padding: 13px; border-left: 2px solid var(--accent); background: color-mix(in srgb, var(--accent) 6%, var(--elev)); }.hero-copy p { margin: 6px 0 0; color: var(--text); font-size: 13px; line-height: 1.5; }
  .structured-info { margin: 12px 0 0; border-top: 1px solid var(--border-soft); }.structured-info > div { display: grid; grid-template-columns: minmax(105px, .3fr) minmax(0, 1fr); gap: 14px; padding: 10px 2px; border-bottom: 1px solid var(--border-soft); }.structured-info > div.protected { padding-left: 9px; border-left: 2px solid color-mix(in srgb, #a989ff 65%, var(--border)); }.structured-info dt { color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; }.structured-info dd { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.45; }
  .connections, .roles { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border-soft); }.connections > div { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }.connections button { border: 1px solid var(--border-soft); border-radius: 999px; padding: 5px 8px; background: var(--elev); color: var(--text); font: inherit; font-size: 9px; font-weight: 750; cursor: pointer; }.connections button:hover { border-color: var(--accent); }.connections small { color: var(--faint); font-size: 10px; }
  .roles > div { display: grid; grid-template-columns: minmax(90px, .3fr) minmax(0, 1fr); gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--border-soft); }.roles b { color: var(--text); font-size: 10px; }.roles p { margin: 0; color: var(--muted); font-size: 10px; line-height: 1.38; }.director-link { margin-top: 14px; }
  @media (max-width: 760px) { .explorer { display: block; overflow: auto; }.explorer-head nav { overflow-x: auto; }.explorer-head nav button { flex: 0 0 auto; min-width: max-content; padding-inline: 12px; }.overview { grid-template-columns: 1fr; }.entity-layout { grid-template-columns: 1fr; height: auto; overflow: visible; }.selector { display: flex; gap: 4px; overflow-x: auto; border-right: 0; border-bottom: 1px solid var(--border-soft); }.selector > span { display: none; }.selector button { flex: 0 0 165px; }.detail { overflow: visible; }.structured-info > div { grid-template-columns: 1fr; gap: 4px; }.opening-card dl { grid-template-columns: 1fr; } }
</style>

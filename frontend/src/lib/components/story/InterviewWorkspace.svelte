<script>
  import { loadStory } from '$lib/stories.svelte.js';
  import { voice, startListening, stopListening } from '$lib/voice.svelte.js';
  import { charName, loadChars } from '$lib/characters.svelte.js';
  import DirectorBoard from './DirectorBoard.svelte';
  import EditorialModal from './EditorialModal.svelte';
  import StoryArchitectModal from './StoryArchitectModal.svelte';
  import StoryControlFlow from './StoryControlFlow.svelte';
  import PlaySurface from './PlaySurface.svelte';

  let { storyKey, story } = $props();
  let current = $state(story);
  // Keep this local working view aligned when a parent refreshes the card,
  // while still allowing an authoring response to update it optimistically.
  $effect(() => {
    if (story) current = story;
  });
  const workspaceModes = [
    { id: 'story', label: 'Story card', hint: 'Your living story. Browse a section first, then discuss the exact detail you want to change.' },
    { id: 'architect', label: 'Architect', hint: 'The Story Architect can turn high-level direction into a bounded story pass.' },
    { id: 'play', label: 'Play', hint: 'Readiness and the live story state.' }
  ];
  // The card is the everyday home because it is the public source of truth.
  // Architect is a deliberate co-authoring surface; Director-only material is
  // available contextually from the card rather than competing with it.
  let workspaceMode = $state('story');
  let directorOpen = $state(false);
  let conversationOpen = $state(false);
  const stages = [
    { id: 'world', label: 'The world', hint: 'What sort of story are we telling?' },
    { id: 'premise', label: 'The opening', hint: 'Where do we begin, and what has shifted?' },
    { id: 'cast', label: 'The people', hint: 'Who is already waiting there?' },
    { id: 'arcs', label: 'Theme & arcs', hint: 'What can each person not admit or see yet?' },
    { id: 'first_day', label: 'Possible scenes', hint: 'Where can pressure land before the first death?' },
    { id: 'time_system', label: 'Entity schedule', hint: 'When can the entity observe, hunt, or feed?' },
  ];
  const legacyBootstrap = new Set([
    'I’m starting a new story. Invite me to share any useful starting spark.',
    "I'm starting a new story. Invite me to share any useful starting spark.",
    'This is an existing story card. Read what is already established and identify the single most important thing it still needs before it can drive playable scenes.'
  ]);
  function cleanConversation(source) {
    return (Array.isArray(source) ? source : [])
      .filter((message) => message?.text && !legacyBootstrap.has(message.text.trim()))
      .map((message) => ({ ...message, text: message.text.trim() }));
  }
  const initialFocus = () => {
    if (!Object.keys(current?.world || {}).length) return 'world';
    if (!current?.premise) return 'premise';
    if (!(current?.cast || []).length) return 'cast';
    if (!(current?.fields?.arc_outline?.arcs || []).length) return 'arcs';
    if (!(current?.fields?.first_day_plan?.events || []).length) return 'first_day';
    if (!(current?.time_system?.entity_periods || []).length) return 'time_system';
    return 'arcs';
  };
  let focus = $state(initialFocus());
  // Every thread belongs to one durable story-card target.  Histories live on
  // the server, rather than in one local, story-wide interview transcript.
  let editorTarget = $state(null);
  let messages = $state([]);
  let editorSuggestions = $state([]);
  let editorRequest = 0;
  let typed = $state('');
  let busy = $state(false);
  let organizing = $state(false);
  let reviewing = $state(false);
  let developing = $state(false);
  let developOpen = $state(false);
  let developScope = $state('setup');
  let developBrief = $state('');
  let architectBusy = $state(false);
  let architectTyped = $state('');
  let architectMessages = $state([]);
  let architectGaps = $state([]);
  let architectSummary = $state('');
  let architectMission = $state('');
  let architectActions = $state([]);
  let architectPlan = $state(null);
  let architectAuthorPlan = $state(null);
  let controlGraph = $state(null);
  let controlGraphKey = $state('');
  let controlGraphCompact = $state(true);
  let architectExecutionReady = $state(false);
  let architectRoute = $state('');
  let architectError = $state('');
  // Kept separately from the error copy so a retry can reuse the exact
  // author direction without treating a model failure as a completed turn.
  let architectFallback = $state(null);
  let architectQuestion = $state('');
  let architectAnswerTo = $state('');
  let architectQuestionKey = $state('');
  let architectActivity = $state('Reading the live story card…');
  let architectSessionKey = $state('');
  let architectRequest = 0;
  let architectRun = 0;
  let error = $state('');
  let card = $state(null);
  let lastUpdated = $state('');
  let focusedCardItem = $state('');
  let readiness = $state(null);
  let activating = $state(false);
  let readinessKey = '';
  let readinessItems = $derived.by(() => {
    const grouped = new Map();
    for (const blocker of readiness?.blockers || []) {
      const key = `${blocker.section || 'world'}:${blocker.code || blocker.message}`;
      const prior = grouped.get(key);
      if (prior) prior.count += 1;
      else grouped.set(key, { ...blocker, count: 1 });
    }
    return [...grouped.values()];
  });
  const developmentScopes = [
    {
      id: 'all',
      label: 'Story pass',
      hint: 'Fill the connected missing pieces across the opening, cast, possible scenes, and schedule without rewriting canon.'
    },
    {
      id: 'setup',
      label: 'Playable opening',
      hint: 'Fill the practical opening pieces and create only the people the first scenes need.'
    },
    {
      id: 'cast',
      label: 'Starting cast',
      hint: 'Create a small, connected group of real character cards from the story already on this card.'
    }
  ];
  // A Story Agent pass should never leave the primary authoring surface in a
  // permanent "Working" state.  The server may still finish after an abort,
  // so the recovery copy tells the author to retry against the latest card
  // rather than claiming that no write occurred.
  const architectRequestTimeoutMs = 45_000;

  function stageFor(patch) {
    if (patch?.fields?.arc_design || patch?.themes) return 'arcs';
    if (patch?.fields?.first_day_plan) return 'first_day';
    if (patch?.time_system) return 'time_system';
    const changed = Object.keys(patch || {}).filter((key) => key !== 'fields');
    const last = changed.at(-1);
    if (last === 'relationships') return 'cast';
    if (last === 'locations' || last === 'start') return 'world';
    return last || focus;
  }
  function authorNote(text) {
    return String(text || '').replace(/\[\[\s*\/?\s*(?:model[-_\s]?)?hidden\s*\]\]/gi, '').trim();
  }
  function normaliseSection(section) {
    return stages.some((stage) => stage.id === section) ? section : 'world';
  }
  function sectionTarget(section) {
    const id = normaliseSection(section?.id || section);
    const stage = stages.find((item) => item.id === id);
    return {
      id: `section:${id}`,
      scope: 'section',
      section: id,
      kind: 'section',
      label: stage?.label || 'Story section',
      summary: stage?.hint || 'Make this part of the story more useful.'
    };
  }
  function itemTarget(section, kind, id, label, summary = '', field = '') {
    const safeSection = normaliseSection(section);
    const safeKind = String(kind || 'item');
    const safeId = String(id || `${safeKind}-${safeSection}`);
    const item = { kind: safeKind, id: safeId };
    if (field) item.field = String(field);
    return {
      id: `${safeKind}:${safeId}${field ? `:${field}` : ''}`,
      scope: field ? 'field' : 'item',
      section: safeSection,
      kind: safeKind,
      item,
      label: label || safeKind.replace(/[_-]+/g, ' '),
      summary: summary || `Improve this part of ${stages.find((stage) => stage.id === safeSection)?.label || 'the story'}.`
    };
  }
  function normaliseTarget(target) {
    if (!target || typeof target === 'string') return sectionTarget(target || focus);
    if (!target.item && (!target.kind || target.kind === 'section')) return sectionTarget(target.section || target.id || focus);
    if (target.item?.kind && target.item?.id) {
      return itemTarget(target.section || focus, target.item.kind, target.item.id, target.label, target.summary, target.item.field || target.field);
    }
    if (target.kind && target.id) return itemTarget(target.section || focus, target.kind, target.id, target.label, target.summary, target.field);
    return sectionTarget(target.section || focus);
  }
  function apiTarget(target) {
    const resolved = normaliseTarget(target);
    const api = { section: resolved.section };
    if (resolved.item) api.item = { ...resolved.item };
    return api;
  }
  function targetHistoryUrl(target) {
    const resolved = normaliseTarget(target);
    const params = new URLSearchParams({ section: resolved.section });
    if (resolved.item) {
      params.set('item_kind', resolved.item.kind);
      params.set('item_id', resolved.item.id);
      if (resolved.item.field) params.set('field', resolved.item.field);
    }
    return `/api/stories/${storyKey}/interview-history?${params}`;
  }
  function defaultSuggestions(target) {
    const resolved = normaliseTarget(target);
    const noun = resolved.label || 'this part of the story';
    return [
      { id: 'purpose', title: 'Clarify its job', detail: `What must ${noun} accomplish in play?`, prompt: `Clarify what ${noun} must accomplish in play.` },
      { id: 'pressure', title: 'Add useful pressure', detail: 'Give it a constraint, consequence, or point of tension.', prompt: `Add a concrete source of pressure or consequence to ${noun}.` },
      { id: 'connection', title: 'Connect it', detail: 'Tie it to another canon fact so it has consequences.', prompt: `Connect ${noun} to another part of the story in a way that changes play.` }
    ];
  }
  function normaliseSuggestions(value, target = editorTarget) {
    const suggestions = Array.isArray(value) ? value : [];
    const formatted = suggestions.map((suggestion, index) => ({
      id: suggestion?.id || `${target?.id || 'target'}-${index}`,
      title: suggestion?.title || suggestion?.label || suggestion?.section || 'Improve this',
      detail: suggestion?.detail || suggestion?.reason || suggestion?.suggestion || suggestion?.prompt || '',
      prompt: suggestion?.prompt || suggestion?.suggestion || suggestion?.detail || suggestion?.reason || ''
    })).filter((suggestion) => suggestion.title || suggestion.detail);
    return formatted.length ? formatted : defaultSuggestions(target);
  }
  function responseHistory(data, fallback = []) {
    return cleanConversation(data?.conversation?.history || data?.history || fallback);
  }
  function scrollCardTarget(target) {
    requestAnimationFrame(() => {
      if (!card) return;
      const item = target?.id
        ? [...card.querySelectorAll('[data-card-item]')].find((element) => element.dataset.cardItem === target.id)
        : null;
      const section = [...card.querySelectorAll('[data-card-section]')]
        .find((element) => element.dataset.cardSection === target?.section);
      (item || section)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }
  async function applyDirectorStory(storyUpdate) {
    current = storyUpdate || await loadStory(storyKey);
    readiness = await refreshReadiness();
  }
  function applyDirectorArchitectUpdate(update) {
    if (!update || typeof update !== 'object') return;
    // A placement is authoritative immediately.  If an Architect turn was
    // still rendering, discard its local result rather than letting it hide
    // the reconciliation question that the Director just created.
    const run = ++architectRun;
    architectBusy = false;
    architectError = '';
    architectActivity = 'Reconciling the Director placement…';
    applyArchitectResponse(update, { run, before: architectMessages });
    architectActivity = 'Waiting for your direction';
  }
  function compactArchitectText(value) {
    if (typeof value === 'string') return value.trim();
    if (!value || typeof value !== 'object') return '';
    return String(value.text || value.question || value.prompt || value.message || value.detail || '').trim();
  }
  function architectQuestionFromGap(gaps = architectGaps) {
    const gap = (Array.isArray(gaps) ? gaps : []).find((item) => item?.severity === 'blocker')
      || (Array.isArray(gaps) ? gaps : [])[0];
    if (!gap) {
      return {
        id: 'creative-next',
        text: 'The card is structurally connected. What tension, relationship, or possible scene would you like me to deepen next?'
      };
    }
    const section = String(gap.section || 'story').replace(/_/g, ' ');
    const title = String(gap.title || 'the next connection').replace(/[.?!]+$/, '');
    const detail = String(gap.detail || '').trim();
    return {
      id: String(gap.id || `${section}:${title}`),
      text: `Before I strengthen the ${section}, I need your judgment: ${title}. ${detail}${detail ? ' ' : ''}What should be true here?`
    };
  }
  function setArchitectQuestion(value, { answerTo = '', announce = false, force = false, allowGapFallback = false } = {}) {
    const parsed = typeof value === 'object' && value ? value : { text: value };
    // Only the retired local fallback is allowed to turn a diagnostic into a
    // question.  The persisted Story Architect must explicitly nominate the
    // one author decision; raw gaps are implementation work, not prompts.
    const fallback = allowGapFallback ? architectQuestionFromGap() : null;
    const text = compactArchitectText(parsed) || fallback?.text || '';
    if (!text) {
      architectQuestion = '';
      architectAnswerTo = '';
      architectQuestionKey = '';
      return false;
    }
    const id = String(parsed?.id || parsed?.key || answerTo || fallback?.id || text).trim();
    const key = `${id}:${text}`;
    architectQuestion = text;
    architectAnswerTo = String(answerTo || parsed?.answer_to || parsed?.id || '').trim();
    if (!force && key === architectQuestionKey) return;
    architectQuestionKey = key;
    if (announce && text) {
      architectMessages = [...architectMessages, {
        role: 'assistant', kind: 'question', text,
        meta: 'Current author decision — I will handle the connected implementation.'
      }];
    }
    return true;
  }
  function architectHistory(data) {
    const candidates = [
      data?.conversation?.history,
      data?.conversation?.messages,
      data?.history,
      data?.messages,
      data?.turns,
      data?.architect?.history
    ];
    for (const candidate of candidates) {
      if (!Array.isArray(candidate)) continue;
      const history = candidate.map((item) => {
        const text = compactArchitectText(item);
        if (!text) return null;
        return {
          role: item?.role === 'user' || item?.role === 'author' ? 'user' : 'assistant',
          text,
          meta: compactArchitectText(item?.meta)
        };
      }).filter(Boolean);
      if (history.length) return history;
    }
    return [];
  }
  function architectAuthorHistory(data, fallback = []) {
    /*
     * `architect.history` is a tiny coordinator ledger, not a chat log: the
     * server records each pending question there so it can survive reloads.
     * Showing that ledger verbatim made obsolete worker/reconciliation
     * questions look like a growing questionnaire.  The conversation surface
     * needs only the author's own directions; a completed pass and the one
     * live question are projected below from the current response.
     */
    const source = architectHistory(data);
    const candidates = source.length ? source : fallback;
    const authorTurns = [];
    for (const message of candidates) {
      if (message?.role !== 'user') continue;
      const text = String(message.text || '').trim();
      if (!text || authorTurns.at(-1)?.text === text) continue;
      authorTurns.push({ role: 'user', text });
    }
    // Enough local context for a human to read, without turning a long
    // autonomous completion into an ever-growing interview transcript.
    return authorTurns.slice(-4);
  }
  function architectUpdatedSections(data) {
    const values = [
      ...(Array.isArray(data?.updated_sections) ? data.updated_sections : []),
      ...(Array.isArray(data?.sections) ? data.sections : []),
      ...(Array.isArray(data?.action?.updated_sections) ? data.action.updated_sections : [])
    ];
    const labels = {
      world: 'world', premise: 'opening', cast: 'people', arcs: 'theme & arcs',
      first_day: 'possible scenes', time_system: 'schedule'
    };
    return [...new Set(values
      .map((value) => labels[String(value || '').trim()] || '')
      .filter(Boolean))];
  }
  function architectWorkerCount(data) {
    const candidates = [
      data?.workers,
      data?.worker_results,
      data?.implementation?.workers,
      data?.progress?.workers
    ];
    for (const candidate of candidates) {
      if (!Array.isArray(candidate)) continue;
      const finished = candidate.filter((worker) => {
        const status = String(worker?.status || worker?.state || '').toLowerCase();
        return ['complete', 'completed', 'done', 'succeeded', 'success'].includes(status);
      });
      if (finished.length) return finished.length;
    }
    return 0;
  }
  function architectProgressEntry(data, reply = '') {
    const action = String(data?.action || '').trim();
    const sections = architectUpdatedSections(data);
    const workers = architectWorkerCount(data);
    const sectionLabel = sections.length ? sections.join(', ') : '';
    let text = '';
    if (workers) text = `Completed ${workers} planned build${workers === 1 ? '' : 's'}${sectionLabel ? ` for ${sectionLabel}` : ''}.`;
    else if (action === 'reconcile') text = sectionLabel ? `Reconciled the moved scene and updated ${sectionLabel}.` : 'Reconciled the moved scene.';
    else if (action === 'knowledge') text = 'Completed the director-only knowledge pass.';
    else if (action === 'interview') text = sectionLabel ? `Updated the protected ${sectionLabel} pass.` : 'Updated the protected authoring pass.';
    else if (action === 'develop') text = sectionLabel ? `Completed the planned ${sectionLabel} pass.` : 'Completed the planned card pass.';
    else if (action === 'normalize') text = 'Normalized the story setup from established canon.';
    else if (action === 'assess') text = 'Rechecked the story plan.';
    else text = 'Completed an Architect pass.';

    // A worker should never turn its raw task output into another question on
    // the author-facing thread.  The Architect's current `question` field is
    // the only request for input; the card remains the detailed source of
    // truth for implementation results.
    const raw = String(reply || '').replace(/\s+/g, ' ').trim();
    const asksForInput = /\?|^(?:before\s+i\s+(?:build|continue)|what\b|who\b|where\b|when\b|which\b|how\b|should\b|can\b|do\b|is\b|are\b|tell\s+me\b|give\s+me\b|i\s+need\b)/i.test(raw);
    const detail = !asksForInput && raw && raw.length <= 220 && raw !== text ? raw : '';
    return {
      role: 'assistant', kind: 'progress', text,
      meta: detail || (workers ? 'Implementation progress — no response required.' : 'Plan progress — no response required.')
    };
  }
  function responseArchitectQuestion(data) {
    return data?.question
      || data?.next_question
      || data?.pending_question
      || data?.conversation?.question
      || data?.conversation?.pending_question
      || null;
  }
  function responseArchitectGaps(data) {
    const gaps = Array.isArray(data?.gaps)
      ? data.gaps
      : (data?.gaps?.gaps || data?.assessment?.gaps || data?.card_gaps?.gaps || []);
    return Array.isArray(gaps) ? gaps : [];
  }
  function responseArchitectSummary(data) {
    const summary = data?.summary || data?.gaps?.summary || data?.assessment?.summary || data?.card_gaps?.summary || '';
    return typeof summary === 'string' ? summary : String(summary?.message || summary?.text || '');
  }
  function responseAuthorPlan(data) {
    const candidates = [
      data?.author_plan,
      data?.authorPlan,
      data?.architect?.author_plan,
      data?.architect?.authorPlan
    ];
    for (const candidate of candidates) {
      if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) continue;
      if (Array.isArray(candidate?.sections) || typeof candidate?.summary === 'string' || typeof candidate?.title === 'string') {
        return candidate;
      }
    }
    return null;
  }
  function responseArchitectReply(data) {
    return compactArchitectText(data?.reply)
      || compactArchitectText(data?.message)
      || compactArchitectText(data?.response)
      || compactArchitectText(data?.assistant_message);
  }
  const architectScopeNames = new Set(['world', 'premise', 'cast', 'first_day', 'time_system', 'setup', 'starter', 'all']);
  function architectScopes(gaps) {
    return [...new Set((Array.isArray(gaps) ? gaps : [])
      .map((gap) => String(gap?.section || '').trim().toLowerCase())
      .filter((scope) => architectScopeNames.has(scope) && !['setup', 'starter', 'all'].includes(scope)))];
  }
  function readableArchitectScopes(scopes) {
    const labels = { world: 'world', premise: 'opening', cast: 'people', first_day: 'possible scenes', time_system: 'schedule', all: 'story' };
    return scopes.map((scope) => labels[scope] || scope).join(', ');
  }
  function architectRouteLabel(route) {
    if (!route || typeof route !== 'object') return '';
    const label = String(route.label || route.model || '').trim();
    const selected = String(route.selected_model || '').trim();
    if (route.used_fallback && selected) return `${label || 'Story Agent'} → ${selected} fallback`;
    return label || selected;
  }
  function architectFailure(data, status) {
    const failure = new Error(data?.error || 'The Story Architect could not take this turn');
    failure.status = status;
    failure.retryable = Boolean(data?.retryable);
    failure.modelRoute = data?.model_route && typeof data.model_route === 'object'
      ? data.model_route
      : null;
    return failure;
  }
  function canRetryArchitectFallback(error) {
    const route = error?.modelRoute;
    return error?.status === 504
      && error?.retryable === true
      && route?.fallback_available === true
      && route?.used_fallback !== true;
  }
  async function assessArchitect({ announce = true } = {}) {
    const request = ++architectRequest;
    architectError = '';
    try {
      const res = await fetch(`/api/stories/${storyKey}/card/gaps`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Could not assess the story card');
      if (request !== architectRequest) return data;
      architectGaps = Array.isArray(data?.gaps) ? data.gaps : [];
      architectSummary = typeof data?.summary === 'string'
        ? data.summary
        : (data?.summary?.message || '');
      if (announce) {
        architectMessages = [...architectMessages, {
          role: 'assistant', kind: 'progress',
          text: architectSummary || 'I assessed the current story card.',
          meta: architectGaps.length ? `${architectGaps.length} concrete connection${architectGaps.length === 1 ? '' : 's'} to consider.` : 'No structural gaps detected.'
        }];
      }
      return data;
    } catch (err) {
      if (request === architectRequest) architectError = err.message || 'Could not assess the story card';
      return null;
    }
  }
  function applyArchitectResponse(data, { run, before = [], userText = '' } = {}) {
    if (run !== architectRun) return;
    architectFallback = null;
    const architectState = data?.architect;
    if (architectState && typeof architectState === 'object') {
      architectMission = typeof architectState.mission === 'string' ? architectState.mission.trim() : '';
      architectActions = Array.isArray(architectState.actions)
        ? architectState.actions.filter((action) => action && typeof action === 'object')
        : [];
      architectExecutionReady = architectState.execution_ready === true;
    } else {
      architectExecutionReady = false;
    }
    if (data?.plan && typeof data.plan === 'object') architectPlan = data.plan;
    if (data?.control_graph && typeof data.control_graph === 'object') {
      controlGraph = data.control_graph;
    }
    const authorPlan = responseAuthorPlan(data);
    if (authorPlan) architectAuthorPlan = authorPlan;
    const hasGaps = Array.isArray(data?.gaps)
      || Array.isArray(data?.gaps?.gaps)
      || Array.isArray(data?.assessment?.gaps)
      || Array.isArray(data?.card_gaps?.gaps);
    if (hasGaps) architectGaps = responseArchitectGaps(data);
    const summary = responseArchitectSummary(data);
    if (summary) architectSummary = summary;
    const route = data?.model_route || data?.route || data?.agent_route;
    if (route) architectRoute = typeof route === 'string' ? route : architectRouteLabel(route);
    const nextStory = data?.story || data?.card || data?.updated_story;
    if (nextStory) current = nextStory;
    if (data?.readiness) readiness = data.readiness;
    // Readiness is supporting chrome, never a reason to hold the architect's
    // first question hostage.  In particular, a bootstrap must settle even
    // if another local request is slow or being restarted.
    else if (nextStory) void refreshReadiness();

    const history = architectAuthorHistory(data, before);
    const reply = responseArchitectReply(data);
    const questionValue = responseArchitectQuestion(data);
    const rawAnswerTo = data?.answer_to || data?.question_id || questionValue?.answer_to || questionValue?.id || '';
    const answerTo = typeof rawAnswerTo === 'string' ? rawAnswerTo : String(rawAnswerTo?.id || '');
    const parsedQuestion = compactArchitectText(questionValue);
    if (parsedQuestion) {
      setArchitectQuestion(questionValue, { answerTo, force: true });
    } else {
      // Do not revive a stale decision from a generic gap list.  A quiet
      // response means the Architect is still planning/implementing, not
      // that its worker diagnostics became questions for the author.
      setArchitectQuestion(null);
    }
    const transcript = [...history];
    if (userText && transcript.at(-1)?.text !== userText) transcript.push({ role: 'user', text: userText });
    // Project every completed worker/implementation result into one compact
    // progress entry. The persisted history deliberately never supplies
    // worker text to this surface, so a worker cannot create a second author
    // question or a backlog of stale questions here.
    if (reply || (data?.action && data.action !== 'assess')) transcript.push(architectProgressEntry(data, reply));
    const lastText = transcript.at(-1)?.text?.trim();
    // A complete Architect plan is the primary author-facing artifact. The
    // server's legacy/current question remains available as a small feedback
    // cue inside that plan, rather than becoming a second, dominant chat
    // prompt next to it.
    const hasVisibleAuthorPlan = Array.isArray(architectAuthorPlan?.sections)
      && architectAuthorPlan.sections.length > 0;
    if (architectQuestion && architectQuestion !== lastText && !hasVisibleAuthorPlan) {
      transcript.push({
        role: 'assistant', kind: 'question', text: architectQuestion,
        meta: 'Current author decision — the Architect will handle the connected implementation.'
      });
    }
    architectMessages = transcript.length ? transcript : [{
      role: 'assistant', kind: 'progress', text: 'The Architect is holding the current plan.',
      meta: 'No author decision is waiting right now.'
    }];
    const changed = data?.next_focus || data?.updated_section || data?.focus
      || (Array.isArray(data?.updated_sections) ? data.updated_sections[0] : '');
    if (changed && stages.some((stage) => stage.id === changed)) focus = changed;
    if (data?.created || data?.created_characters) {
      void loadChars().catch(() => { /* names refresh on the next app update */ });
    }
    // Do not emit `story:refresh` from a bootstrap.  The route shell replaces
    // this component for that event; doing so here created a reload loop:
    // bootstrap → refresh → remount → bootstrap.  `current` already carries
    // the returned card, and secondary views receive it directly.
    window.dispatchEvent(new CustomEvent('queue:refresh'));
  }
  async function requestArchitectTurn(message, { run, before = [], bootstrap = false, complete = false, newMission = false, useFallback = false, commentOn = '' } = {}) {
    let res;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), architectRequestTimeoutMs);
    try {
      const payload = { message: String(message || '') };
      // The server can resolve its persisted pending question without this,
      // but carrying the opaque id makes an answer/replay relationship
      // explicit when the endpoint has supplied one.
      // A completion request is a new bounded mission, not an answer routed
      // through the old question's narrow section. The server owns the plan.
      // A selected plan section has its own server-side whitelist. Do not
      // carry a legacy pending-question id beside it: the selected plan
      // section must be the single routing authority for this feedback.
      if (commentOn) payload.comment_on = commentOn;
      else if (architectAnswerTo && !complete) payload.answer_to = architectAnswerTo;
      if (complete) payload.mode = 'complete';
      if (newMission) payload.new_mission = true;
      if (useFallback) payload.use_fallback = true;
      res = await fetch(`/api/stories/${storyKey}/architect/turn`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), signal: controller.signal
      });
    } catch (err) {
      if (err?.name === 'AbortError') {
        throw new Error('The Story Architect did not respond within 45 seconds. Send the same direction again to re-check the latest story card.');
      }
      throw new Error(err?.message || 'Could not reach the Story Architect');
    } finally {
      clearTimeout(timeout);
    }
    // Older servers do not have the persisted Architect route yet.  The
    // fallback below keeps the current workspace usable during an upgrade,
    // but normal operation always uses the server-owned agent loop.
    if (res.status === 404) return { unsupported: true };
    let data;
    try { data = await res.json(); } catch { data = {}; }
    // A model turn can finish after a browser abort, or the card can be
    // edited in another authoring surface while an answer is being composed.
    // The opaque `answer_to` then correctly receives a 409.  Treat that as a
    // normal resync, not as a dead-end error: fetch the current persisted
    // question without replaying the stale answer, leave the draft available,
    // and let the author decide whether it still applies.
    if (res.status === 409 && !bootstrap) {
      try {
        const refresh = await fetch(`/api/stories/${storyKey}/architect/turn`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: '' })
        });
        let latest;
        try { latest = await refresh.json(); } catch { latest = {}; }
        if (refresh.ok) {
          if (run !== architectRun) return { stale: true, data: latest };
          applyArchitectResponse(latest, { run, before, userText: '' });
          return { staleQuestion: true, data: latest };
        }
      } catch {
        // Fall through to the server's original conflict message.  The
        // normal error handling below preserves the draft either way.
      }
    }
    if (!res.ok) throw architectFailure(data, res.status);
    if (run !== architectRun) return { stale: true, data };
    applyArchitectResponse(data, { run, before, userText: bootstrap ? '' : String(message || '').trim() });
    return { data };
  }
  async function startArchitect({ force = false } = {}) {
    if (!storyKey || (!force && architectSessionKey === storyKey)) return;
    architectSessionKey = storyKey;
    const run = ++architectRun;
    architectError = '';
    architectTyped = '';
    architectQuestion = '';
    architectAnswerTo = '';
    architectQuestionKey = '';
    architectSummary = '';
    architectGaps = [];
    architectMission = '';
    architectActions = [];
    architectPlan = null;
    architectAuthorPlan = null;
    architectExecutionReady = false;
    architectRoute = '';
    architectFallback = null;
    architectActivity = 'Reading the live story card…';
    architectBusy = true;
    architectMessages = [{ role: 'assistant', text: '', loading: true }];
    try {
      const result = await requestArchitectTurn('', { run, before: [], bootstrap: true });
      if (run !== architectRun) return;
      if (result?.unsupported) {
        architectActivity = 'Mapping the current story…';
        const assessed = await assessArchitect({ announce: false });
        if (run !== architectRun) return;
        architectMessages = [{
          role: 'assistant', kind: 'progress',
          text: 'I have the live card. I will make connected, validated additions myself and pause only when a decision needs your intent.',
          meta: 'Local planner fallback — the persisted Story Architect will take over when available.'
        }];
        setArchitectQuestion(null, { announce: true, force: true, allowGapFallback: true });
        if (assessed?.summary) architectSummary = responseArchitectSummary(assessed);
      }
    } catch (err) {
      if (run === architectRun) {
        architectMessages = [];
        architectError = err.message || 'Could not prepare the Story Architect';
      }
    } finally {
      if (run === architectRun) {
        architectBusy = false;
        architectActivity = 'Waiting for your direction';
      }
    }
  }
  function reviewArchitectDecision() {
    // This is intentionally a blank Architect bootstrap: it refreshes the
    // persisted plan/question without turning the button into a completion
    // request or sending a worker a fresh author direction.
    if (architectBusy || developing || busy || architectQuestion) return;
    void startArchitect({ force: true });
  }
  function architectDirectionContext(latest) {
    // The card remains the source of canon.  This merely preserves a few
    // author-level intentions between bounded passes, so "do that" retains
    // the direction of the immediately preceding architect turn without
    // replaying a long, increasingly expensive chat transcript.
    const prior = architectMessages
      .filter((message) => message?.role === 'user' && typeof message.text === 'string')
      .map((message) => message.text.trim())
      .filter(Boolean)
      .slice(-3);
    const directions = [...prior, latest].slice(-4);
    if (directions.length < 2) return latest;
    return `Continue the author's current direction. Earlier priorities:\n${directions
      .slice(0, -1).map((text) => `- ${text}`).join('\n')}\nCurrent request:\n- ${latest}`;
  }
  async function runArchitect(direction = architectTyped, { complete = false, newMission = false, useFallback = false, commentOn = '' } = {}) {
    if (architectBusy || developing || busy) return;
    const requested = String(direction || '').trim();
    // An approved protected plan may be applied without inventing a fake
    // author message. The server still validates its one-card authorization.
    if (!requested && !complete) return;
    const run = ++architectRun;
    const before = architectMessages;
    architectBusy = true;
    architectError = '';
    architectFallback = null;
    architectActivity = complete && !requested ? 'Applying the approved plan…' : 'Tracing what your direction changes…';
    architectTyped = '';
    architectMessages = requested
      ? [...before, { role: 'user', text: requested }, { role: 'assistant', text: '', loading: true }]
      : [...before, { role: 'assistant', text: '', loading: true }];
    try {
      const result = await requestArchitectTurn(requested, { run, before, complete, newMission, useFallback, commentOn });
      if (run !== architectRun) return;
      if (result?.unsupported) {
        // The safe legacy path still infers scope from the live diagnostic;
        // it never asks the author to pick a target.  It is only used while
        // an older local server is running during migration.
        architectMessages = before;
        architectBusy = false;
        // An older server cannot validate an opaque approved-plan action, so
        // never fall through to the legacy broad worker for an empty request.
        if (!requested) {
          architectError = 'This server cannot apply the approved plan safely. Refresh the Story Architect and try again.';
          return;
        }
        return await runLegacyArchitect(requested);
      }
      if (result?.staleQuestion) {
        // `requestArchitectTurn` has already rendered the fresh question.  A
        // stale answer is not lost; it remains in the composer for a conscious
        // retry against the newly observed card instead of being sent twice.
        architectTyped = requested;
        architectError = 'The card changed while that feedback was in flight. I refreshed the current plan; review it, then resend or revise your draft.';
      }
    } catch (err) {
      if (run === architectRun) {
        architectMessages = before;
        // Preserve the author’s direction so a transient model/provider
        // failure can be retried directly from the composer.
        architectTyped = requested;
        architectError = err.message || 'The Story Architect could not complete this turn';
        if (err?.modelRoute) architectRoute = architectRouteLabel(err.modelRoute);
        // The retry is deliberately separate from this failed request.  It
        // only appears for a server-confirmed, retryable timeout on the
        // primary route; a fallback timeout never cascades into another call.
        if (canRetryArchitectFallback(err)) {
          architectFallback = {
            direction: requested,
            complete,
            newMission,
            commentOn,
            route: err.modelRoute
          };
        }
      }
    } finally {
      if (run === architectRun) {
        architectBusy = false;
        architectActivity = 'Waiting for your direction';
      }
    }
  }
  function retryArchitectWithFallback() {
    const retry = architectFallback;
    if (!retry || architectBusy || developing || busy) return;
    void runArchitect(retry.direction, {
      complete: Boolean(retry.complete),
      newMission: Boolean(retry.newMission),
      useFallback: true,
      commentOn: String(retry.commentOn || '')
    });
  }
  async function runLegacyArchitect(direction = architectTyped) {
    if (architectBusy || developing || busy) return;
    const run = ++architectRun;
    const requestedBrief = String(direction || '').trim() || 'Fill the highest-leverage missing connections while preserving the current tone, central relationships, and established canon.';
    const brief = architectDirectionContext(requestedBrief);
    architectBusy = true;
    architectError = '';
    architectActivity = 'Mapping the current story…';
    architectTyped = '';
    const before = architectMessages;
    architectMessages = [...before, { role: 'user', text: requestedBrief }, { role: 'assistant', text: '', loading: true }];
    try {
      const assessed = await assessArchitect({ announce: false });
      if (run !== architectRun) return;
      const gaps = assessed?.gaps || architectGaps;
      const inferredScopes = architectScopes(gaps);
      // A connected card can still use a high-level author direction to add
      // a new, compatible possibility.  The safe developer remains additive.
      const scopes = inferredScopes.length ? inferredScopes : ['all'];
      const needsArcTurn = gaps.some((gap) => gap?.section === 'arcs');
      const arcOnly = !inferredScopes.length && needsArcTurn;
      if (arcOnly) {
        architectActivity = 'Protecting the private arc layer…';
        // Arcs carry private pressure, so they must use the existing
        // visibility-aware interview boundary rather than the additive setup
        // builder.  The same high-level direction becomes an arcs-only turn.
        const res = await fetch(`/api/stories/${storyKey}/interview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            target: { section: 'arcs' }, section: 'arcs', focus: 'arcs',
            messages: [{ role: 'user', text: brief }]
          })
        });
        const data = await res.json();
        if (run !== architectRun) return;
        if (!res.ok) throw new Error(data?.error || 'The story agent could not shape the character pressure');
        architectRoute = architectRouteLabel(data?.model_route);
        current = data.story || await loadStory(storyKey);
        readiness = data.readiness || await refreshReadiness();
        architectMessages = [...before, { role: 'user', text: requestedBrief }, {
          role: 'assistant', kind: 'progress', text: data?.reply || 'I drafted the next character-pressure decision.',
          meta: 'Updated the private theme-and-arc design through the protected arcs boundary.'
        }];
        focus = 'arcs';
      } else {
        architectActivity = 'Fleshing out connected story material…';
        const res = await fetch(`/api/stories/${storyKey}/card/develop`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scope: scopes,
            brief
          })
        });
        const data = await res.json();
        if (run !== architectRun) return;
        if (!res.ok) throw new Error(data?.error || 'The story agent could not complete this pass');
        architectRoute = architectRouteLabel(data?.model_route);
        current = data.story || await loadStory(storyKey);
        try { await loadChars(); } catch { /* display names refresh on the next app update */ }
        readiness = data.readiness || await refreshReadiness();
        const sections = Array.isArray(data?.updated_sections) ? data.updated_sections : scopes;
        const created = createdCharacterNames(data);
        const reply = data?.reply || data?.message || `Completed a bounded pass across ${readableArchitectScopes(sections)}.`;
        let arcReply = '';
        let arcMeta = '';
        if (needsArcTurn) {
          // A public card pass can safely establish people and scene offers,
          // but arc pressure must cross the protected interviewer boundary.
          // Keep this to one follow-up turn: it can draft the next private
          // decision or ask for the author judgement it genuinely needs.
          try {
            const arcRes = await fetch(`/api/stories/${storyKey}/interview`, {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                target: { section: 'arcs' }, section: 'arcs', focus: 'arcs',
                messages: [{ role: 'user', text: brief }]
              })
            });
            const arcData = await arcRes.json();
            if (run !== architectRun) return;
            if (!arcRes.ok) throw new Error(arcData?.error || 'The protected arc turn could not run');
            architectRoute = architectRouteLabel(arcData?.model_route) || architectRoute;
            current = arcData.story || await loadStory(storyKey);
            readiness = arcData.readiness || await refreshReadiness();
            arcReply = arcData?.reply || 'I identified the next private character-pressure decision.';
            arcMeta = 'Protected arc turn completed.';
            focus = 'arcs';
          } catch (arcError) {
            // The public pass has already committed safely.  Do not hide that
            // work merely because the optional protected follow-up is delayed.
            arcMeta = `Arc follow-up deferred: ${arcError?.message || 'try again from Theme & arcs.'}`;
          }
        }
        architectMessages = [...before, { role: 'user', text: requestedBrief }, {
          role: 'assistant', kind: 'progress', text: arcReply ? `${reply}\n\n${arcReply}` : reply,
          meta: [
            sections.length ? `Updated ${readableArchitectScopes(sections)}.` : '',
            created.length ? `Created ${created.join(', ')}.` : '',
            arcMeta
          ].filter(Boolean).join(' ')
        }];
        if (!needsArcTurn) {
          focus = sections.includes('cast') ? 'cast' : (sections.includes('first_day') ? 'first_day' : (sections.includes('time_system') ? 'time_system' : focus));
        }
      }
      const changedFocus = focus;
      lastUpdated = changedFocus;
      setTimeout(() => { if (lastUpdated === changedFocus) lastUpdated = ''; }, 1100);
      architectActivity = 'Checking what needs your judgment next…';
      await assessArchitect({ announce: false });
      if (run !== architectRun) return;
      setArchitectQuestion(null, { announce: true, force: true, allowGapFallback: true });
      window.dispatchEvent(new CustomEvent('story:refresh', { detail: { key: storyKey } }));
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } catch (err) {
      if (run === architectRun) {
        architectMessages = before;
        architectError = err.message || 'The story agent could not complete this pass';
      }
    } finally {
      if (run === architectRun) {
        architectBusy = false;
        architectActivity = 'Waiting for your direction';
      }
    }
  }
  async function bootstrapTarget(target, request) {
    const resolved = normaliseTarget(target);
    const res = await fetch(`/api/stories/${storyKey}/interview`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: apiTarget(resolved), messages: [], section: resolved.section, focus: resolved.section, bootstrap: true })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data?.error || 'Could not prepare this conversation');
    if (request !== editorRequest) return;
    messages = responseHistory(data, [{ role: 'assistant', text: data.reply || 'What would you like to improve here?' }]);
    editorSuggestions = normaliseSuggestions(data?.conversation?.suggestions || data?.suggestions, resolved);
    current = data.story || current;
    readiness = data.readiness || await refreshReadiness();
  }
  async function openConversation(target = sectionTarget(focus)) {
    const resolved = normaliseTarget(target);
    const request = ++editorRequest;
    focus = resolved.section;
    focusedCardItem = resolved.item ? resolved.id : '';
    scrollCardTarget(resolved);
    editorTarget = resolved;
    typed = '';
    error = '';
    messages = [];
    editorSuggestions = defaultSuggestions(resolved);
    conversationOpen = true;
    busy = true;
    try {
      const res = await fetch(targetHistoryUrl(resolved));
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Could not load this conversation');
      if (request !== editorRequest) return;
      messages = responseHistory(data);
      editorSuggestions = normaliseSuggestions(data?.conversation?.suggestions || data?.suggestions, resolved);
      if (!messages.length) await bootstrapTarget(resolved, request);
    } catch (err) {
      if (request === editorRequest) {
        messages = [];
        error = err.message || 'Could not load this conversation';
      }
    } finally {
      if (request === editorRequest) busy = false;
    }
  }
  async function refreshControlGraph() {
    if (!storyKey) return null;
    try {
      const res = await fetch(`/api/stories/${encodeURIComponent(storyKey)}/control-graph`);
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Could not load story map');
      if (data?.graph && typeof data.graph === 'object') {
        controlGraph = data.graph;
        return data.graph;
      }
    } catch {
      // The Architect response also carries this projection; the card stays
      // fully usable while an older server is being refreshed.
    }
    return null;
  }
  function selectArchitectGraphNode(node) {
    if (!node || typeof node !== 'object') return;
    if (node.id === 'director') {
      workspaceMode = 'story';
      directorOpen = true;
      return;
    }
    if (node.target) void openConversation(node.target);
  }
  function closeConversation() {
    conversationOpen = false;
    editorRequest += 1;
    // A late history/bootstrap response must not leave the whole workspace
    // disabled after its popup has already been dismissed.
    busy = false;
  }
  function returnToStory(section = 'world') {
    focus = normaliseSection(section);
    focusedCardItem = '';
    workspaceMode = 'story';
    scrollCardTarget(sectionTarget(focus));
  }
  function selectDirectorItem(item) {
    directorOpen = false;
    if (item?.target) return openConversation({ ...item.target, label: item.label, summary: item.summary, field: item.field || item.target?.field });
    if (item?.kind === 'entity-period') return openConversation(itemTarget('time_system', 'entity_period', item.id, item.period?.label || item.id, item.period?.constraint || 'Entity activity window.'));
    if (item?.kind === 'scene') {
      if (item.id === 'opening-state') return openConversation(itemTarget('premise', 'opening_state', 'opening-state', 'Opening position', item.scene?.publicSurface || 'The state that begins play.'));
      return openConversation(itemTarget('first_day', 'scene', item.id, item.scene?.title || item.id, item.scene?.publicSurface || item.scene?.trigger || 'A possible scene.'));
    }
    if (item?.kind === 'character-arc') return openConversation(itemTarget('arcs', 'arc', item.id, item.arc?.owner || item.id, item.arc?.theme || 'A character arc.'));
    const issue = item?.issue || item;
    return openConversation(itemTarget(issue?.section || 'world', 'readiness_issue', issue?.code || issue?.path || issue?.message || 'issue', issue?.message || 'Runtime decision', issue?.fix || 'Resolve this before play.'));
  }
  async function refreshReadiness() {
    try {
      const res = await fetch(`/api/stories/${storyKey}/play-readiness`);
      const data = await res.json();
      if (res.ok) readiness = data;
      return data;
    } catch { return null; }
  }
  function resolveBlocker(blocker) {
    workspaceMode = 'architect';
    const prompt = `Resolve this play-readiness constraint without changing established canon: ${blocker.message || 'The story needs one runtime decision.'}${blocker.fix ? ` ${blocker.fix}` : ''}`;
    if (architectBusy) architectTyped = prompt;
    else void runArchitect(prompt);
  }
  async function activatePlay() {
    if (activating || busy || developing || !readiness?.ready) return;
    activating = true; error = '';
    try {
      const res = await fetch(`/api/stories/${storyKey}/activate`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        readiness = data?.readiness || readiness;
        throw new Error(data?.error || 'This card still needs a few decisions');
      }
      window.dispatchEvent(new CustomEvent('story:refresh', { detail: { key: storyKey } }));
      current = await loadStory(storyKey);
      workspaceMode = 'play';
    } catch (err) { error = err.message || 'Could not start play'; }
    finally { activating = false; }
  }
  async function organizeCard() {
    if (organizing || busy || developing) return;
    organizing = true; error = '';
    try {
      const res = await fetch(`/api/stories/${storyKey}/card/organize`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Could not organize the card');
      current = data.story || await loadStory(storyKey);
      await refreshReadiness();
      lastUpdated = 'world';
      setTimeout(() => { if (lastUpdated === 'world') lastUpdated = ''; }, 1100);
    } catch (err) { error = err.message || 'Could not organize the card'; }
    finally { organizing = false; }
  }
  async function reviewCard() {
    if (reviewing || busy || developing) return;
    reviewing = true; error = '';
    try {
      const res = await fetch(`/api/stories/${storyKey}/card/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Could not review the card');
      const reviewed = data.suggestions || [];
      const target = editorTarget || sectionTarget(focus);
      editorTarget = target;
      editorSuggestions = normaliseSuggestions(reviewed, target);
      messages = [...messages, { role: 'assistant', text: 'Here are the highest-leverage improvements to consider next.' }];
      conversationOpen = true;
    } catch (err) { error = err.message || 'Could not review the card'; }
    finally { reviewing = false; }
  }
  function createdCharacterNames(data) {
    const created = Array.isArray(data?.created)
      ? data.created
      : (data?.created?.characters || data?.characters || data?.created_characters || []);
    return (Array.isArray(created) ? created : [])
      .map((character) => typeof character === 'string' ? character : (character?.name || character?.key || ''))
      .filter(Boolean);
  }
  async function developCard() {
    if (developing || busy || reviewing || organizing) return;
    developing = true; error = '';
    const scope = developmentScopes.find((item) => item.id === developScope);
    try {
      const res = await fetch(`/api/stories/${storyKey}/card/develop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scope: developScope, brief: developBrief.trim() })
      });
      const data = await res.json();
      if (!res.ok) {
        const fallback = res.status === 404
          ? 'Batch authoring is not available for this story yet.'
          : 'Could not build the starting set';
        throw new Error(data?.error || fallback);
      }
      const created = createdCharacterNames(data);
      const reply = data.reply || data.message || data.summary ||
        (created.length
          ? `Built a ${scope?.label?.toLowerCase() || 'starting set'} and created ${created.join(', ')}.`
          : `Built a ${scope?.label?.toLowerCase() || 'starting set'} from the current card.`);
      messages = [...messages, {
        role: 'assistant', text: reply,
        development: { scope: scope?.label || 'Starting set', created }
      }];
      if (!editorTarget) editorTarget = sectionTarget(developScope === 'cast' ? 'cast' : 'premise');
      current = data.story || await loadStory(storyKey);
      try { await loadChars(); } catch { /* names still fall back safely until the next app refresh */ }
      readiness = data.readiness || await refreshReadiness();
      focus = data.next_focus || stageFor(data.patch || {});
      lastUpdated = focus;
      setTimeout(() => { if (lastUpdated === focus) lastUpdated = ''; }, 1100);
      developOpen = false;
      developBrief = '';
      window.dispatchEvent(new CustomEvent('story:refresh', { detail: { key: storyKey } }));
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } catch (err) {
      error = err.message || 'Could not build the starting set';
    } finally { developing = false; }
  }
  function chooseSuggestion(suggestion) {
    typed = suggestion?.prompt || suggestion?.detail || suggestion?.title || '';
  }
  async function send(text = typed) {
    const value = text.trim(); if (!value || busy || developing) return;
    const target = normaliseTarget(editorTarget || sectionTarget(focus));
    const request = editorRequest;
    typed = ''; error = ''; busy = true;
    const before = messages;
    const pending = [...before, { role: 'user', text: value, target: target.id }, { role: 'assistant', text: '', loading: true }];
    messages = pending;
    try {
      const res = await fetch(`/api/stories/${storyKey}/interview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target: apiTarget(target),
          messages: pending.filter((message) => !message.loading),
          section: target.section,
          focus: target.section
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || 'Interview failed');
      if (request !== editorRequest || editorTarget?.id !== target.id) return;
      const fallback = [...before, { role: 'user', text: value, target: target.id }, { role: 'assistant', text: data.reply || 'Okay.' }];
      messages = responseHistory(data, fallback);
      editorSuggestions = normaliseSuggestions(data?.conversation?.suggestions || data?.suggestions, target);
      current = data.story || await loadStory(storyKey);
      readiness = data.readiness || await refreshReadiness();
      const changedSection = data.next_focus || stageFor(data.patch);
      lastUpdated = changedSection;
      setTimeout(() => { if (lastUpdated === changedSection) lastUpdated = ''; }, 1100);
      window.dispatchEvent(new CustomEvent('story:refresh', { detail: { key: storyKey } }));
      window.dispatchEvent(new CustomEvent('queue:refresh'));
    } catch (err) {
      if (request === editorRequest) {
        messages = before;
        error = err.message || 'Interview failed';
      }
    } finally { if (request === editorRequest) busy = false; }
  }
  function toggleMic() {
    if (voice.state === 'listening') { stopListening(); return; }
    if (!startListening((text) => send(text))) voice.supported = false;
  }
  $effect(() => {
    // The persisted Architect owns its pending question.  Bootstrapping once
    // per story lets it resume or ask the first useful question immediately,
    // without an author first opening a modal or selecting a scope.
    if (!storyKey || architectSessionKey === storyKey) return;
    void startArchitect();
  });
  $effect(() => {
    if (readinessKey === storyKey) return;
    readinessKey = storyKey;
    refreshReadiness();
  });
  $effect(() => {
    if (!storyKey || controlGraphKey === storyKey) return;
    controlGraphKey = storyKey;
    controlGraph = null;
    void refreshControlGraph();
  });
  $effect(() => {
    if (!card || !focus) return;
    const target = card.querySelector(`[data-card-section="${focus}"]`);
    if (target) card.scrollTo({ top: Math.max(0, target.offsetTop - 42), behavior: 'smooth' });
  });
</script>

<main class="interview">
  <header class="workspace-header">
    <div>
      <span class="eyebrow">Story control room</span>
      <h1>{current?.name || 'Untitled story'}</h1>
      <p>{workspaceModes.find((item) => item.id === workspaceMode)?.hint}</p>
    </div>
    <div class="workspace-tabs" role="tablist" aria-label="Story workspace">
      {#each workspaceModes as item}
        <button type="button" role="tab" aria-selected={workspaceMode === item.id} class:active={workspaceMode === item.id} onclick={() => (workspaceMode = item.id)}>{item.label}</button>
      {/each}
      {#if readiness}
        <span class:ready={readiness.ready} class="workspace-readiness">{readiness.ready ? 'Ready' : `${readinessItems.length} to resolve`}</span>
      {/if}
    </div>
  </header>

  {#if workspaceMode === 'architect'}
    <section class="workspace-panel architect-panel" aria-label="Story Architect workspace">
      <StoryArchitectModal
        messages={architectMessages}
        gaps={architectGaps}
        summary={architectSummary}
        mission={architectMission}
        actions={architectActions}
        plan={architectPlan}
        authorPlan={architectAuthorPlan}
        executionReady={architectExecutionReady}
        question={architectQuestion}
        activity={architectActivity}
        route={architectRoute}
        busy={architectBusy}
        error={architectError}
        fallbackRetry={architectFallback}
        bind:typed={architectTyped}
        onrun={runArchitect}
        onreview={reviewArchitectDecision}
        onexecute={() => runArchitect('', { complete: true })}
        onfallback={retryArchitectWithFallback}
      />
    </section>
  {:else if workspaceMode === 'story'}
  <section class="card-workspace" aria-label="Story card workspace">
    <StoryControlFlow
      graph={controlGraph}
      story={current}
      selected={({ world: 'world', premise: 'opening', cast: 'cast', arcs: 'arcs', first_day: 'scenes', time_system: 'director' })[focus] || focus}
      bind:compact={controlGraphCompact}
      onselect={selectArchitectGraphNode}
    />

  <aside class="story-rail" aria-label="Story card actions and readiness">
  <div class="cardhead"><span>Story tools</span><span class="cardactions"><button type="button" class="edit-card" onclick={() => openConversation(focus)} disabled={busy || developing}>Edit {stages.find((stage) => stage.id === focus)?.label || 'section'}</button><button type="button" class="director-tools" onclick={() => (directorOpen = true)} disabled={busy || developing} aria-haspopup="dialog" aria-expanded={directorOpen}>Director tools</button><button type="button" class="develop" onclick={() => (developOpen = !developOpen)} disabled={developing || busy || reviewing || organizing} aria-expanded={developOpen}>{developing ? 'Building…' : 'Build'}</button><button type="button" class="organize" onclick={reviewCard} disabled={reviewing || busy || developing}>{reviewing ? 'Reviewing…' : 'Review'}</button><button type="button" class="organize" onclick={organizeCard} disabled={organizing || busy || developing}>{organizing ? 'Organizing…' : 'Organize'}</button></span></div>
  {#if developOpen}
    <section class="develop-panel" aria-label="Build a starting set">
      <div class="develop-title"><div><strong>Build a starting set</strong><p>One agent pass from this card—not a new interview. It can add canon and make real character cards.</p></div><button type="button" class="close-develop" onclick={() => (developOpen = false)} aria-label="Close build setup">×</button></div>
      <div class="develop-scopes" role="radiogroup" aria-label="What to build">
        {#each developmentScopes as scope}
          <button type="button" role="radio" aria-checked={developScope === scope.id} class:selected={developScope === scope.id} onclick={() => (developScope = scope.id)}><b>{scope.label}</b><span>{scope.hint}</span></button>
        {/each}
      </div>
      <label class="develop-brief">Optional direction<textarea bind:value={developBrief} disabled={developing} placeholder="Keep Shuri central; add two people with conflicting reasons to be at the dock…"></textarea></label>
      <div class="develop-actions"><button type="button" class="cancel-develop" onclick={() => (developOpen = false)} disabled={developing}>Cancel</button><button type="button" class="run-develop" onclick={developCard} disabled={developing}>{developing ? 'Building…' : `Build ${developmentScopes.find((scope) => scope.id === developScope)?.label || 'set'}`}</button></div>
    </section>
  {/if}
  {#if readiness}
    <div class:ready={readiness.ready} class="readiness">
      {#if readiness.ready}
        <span>Ready to play</span><button onclick={activatePlay} disabled={activating || busy || developing}>{activating ? 'Starting…' : 'Start play'}</button>
      {:else}
        <span>Needs {readinessItems.length} runtime decision{readinessItems.length === 1 ? '' : 's'}</span>
        <div class="readiness-items">
          {#each readinessItems as blocker}
            <button onclick={() => resolveBlocker(blocker)}><b>{blocker.section}</b><span>{blocker.message}{#if blocker.count > 1} ({blocker.count} scenes){/if}</span>{#if blocker.fix}<small>{blocker.fix}</small>{/if}</button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}
  {#if current?.fields?.open_questions?.length}
    <div class="questions"><span>Still open</span>{#each current.fields.open_questions as question, index}<button type="button" class="card-leaf inline-leaf" onclick={() => openConversation(itemTarget('world', 'author_note', `open-question-${index}`, 'Open question', question, 'open_questions'))}>{question}</button>{#if index < current.fields.open_questions.length - 1}<i> · </i>{/if}{/each}</div>
  {/if}
  {#if current?.fields?.author_notes?.length}
    <div class="notes author-notes"><span>Author-only notes</span><small>Never sent to a model</small>{#each current.fields.author_notes as note, index}<button type="button" class="card-leaf rule-leaf" onclick={() => openConversation(itemTarget('world', 'author_note', `note-${index}`, 'Author-only note', authorNote(note)))}>{authorNote(note)}</button>{/each}</div>
  {/if}
  </aside>
  </section>

  {:else}
    <section class="workspace-panel play-panel" aria-label="Play workspace">
      <PlaySurface storyKey={storyKey} story={current} {readiness} onedit={returnToStory} />
    </section>
  {/if}

  {#if directorOpen}
    <div class="director-overlay" role="presentation" onclick={(event) => { if (event.target === event.currentTarget) directorOpen = false; }}>
      <section class="director-sheet" role="dialog" aria-modal="true" aria-labelledby="director-tools-title" tabindex="-1">
        <header class="director-sheet-head">
          <div>
            <span>Advanced author controls</span>
            <h2 id="director-tools-title">Director tools</h2>
            <p>Use this only to inspect or place private scene logic. The story card remains the everyday source of truth.</p>
          </div>
          <button type="button" class="close-director" onclick={() => (directorOpen = false)} aria-label="Close Director tools">Close</button>
        </header>
        <div class="director-sheet-body">
          <DirectorBoard
            storyKey={storyKey}
            onselect={selectDirectorItem}
            onstorychange={applyDirectorStory}
            onarchitect={applyDirectorArchitectUpdate}
          />
        </div>
      </section>
    </div>
  {/if}

  {#if conversationOpen}
    <EditorialModal
      target={editorTarget}
      {messages}
      suggestions={editorSuggestions}
      busy={busy || developing}
      {error}
      bind:typed
      voiceSupported={voice.supported}
      listening={voice.state === 'listening'}
      onclose={closeConversation}
      onsend={send}
      onchoose={chooseSuggestion}
      ontogglemic={toggleMic}
    />
  {/if}
</main>

<style>
  .interview { box-sizing: border-box; max-width: 1180px; height: calc(100dvh - var(--chrome-top, 0px)); min-height: 520px; margin: 0 auto; padding: clamp(24px, 6vh, 64px) 24px 16px; display: grid; grid-template-columns: minmax(0, 1fr) 340px; grid-template-rows: auto auto minmax(0, 1fr) auto; column-gap: 28px; row-gap: 18px; }
  .workspace-header { grid-column: 1 / -1; }
  .workspace-header { display: flex; align-items: end; justify-content: space-between; gap: 20px; }
  .workspace-header > div:first-child { max-width: 650px; } .eyebrow { color: var(--accent); font-size: 11px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
  h1 { margin: 5px 0; font-size: clamp(30px, 5vw, 48px); letter-spacing: -.04em; } .workspace-header p { margin: 0; color: var(--muted); }
  .workspace-tabs { display: flex; align-items: center; gap: 5px; flex-wrap: wrap; justify-content: flex-end; }
  .workspace-tabs button { border: 1px solid var(--border-soft); border-radius: 8px; padding: 7px 10px; background: var(--elev); color: var(--muted); font: inherit; font-size: 12px; font-weight: 750; cursor: pointer; }
  .workspace-tabs button.active { border-color: color-mix(in srgb, var(--accent) 65%, var(--border)); background: color-mix(in srgb, var(--accent) 16%, var(--elev)); color: var(--text); }
  .workspace-readiness { margin-left: 5px; color: var(--faint); font-size: 11px; white-space: nowrap; } .workspace-readiness.ready { color: var(--good, #6ec77f); }
  .workspace-panel { grid-column: 1 / -1; grid-row: 2 / span 3; min-height: 0; }
  .architect-panel { height: 100%; }
  .card-workspace { grid-column: 1 / -1; grid-row: 2 / span 3; display: grid; grid-template-columns: minmax(420px, 1.25fr) minmax(320px, .75fr); gap: 18px; min-height: 0; overflow: hidden; }
  .story-rail { box-sizing: border-box; min-width: 0; min-height: 0; overflow: auto; padding: 14px; border: 1px solid var(--border-soft); border-radius: 14px; background: color-mix(in srgb, var(--panel) 88%, var(--elev)); scrollbar-width: thin; }
  .story-rail .cardhead { top: -14px; margin: -14px -14px 12px; padding: 14px 14px 10px; }
  .card-workspace .card { grid-column: auto; grid-row: auto; align-self: stretch; position: relative; top: auto; max-height: calc(100dvh - 160px); }
  .card { grid-column: 2; grid-row: 3 / span 2; align-self: start; position: sticky; top: 20px; max-height: calc(100dvh - 150px); overflow: auto; scroll-behavior: smooth; background: color-mix(in srgb, var(--accent) 7%, var(--panel)); border: 1px solid color-mix(in srgb, var(--accent) 26%, var(--border)); border-radius: 14px; padding: 14px 16px; } .cardhead { position: sticky; top: -14px; z-index: 1; display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin: -14px -16px 0; padding: 14px 16px 9px; background: var(--panel); color: var(--faint); font-size: 11px; text-transform: uppercase; letter-spacing: .07em; } .cardactions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; } .organize, .develop, .director-tools { border: 1px solid var(--border-soft); border-radius: 6px; padding: 4px 7px; background: var(--elev); color: var(--accent); font: inherit; font-size: 10px; font-weight: 800; letter-spacing: .04em; cursor: pointer; } .director-tools { color: var(--muted); } .director-tools:hover { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); color: var(--text); } .develop { border-color: color-mix(in srgb, var(--accent) 55%, var(--border)); background: color-mix(in srgb, var(--accent) 15%, var(--elev)); color: var(--text); } .organize:disabled, .develop:disabled, .director-tools:disabled { opacity: .55; cursor: default; } .develop-panel { margin: 0 0 12px; padding: 11px; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); background: color-mix(in srgb, var(--accent) 6%, var(--elev)); } .develop-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 9px; } .develop-title strong { display: block; color: var(--text); font-size: 12px; } .develop-title p { margin: 4px 0 0; color: var(--muted); font-size: 11px; line-height: 1.35; } .close-develop { flex: none; border: 0; padding: 0 2px; background: transparent; color: var(--muted); font: inherit; font-size: 18px; line-height: 1; cursor: pointer; } .develop-scopes { display: grid; gap: 6px; margin-top: 10px; } .develop-scopes button { display: grid; gap: 2px; width: 100%; padding: 7px 8px; border: 1px solid var(--border-soft); background: var(--panel); color: var(--muted); text-align: left; font: inherit; cursor: pointer; } .develop-scopes button.selected { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--panel)); } .develop-scopes b { color: var(--text); font-size: 11px; } .develop-scopes span { font-size: 10px; line-height: 1.3; } .develop-brief { display: grid; gap: 5px; margin-top: 10px; color: var(--faint); font-size: 10px; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; } .develop-brief textarea { box-sizing: border-box; width: 100%; min-height: 62px; resize: vertical; border: 1px solid var(--border-soft); padding: 7px 8px; background: var(--panel); color: var(--text); font: inherit; font-size: 11px; font-weight: 400; line-height: 1.35; letter-spacing: normal; text-transform: none; outline: 0; } .develop-brief textarea:focus { border-color: var(--accent); } .develop-actions { display: flex; justify-content: flex-end; gap: 7px; margin-top: 9px; } .cancel-develop, .run-develop { border: 1px solid var(--border-soft); padding: 6px 8px; background: transparent; color: var(--muted); font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; } .run-develop { border-color: var(--accent); background: var(--accent); color: #0b0e14; } .cancel-develop:disabled, .run-develop:disabled { opacity: .55; cursor: default; } .readiness { margin: 0 0 10px; padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--accent) 24%, var(--border)); background: var(--elev); color: var(--muted); font-size: 11px; line-height: 1.35; } .readiness.ready { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--good, #6ec77f); } .readiness > button { border: 0; border-radius: 6px; padding: 5px 8px; background: var(--accent); color: #0b0e14; font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; } .readiness > button:disabled { opacity: .55; cursor: default; } .readiness-items { display: grid; gap: 5px; margin-top: 7px; } .readiness-items button { display: grid; gap: 2px; width: 100%; border: 0; padding: 5px 0; background: transparent; color: var(--muted); text-align: left; font: inherit; font-size: 11px; line-height: 1.3; cursor: pointer; } .readiness-items button:hover { color: var(--text); } .readiness-items b { color: var(--accent); font-size: 9px; letter-spacing: .05em; text-transform: uppercase; } .readiness-items small { color: var(--faint); font-size: 10px; } .cardsection { width: 100%; display: block; text-align: left; padding: 10px 8px; border: 0; border-bottom: 1px solid var(--border-soft); background: transparent; color: var(--text); font: inherit; transition: color .18s, background .18s, box-shadow .18s; cursor: pointer; } .cardsection:hover { background: color-mix(in srgb, var(--accent) 4%, transparent); } .cardsection.active { background: color-mix(in srgb, var(--accent) 5%, transparent); box-shadow: inset 2px 0 0 var(--accent); } .cardsection.active .sectiontext, .cardsection.active .bonds { color: var(--text); } .cardsection.active > span:first-child { color: var(--accent); } .cardsection.updated { animation: cardupdate 1.1s ease-out; } .cardsection > span:first-child { color: var(--faint); font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; } .sectiontext { display: block; margin-top: 5px; line-height: 1.4; font-size: 12px; } .bonds { display: block; margin-top: 7px; color: var(--muted); font-size: 11px; line-height: 1.35; } .events { margin: 8px 0; padding-left: 18px; display: grid; gap: 8px; } .events li { padding-left: 2px; color: var(--muted); font-size: 12px; line-height: 1.35; } .events b, .events span { display: block; } .events b { color: var(--text); font-size: 11px; } .notes { margin: 8px 0; padding: 8px; border-left: 2px solid var(--accent); background: var(--elev); color: var(--muted); font-size: 11px; line-height: 1.35; } .periods { margin: 6px 0 0; padding: 0; list-style: none; display: grid; gap: 5px; color: var(--muted); font-size: 11px; } .periods b { color: var(--text); text-transform: capitalize; } .questions { margin-top: 10px; color: var(--muted); font-size: 12px; } .questions span { color: var(--faint); text-transform: uppercase; font-size: 10px; font-weight: 700; margin-right: 8px; } .chat-suggestions { align-self: stretch; display: grid; gap: 5px; } .chat-suggestions button { text-align: left; padding: 8px 10px; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); color: var(--text); font: inherit; font-size: 12px; line-height: 1.35; cursor: pointer; } .chat-suggestions b { display: block; color: var(--accent); font-size: 9px; text-transform: uppercase; letter-spacing: .05em; } .chat-suggestions small { display: block; margin-top: 3px; color: var(--muted); font-size: 10px; } @keyframes cardupdate { 0% { background: color-mix(in srgb, var(--good, #6ec77f) 20%, transparent); } 100% { background: transparent; } }
  .edit-card { border: 1px solid color-mix(in srgb, var(--accent) 56%, var(--border)); border-radius: 6px; padding: 4px 7px; background: color-mix(in srgb, var(--accent) 14%, var(--elev)); color: var(--text); font: inherit; font-size: 10px; font-weight: 800; letter-spacing: .04em; cursor: pointer; } .edit-card:disabled { opacity: .55; cursor: default; }
  .card-leaves { display: grid; gap: 5px; margin: 7px 0 10px; } .card-leaf { display: block; box-sizing: border-box; width: 100%; border: 0; background: transparent; color: inherit; font: inherit; text-align: left; cursor: pointer; } .card-leaf:hover { color: var(--text); } .card-leaf:focus-visible { outline: 1px solid var(--accent); outline-offset: 2px; } .card-detail { padding: 7px 8px; border: 1px solid transparent; background: color-mix(in srgb, var(--accent) 3%, transparent); color: var(--muted); font-size: 11px; line-height: 1.35; } .card-detail:hover, .card-detail.focused { border-color: color-mix(in srgb, var(--accent) 54%, var(--border)); background: color-mix(in srgb, var(--accent) 10%, transparent); } .card-detail.focused { box-shadow: inset 2px 0 0 var(--accent); } .card-detail b, .card-detail span, .card-detail small { display: block; } .card-detail b { color: var(--text); font-size: 11px; } .card-detail small { margin-top: 2px; color: var(--faint); font-size: 10px; } .rule-leaf { margin: 5px 0 0; padding: 2px 0; color: var(--muted); font-size: 11px; line-height: 1.35; } .rule-leaf:hover { color: var(--text); } .inline-leaf { display: inline; width: auto; color: inherit; font-size: inherit; line-height: inherit; } .inline-leaf:hover { color: var(--text); text-decoration: underline; }
  .scene-offer { display: grid; gap: 3px; }
  .scene-offer .scene-title { color: var(--text); font-weight: 730; }
  .scene-offer .scene-visible { color: var(--muted); }
  .scene-offer .scene-participants { color: var(--faint); }
  .scene-offer .scene-hook { color: var(--accent); font-weight: 700; }
  .scene-offer .scene-needs-hook { color: var(--faint); font-style: italic; }
  .scene-offers li.needs-hook .scene-offer { border-color: color-mix(in srgb, var(--accent) 28%, var(--border)); }
  .world-groups { display: grid; gap: 10px; margin: 10px 0 12px; }
  .world-group { scroll-margin-top: 68px; overflow: hidden; border: 1px solid var(--border-soft); border-radius: 10px; background: color-mix(in srgb, var(--accent) 3%, var(--elev)); transition: border-color .16s, box-shadow .16s, background .16s; }
  .world-group.focused { border-color: color-mix(in srgb, var(--accent) 58%, var(--border)); background: color-mix(in srgb, var(--accent) 7%, var(--elev)); box-shadow: inset 2px 0 0 var(--accent); }
  .world-group-head { padding: 9px 10px 8px; border-bottom: 1px solid color-mix(in srgb, var(--accent) 16%, var(--border-soft)); background: color-mix(in srgb, var(--accent) 4%, transparent); }
  .world-group-head > span { display: block; color: var(--accent); font-size: 10px; font-weight: 820; letter-spacing: .065em; text-transform: uppercase; }
  .world-group-head p { margin: 4px 0 0; color: var(--muted); font-size: 10.5px; line-height: 1.35; }
  .world-group-items { display: grid; gap: 5px; padding: 7px; }
  .world-detail { min-height: 0; }
  .world-detail b { text-transform: none; letter-spacing: normal; }
  .author-notes { border-left-color: color-mix(in srgb, var(--accent) 80%, var(--border)); } .author-notes > span { display: block; margin-bottom: 3px; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; } .author-notes small { display: block; color: var(--faint); font-size: 10px; } .author-notes p { margin: 6px 0 0; }
  .conversation-pane { grid-column: 1; min-height: 0; display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 0; overflow: visible; border: 1px solid var(--border-soft); border-radius: 14px; background: color-mix(in srgb, var(--panel) 88%, var(--elev)); } .conversation { grid-column: 2; display: flex; flex-direction: column; align-items: flex-start; gap: 9px; overflow-y: auto; min-height: 0; padding: 12px; scrollbar-width: none; -ms-overflow-style: none; } .conversation::-webkit-scrollbar, .card::-webkit-scrollbar { display: none; } .card { scrollbar-width: none; -ms-overflow-style: none; } .turn-index { grid-column: 1; grid-row: 1; position: sticky; top: 4px; align-self: stretch; z-index: 10; display: grid; width: 64px; overflow: visible; grid-template-rows: repeat(var(--turn-count), minmax(18px, 30px)); align-content: center; justify-items: stretch; gap: 0; margin: 0; padding: 0; } .turn-mark { position: relative; display: grid; align-items: center; justify-items: start; width: 64px; min-height: 18px; cursor: pointer; outline: 0; border-radius: 0; } .turn-mark .dash { width: var(--dash-width); height: 2px; border-radius: 0; background: var(--border); transition: width .09s linear, background .09s linear; } .turn-mark.selected .dash, .turn-mark:focus-visible .dash { background: var(--accent); } .turn-prompt { position: absolute; left: 40px; top: 50%; transform: translateY(-50%); z-index: 30; width: min(360px, calc(100vw - 120px)); max-height: 260px; overflow: auto; padding: 10px; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); border-radius: 0; background: color-mix(in srgb, var(--panel) 96%, #000); box-shadow: 0 7px 20px rgba(0,0,0,.24); color: var(--text); pointer-events: none; } .turn-prompt p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--text); font-size: 12px; line-height: 1.45; } .conversation > div { box-sizing: border-box; width: min(100%, 560px); padding: 10px 12px; line-height: 1.45; font-size: 14px; border-radius: 12px; background: var(--elev); } .conversation > .author { align-self: flex-end; background: var(--accent); color: #0b0e14; } .author-hidden-text { display: inline; margin: 0 1px; padding: 1px 4px; border: 1px solid color-mix(in srgb, currentColor 34%, transparent); border-radius: 3px; background: color-mix(in srgb, currentColor 9%, transparent); color: inherit; font-weight: 650; white-space: pre-wrap; -webkit-box-decoration-break: clone; box-decoration-break: clone; } .author-hidden-label { display: inline-block; margin-right: 4px; font-size: 9px; font-weight: 850; letter-spacing: .05em; text-transform: uppercase; vertical-align: .08em; } .development-result { margin-top: 9px; padding-top: 8px; border-top: 1px solid color-mix(in srgb, var(--accent) 24%, var(--border)); color: var(--muted); font-size: 11px; line-height: 1.35; } .development-result > span { color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; } .development-result p { margin: 4px 0; } .development-result button { border: 0; padding: 0; background: transparent; color: var(--accent); font: inherit; font-size: 11px; font-weight: 800; cursor: pointer; } .dots, .error { color: var(--muted); font-size: 13px; } .error { color: var(--bad, #d0655a); }
  form { grid-column: 1; display: flex; align-items: center; gap: 8px; padding: 8px; border: 1px solid var(--border-soft); border-radius: 15px; background: var(--elev); box-shadow: 0 -10px 30px color-mix(in srgb, var(--bg) 50%, transparent); } input { flex: 1; min-width: 0; border: 0; outline: 0; padding: 7px 5px; font: inherit; background: transparent; color: var(--text); } .private-note { display: inline-flex; align-items: center; gap: 5px; flex: none; min-height: 36px; padding: 0 8px; border: 1px solid color-mix(in srgb, var(--accent) 38%, var(--border)); border-radius: 10px; background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--muted); font: inherit; font-size: 11px; font-weight: 750; cursor: pointer; } .private-note svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; } .private-note:hover { border-color: var(--accent); color: var(--text); } .private-note:disabled { opacity: .4; cursor: default; } .icon { width: 36px; height: 36px; display: grid; place-items: center; flex: none; padding: 0; border: 1px solid var(--border-soft); border-radius: 10px; background: transparent; color: var(--muted); cursor: pointer; } .icon svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; } .mic:hover { color: var(--text); border-color: var(--muted); } .mic.listening { color: var(--bad, #d0655a); border-color: var(--bad, #d0655a); animation: pulse 1.1s infinite; } .send { color: #0b0e14; border-color: var(--accent); background: var(--accent); } .icon:disabled { opacity: .4; cursor: default; } @keyframes pulse { 50% { box-shadow: 0 0 0 5px color-mix(in srgb, var(--bad, #d0655a) 15%, transparent); } }
  .conversation-overlay { position: fixed; z-index: 100; inset: 0; display: grid; place-items: center; padding: 24px; background: color-mix(in srgb, #05070b 72%, transparent); backdrop-filter: blur(4px); }
  .conversation-modal { box-sizing: border-box; display: grid; grid-template-rows: auto minmax(0, 1fr) auto; gap: 12px; width: min(820px, calc(100vw - 48px)); height: min(760px, calc(100dvh - 48px)); padding: 18px; border: 1px solid color-mix(in srgb, var(--accent) 35%, var(--border)); border-radius: 18px; background: var(--panel); box-shadow: 0 24px 70px rgba(0, 0, 0, .5); }
  .director-overlay { position: fixed; z-index: 95; inset: 0; display: grid; justify-items: end; background: color-mix(in srgb, #05070b 65%, transparent); backdrop-filter: blur(3px); }
  .director-sheet { box-sizing: border-box; display: grid; grid-template-rows: auto minmax(0, 1fr); width: min(920px, calc(100vw - 28px)); height: 100dvh; border-left: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border)); background: var(--panel); box-shadow: -20px 0 64px rgba(0, 0, 0, .4); }
  .director-sheet-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 16px 18px; border-bottom: 1px solid var(--border-soft); background: linear-gradient(120deg, color-mix(in srgb, var(--accent) 9%, var(--panel)), var(--panel)); }
  .director-sheet-head span { color: var(--accent); font-size: 9px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; } .director-sheet-head h2 { margin: 4px 0; font-size: 20px; letter-spacing: -.02em; } .director-sheet-head p { max-width: 590px; margin: 0; color: var(--muted); font-size: 11px; line-height: 1.4; }
  .close-director { flex: none; border: 1px solid var(--border-soft); border-radius: 7px; padding: 7px 9px; background: var(--elev); color: var(--muted); font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; } .close-director:hover { border-color: var(--accent); color: var(--text); }
  .director-sheet-body { min-height: 0; overflow: auto; padding: 18px; scrollbar-width: thin; }
  .conversation-modal-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; } .conversation-modal-head h2 { margin: 4px 0; font-size: 20px; letter-spacing: -.02em; } .conversation-modal-head p { max-width: 570px; margin: 0; color: var(--muted); font-size: 12px; line-height: 1.4; }
  .close-conversation { width: 32px; height: 32px; flex: none; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); color: var(--muted); font: inherit; font-size: 21px; line-height: 1; cursor: pointer; } .close-conversation:hover { color: var(--text); border-color: var(--muted); }
  .conversation-modal .conversation-pane { grid-column: auto; min-height: 0; height: 100%; } .conversation-modal form { grid-column: auto; }
  @media (max-width: 900px) { .card-workspace { grid-template-columns: 1fr; overflow: auto; } .story-rail { overflow: visible; } .card-workspace .card { max-height: none; } }
  @media (max-width: 760px) { .interview { height: 100dvh; min-height: 0; padding: 24px 16px 12px; grid-template-columns: minmax(0, 1fr); grid-template-rows: auto auto auto minmax(0, 1fr) auto; gap: 14px; } .workspace-header { align-items: stretch; flex-direction: column; } .workspace-tabs { justify-content: flex-start; } .workspace-panel, .card-workspace { grid-row: 2 / span 4; } .card { grid-column: 1; grid-row: 3; position: static; } .card-workspace .card { grid-column: auto; grid-row: auto; } .conversation-pane { grid-row: 4; } form { grid-row: 5; } .conversation-overlay { padding: 12px; } .conversation-modal { width: 100%; height: calc(100dvh - 24px); padding: 14px; border-radius: 14px; } .conversation-modal .conversation-pane, .conversation-modal form { grid-row: auto; } .conversation-modal-head p { font-size: 11px; } .director-sheet { width: 100vw; border-left: 0; } .director-sheet-head, .director-sheet-body { padding: 14px; } }
</style>

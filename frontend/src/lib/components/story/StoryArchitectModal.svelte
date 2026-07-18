<script>
  /*
   * This used to be a secondary modal.  It is deliberately kept in its
   * original file so existing story workspaces can use it, but it now renders
   * the primary, persistent Architect surface.  The parent owns persistence
   * and the validated agent turn; this component only presents the current
   * conversation and the plan it is actively carrying forward.
   */
  let {
    messages = [],
    gaps = [],
    summary = '',
    mission = '',
    actions = [],
    plan = null,
    authorPlan = null,
    executionReady = false,
    reviewedPatch = '',
    question = '',
    activity = '',
    busy = false,
    error = '',
    fallbackRetry = null,
    route = '',
    docked = false,
    entryLabel = 'Story direction',
    entrySection = 'arcs',
    entrySummary = '',
    history = [],
    typed = $bindable(''),
    onrun = null,
    onreview = null,
    onexecute = null,
    onreject = null,
    onfallback = null,
    onclose = null,
    ondraft = null
  } = $props();

  let composer = $state(null);
  let thread = $state(null);
  let selectedPlanId = $state('');
  let selectedPlanTitle = $state('');
  let planReviewButton = $state(null);
  let planSheet = $state(null);
  let planSheetClose = $state(null);
  let lastEntryKey = $state('');
  // The plan is useful as a review surface, but it should never compete with
  // the active conversation for vertical space. Keep it in a separate sheet
  // the author opens deliberately from the compact review control.
  let planSheetOpen = $state(false);
  let historySheetOpen = $state(false);

  const planSectionMeta = [
    {
      id: 'opening',
      label: 'Opening',
      description: 'The playable first impression, pressure, and promise.'
    },
    {
      id: 'cast',
      label: 'Characters',
      description: 'Who matters in the opening and what each person can carry.'
    },
    {
      id: 'scene_flow',
      label: 'Scenes',
      description: 'The sequence of encounters the player can meaningfully choose between.'
    },
    {
      id: 'mystery_boundary',
      label: 'World & rules',
      description: 'What can be discovered fairly while private answers stay protected.'
    },
    {
      id: 'arc_theme',
      label: 'Arcs & theme',
      description: 'What the story is testing in its people, not just what happens to them.'
    }
  ];

  function send() {
    const value = authorRequest(typed);
    if (busy || !value || typeof onrun !== 'function') return;
    // A free-form intention starts a fresh design kernel. Clicking an existing
    // kernel section makes the same composer a scoped revision instead. This
    // is the boundary between "create a character" and "change the opening."
    // A selected card element is edited directly. Only an explicit selection
    // from the separate plan sheet should route through a plan tile.
    const commentOn = selectedPlanId;
    const newMission = !commentOn;
    const message = docked
      ? `Edit ${entryLabel} directly.${entrySummary ? ` Current context: ${entrySummary}` : ''}\nAuthor request: ${value}`
      : value;
    selectedPlanId = '';
    selectedPlanTitle = '';
    onrun(message, { commentOn, newMission });
  }

  function authorRequest(value) {
    const clean = String(value || '').trim();
    // The dock supplies its own target/context envelope. Authors sometimes
    // paste a copied Architect prompt back into the composer; unwrap that
    // envelope so it cannot become ``Author request: … Author request: …``
    // and be mistaken for prose that should be added to the story card.
    const prefix = `Edit ${entryLabel} directly.`;
    const marker = '\nAuthor request:';
    if (!clean.startsWith(prefix)) return clean;
    const markerAt = clean.indexOf(marker);
    return markerAt >= 0 ? clean.slice(markerAt + marker.length).trim() : clean;
  }

  function composerKeydown(event) {
    if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
    event.preventDefault();
    send();
  }

  function text(value, limit = 1200) {
    if (typeof value === 'string') return value.trim().slice(0, limit);
    if (Array.isArray(value)) {
      return value.map((item) => text(item, limit)).filter(Boolean).join(' · ').slice(0, limit);
    }
    if (!value || typeof value !== 'object') return '';
    for (const key of ['summary', 'description', 'text', 'brief', 'direction', 'content', 'value', 'plan', 'label']) {
      const candidate = text(value[key], limit);
      if (candidate) return candidate;
    }
    return '';
  }

  function planSource(value) {
    if (!value || typeof value !== 'object') return {};
    if (Array.isArray(value.sections)) {
      return Object.fromEntries(value.sections
        .filter((item) => item && typeof item === 'object' && text(item.id, 80))
        .map((item) => [text(item.id, 80), item]));
    }
    return value.sections && typeof value.sections === 'object' ? value.sections : value;
  }

  function planIntroduction(value) {
    if (!value || typeof value !== 'object') return '';
    return text(value.introduction || value.overview || value.summary || value.intent || value.mission);
  }

  function valueForPlanSection(source, id) {
    const raw = source?.[id];
    if (!raw) return null;
    const body = text(raw);
    if (!body) return null;
    const status = typeof raw === 'object' ? text(raw.status || raw.state, 80) : '';
    const detail = typeof raw === 'object' ? text(raw.detail || raw.note || raw.focus || raw.comment_hint, 360) : '';
    return { body, status, detail };
  }

  function statusLabel(value) {
    return text(value, 80).replace(/_/g, ' ');
  }

  function fallbackPlanSections(value) {
    if (!value || typeof value !== 'object') return [];
    const entries = [];
    const scopes = Array.isArray(value.safe_scopes) ? value.safe_scopes : [];
    if (scopes.length) {
      entries.push({
        id: 'build_order',
        label: 'Public foundation',
        description: 'Build the already-established public material before expanding anything private.',
        body: 'The next bounded pass covers ' + scopes.map(planScopeLabel).join(', ') + '.',
        status: 'planned'
      });
    }
    if (value.public_population_task?.kind === 'public_supporting_cast') {
      const roles = Array.isArray(value.public_population_task.archetypes)
        ? value.public_population_task.archetypes.map((item) => text(item?.role, 100)).filter(Boolean)
        : [];
      entries.push({
        id: 'cast',
        label: 'People',
        description: 'Add only ordinary, location-rooted supporting people.',
        body: roles.length ? 'The Architect can add: ' + roles.join(' · ') + '.' : 'The Architect can add a small public supporting cast.',
        status: 'planned'
      });
    }
    const hingeLabels = {
      relationship_truth: ['cast', 'Relationships', 'Set the emotional truth that should govern how the central people behave.'],
      character_or_theme_truth: ['arc_theme', 'Storylines', 'Set the unresolved belief or wound the storyline will pressure.'],
      loop_or_reset_rule: ['opening', 'Loop & reset', 'Set the player-facing rule that makes the return structure coherent.'],
      time_or_entity_rule: ['mystery_boundary', 'Threat schedule', 'Set the public rhythm of danger without exposing the private answer.'],
      scene_placement: ['scene_flow', 'Possible scenes', 'Set how the opening moves through its playable encounters.'],
      private_story_logic: ['mystery_boundary', 'Mystery boundary', 'Set what the player can investigate before the hidden answer comes into view.'],
      blocked_scaffold: ['build_order', 'Plan revision', 'Choose the direction that resolves the blocked story connection.']
    };
    for (const hinge of Array.isArray(value.authorial_hinges) ? value.authorial_hinges : []) {
      const meta = hingeLabels[hinge?.reason] || ['build_order', 'Plan direction', 'Set the next authorial boundary for this story.'];
      if (entries.some((entry) => entry.id === meta[0])) continue;
      entries.push({ id: meta[0], label: meta[1], description: meta[2], body: '', status: 'needs your direction' });
    }
    if (Array.isArray(value.protected_tasks) && value.protected_tasks.length && !entries.some((entry) => entry.id === 'mystery_boundary')) {
      entries.push({
        id: 'mystery_boundary',
        label: 'Mystery boundary',
        description: 'The Architect will give the player fair public material while keeping private logic guarded.',
        body: 'A protected Director pass is staged after the public foundation is agreed.',
        status: 'protected'
      });
    }
    return entries;
  }

  function displayedPlanSections(author, operational) {
    const source = planSource(author);
    const explicit = planSectionMeta.map((meta) => {
      const value = valueForPlanSection(source, meta.id);
      return value ? { id: meta.id, label: meta.label, description: meta.description, ...value } : null;
    }).filter(Boolean);
    return explicit.length ? explicit : fallbackPlanSections(operational);
  }

  function buildOrder(author, operational) {
    const source = planSource(author);
    const raw = source.build_order || author?.build_order;
    if (Array.isArray(raw)) {
      return raw.map((item, index) => {
        const body = text(item, 280);
        return body ? { id: typeof item === 'object' ? text(item.id, 80) || 'build-' + index : 'build-' + index, body } : null;
      }).filter(Boolean);
    }
    const body = text(raw, 420);
    if (body) return [{ id: 'build_order', body }];
    const sections = Array.isArray(operational?.safe_scopes) ? operational.safe_scopes : [];
    return sections.length ? sections.map((scope) => ({ id: 'build_order', body: 'Build ' + planScopeLabel(scope) + ' from the approved direction.' })) : [];
  }

  function selectPlanSection(item) {
    if (!item?.id) return;
    const hasDraft = Boolean(typed.trim());
    selectedPlanId = item.id;
    selectedPlanTitle = item.label || 'this part of the plan';
    // A section click is an explicit change of scope, but it must not rewrite
    // an in-progress note. The visible scope pill makes that attachment clear.
    if (!hasDraft) typed = 'On ' + selectedPlanTitle + ': ';
    closePlanSheetAndFocusComposer();
  }

  function targetPlanSection(item) {
    if (!item?.id) return;
    selectedPlanId = item.id;
    selectedPlanTitle = item.label || 'this part of the plan';
  }

  function commentOnWholePlan() {
    // Build order is the public, plan-wide target validated by the
    // Architect. Sending it explicitly keeps a whole-plan note from being
    // silently routed through an old pending-question id.
    selectedPlanId = 'build_order';
    selectedPlanTitle = 'the overall plan';
    if (!typed.trim()) typed = 'On the overall plan: ';
    closePlanSheetAndFocusComposer();
  }

  function openPlanSheet() {
    planSheetOpen = true;
  }

  function closePlanSheet({ restoreFocus = true } = {}) {
    planSheetOpen = false;
    if (restoreFocus) requestAnimationFrame(() => planReviewButton?.focus());
  }

  function closePlanSheetAndFocusComposer() {
    closePlanSheet({ restoreFocus: false });
    requestAnimationFrame(() => composer?.focus({ preventScroll: true }));
  }

  function closeHistorySheet() {
    historySheetOpen = false;
    requestAnimationFrame(() => composer?.focus({ preventScroll: true }));
  }

  function handleWindowKeydown(event) {
    if (event.key !== 'Escape') return;
    if (historySheetOpen) closeHistorySheet();
    else if (planSheetOpen) closePlanSheet();
    else onclose?.();
  }

  function handlePlanSheetKeydown(event) {
    if (event.key === 'Escape') {
      event.preventDefault();
      closePlanSheet();
      return;
    }
    if (event.key !== 'Tab' || !planSheet) return;
    const focusable = [...planSheet.querySelectorAll(
      'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
    )].filter((element) => !element.hasAttribute('hidden') && element.getClientRects().length);
    if (!focusable.length) {
      event.preventDefault();
      planSheetClose?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function clearPlanScope() {
    selectedPlanId = '';
    selectedPlanTitle = '';
    requestAnimationFrame(() => composer?.focus({ preventScroll: true }));
  }

  let authorPlanSections = $derived(displayedPlanSections(authorPlan, plan));
  let authorPlanBuildOrder = $derived(buildOrder(authorPlan, plan));
  let authorPlanCopy = $derived(planIntroduction(authorPlan) || mission || 'Turn the current card into a complete, playable story.');
  let authorPlanFocus = $derived(
    authorPlan?.next_step && typeof authorPlan.next_step === 'object'
      ? {
          id: text(authorPlan.next_step.id, 80),
          label: text(authorPlan.next_step.label, 100),
          summary: text(authorPlan.next_step.summary, 280)
        }
      : null
  );
  const entryPlanIds = {
    world: 'mystery_boundary',
    premise: 'opening',
    cast: 'cast',
    arcs: 'arc_theme',
    first_day: 'scene_flow',
    time_system: 'mystery_boundary'
  };
  let activePlanSection = $derived(
    authorPlanSections.find((item) => item.id === selectedPlanId)
      || authorPlanSections.find((item) => item.id === entryPlanIds[entrySection])
      || authorPlanSections.find((item) => item.id === authorPlanFocus?.id)
      || authorPlanSections.find((item) => ['needs_direction', 'needs your direction', 'to_shape'].includes(item.status))
      || authorPlanSections[0]
      || null
  );
  let conversationMessages = $derived(messages.filter((message) => {
    const copy = String(message?.text || '').trim().toLowerCase();
    return !(message?.kind === 'progress' && copy === 'the architect is holding the current plan.');
  }));

  $effect(() => {
    const key = `${entrySection}:${entryLabel}`;
    if (key === lastEntryKey) return;
    lastEntryKey = key;
    selectedPlanId = '';
    selectedPlanTitle = '';
  });

  function actionSections(action) {
    const sections = Array.isArray(action?.updated_sections) && action.updated_sections.length
      ? action.updated_sections
      : (Array.isArray(action?.sections) ? action.sections : [action?.section]);
    const labels = {
      world: 'world', premise: 'opening', cast: 'people', arcs: 'theme & arcs',
      first_day: 'possible scenes', time_system: 'schedule'
    };
    return [...new Set(sections.filter(Boolean).map((section) => labels[section] || section))].join(', ');
  }

  function actionLabel(action) {
    const sections = actionSections(action);
    if (action?.kind === 'develop') return sections ? `Built ${sections}` : 'Built a connected card pass';
    if (action?.kind === 'interview') return sections ? `Protected ${sections}` : 'Protected an authoring decision';
    if (action?.kind === 'knowledge') return 'Assigned a director-only knowledge boundary';
    if (action?.kind === 'reconcile') return sections ? `Reconciled ${sections}` : 'Reconciled a Director change';
    return sections ? `Mapped ${sections}` : 'Mapped the current card';
  }

  function hasPass(kind) {
    return actions.some((action) => action?.kind === kind);
  }

  function planScopeLabel(scope) {
    const labels = { world: 'world setup', premise: 'opening', cast: 'public cast', first_day: 'possible scenes' };
    return labels[scope] || scope;
  }

  function planStatus(value) {
    if (!value || typeof value !== 'object') return '';
    const safe = Array.isArray(value.safe_scopes) ? value.safe_scopes : [];
    const hinges = Array.isArray(value.authorial_hinges) ? value.authorial_hinges : [];
    const protectedTasks = Array.isArray(value.protected_tasks) ? value.protected_tasks : [];
    const population = value.public_population_task;
    if (value.phase === 'protected_baseline' && protectedTasks.length) return 'Next protected pass: assign knowledge only to people already present in an existing private scene.';
    if (value.phase === 'safe_baseline' && population?.kind === 'public_supporting_cast') return 'Next planned pass: add ordinary supporting people at safe, already-authored locations.';
    if (value.phase === 'safe_baseline' && safe.length) return `Next planned pass: ${safe.map(planScopeLabel).join(', ')}.`;
    if (value.phase === 'author_decision' && hinges.length) return 'The next remaining step needs your authorial judgment.';
    if (value.phase === 'ready') return 'The structural baseline is ready to play.';
    return '';
  }

  function planTrackingCopy(value, count) {
    if (value?.phase === 'author_decision') return 'The plan is paused at one authorial choice.';
    if (value?.phase === 'safe_baseline') return 'The Architect has a bounded implementation pass ready when you approve the direction.';
    if (value?.phase === 'protected_baseline') return 'The next pass is protected; private story logic stays out of the general build.';
    if (value?.phase === 'ready') return 'The structural plan is ready for play.';
    return count ? 'The Architect is keeping the remaining work inside its plan.' : 'No unassigned planning work is currently visible.';
  }

  function reviewNextDecision() {
    if (busy) return;
    if (executionReady) {
      onexecute?.();
      return;
    }
    // A visible plan is already the place for feedback. Keep the button a
    // navigation/focus aid instead of accidentally submitting a completion
    // request that could start a worker pass.
    if (question || authorPlanSections.length) {
      openPlanSheet();
      return;
    }
    onreview?.();
  }

  function fallbackTarget(value) {
    const target = value?.route?.fallback_target;
    return String(target?.model || value?.route?.fallback || 'configured fallback').trim();
  }

  $effect(() => {
    if (busy || planSheetOpen) return;
    requestAnimationFrame(() => composer?.focus({ preventScroll: true }));
  });

  $effect(() => {
    if (!planSheetOpen) return;
    requestAnimationFrame(() => planSheetClose?.focus());
  });

  $effect(() => {
    if (!planSheetOpen || typeof document === 'undefined') return;
    const { body, documentElement } = document;
    const previousBodyOverflow = body.style.overflow;
    const previousHtmlOverflow = documentElement.style.overflow;
    body.style.overflow = 'hidden';
    documentElement.style.overflow = 'hidden';
    return () => {
      body.style.overflow = previousBodyOverflow;
      documentElement.style.overflow = previousHtmlOverflow;
    };
  });

  $effect(() => {
    const count = messages.length;
    if (!thread || !count) return;
    requestAnimationFrame(() => thread?.scrollTo({ top: thread.scrollHeight, behavior: 'smooth' }));
  });
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<section class="architect-surface" class:docked role={docked ? 'region' : 'dialog'} aria-modal={docked ? undefined : 'true'} aria-label="Story architect">
  {#if docked}
    <button class="history-architect embedded-history" type="button" onclick={() => (historySheetOpen = true)} aria-label={`Show conversation history for ${entryLabel}`}>History{history.length ? ` (${history.length})` : ''}</button>
    <button class="close-architect embedded-close" type="button" onclick={() => onclose?.()} aria-label="Close editing conversation">Close</button>
  {:else}
    <header class="architect-head">
      <div>
        <span class="eyebrow">Primary story co-author</span>
        <h2>Story architect</h2>
        <p><b>{entryLabel}</b> · Choose a starting direction or speak freely. We will confirm the plan before anything changes.</p>
      </div>
      <div class="architect-head-actions">
        <div class:working={busy} class="architect-state" aria-live="polite">
          <span class="state-dot"></span>
          <div>
            <strong>{busy ? (activity || 'Working through the story…') : 'Waiting for your direction'}</strong>
            <small>{route || 'Configured Story Agent'}</small>
          </div>
        </div>
        <button class="close-architect" type="button" onclick={() => onclose?.()} aria-label="Close Story Architect">Close</button>
      </div>
    </header>
  {/if}

  {#if historySheetOpen}
    <div class="plan-sheet-backdrop" role="presentation" onclick={(event) => { if (event.target === event.currentTarget) closeHistorySheet(); }}>
      <section class="plan-sheet history-sheet" role="dialog" aria-modal="true" aria-labelledby="architect-history-title" tabindex="-1">
        <header class="plan-sheet-head">
          <div>
            <span>Conversation history</span>
            <h3 id="architect-history-title">{entryLabel}</h3>
          </div>
          <button class="close-plan-sheet" type="button" onclick={closeHistorySheet}>Close</button>
        </header>
        <div class="history-sheet-scroll">
          {#if history.length}
            {#each history as message, index (`${message.role || 'assistant'}:${index}:${message.text}`)}
              <article class:user={message.role === 'user'} class="history-entry">
                <span>{message.role === 'user' ? 'You' : 'Story Architect'}</span>
                <p>{message.text}</p>
                {#if message.meta}<small>{message.meta}</small>{/if}
              </article>
            {/each}
          {:else}
            <p class="history-empty">This component has no saved discussion yet.</p>
          {/if}
        </div>
      </section>
    </div>
  {/if}

  {#if planSheetOpen}
    <div
      class="plan-sheet-backdrop"
      role="presentation"
      onclick={(event) => { if (event.target === event.currentTarget) closePlanSheet(); }}
    >
      <section
        bind:this={planSheet}
        class="plan-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="architect-plan-title"
        tabindex="-1"
        onkeydown={handlePlanSheetKeydown}
      >
        <header class="plan-sheet-head">
          <div>
            <span>Architect's working plan</span>
            <h3 id="architect-plan-title">Review the story shape</h3>
          </div>
          <button bind:this={planSheetClose} class="close-plan-sheet" type="button" onclick={closePlanSheet} aria-label="Close plan review">Close</button>
        </header>

        <div class="plan-sheet-scroll">
          <section class="author-plan" aria-label="Architect's story plan">
            <div class="author-plan-lead">
              <span>Plan introduction</span>
              <h4>Here is the shape I am building with you.</h4>
              <p>{authorPlanCopy}</p>
              <button class="whole-plan-comment" type="button" disabled={busy} onclick={commentOnWholePlan}>
                Comment on the whole plan
              </button>
            </div>

            <div class="author-plan-sections">
              {#each authorPlanSections as item (item.id)}
                <button
                  type="button"
                  class="author-plan-section"
                  class:selected={selectedPlanId === item.id}
                  class:protected={item.status === 'protected'}
                  class:attention={item.status === 'needs_direction' || item.status === 'needs your direction' || item.status === 'to_shape'}
                  disabled={busy}
                  onclick={() => selectPlanSection(item)}
                >
                  <span class="author-plan-section-head">
                    <span>{item.label}</span>
                    {#if item.status}<small>{statusLabel(item.status)}</small>{/if}
                  </span>
                  <strong>{item.body || item.description}</strong>
                  {#if item.body && item.description}
                    <span class="author-plan-section-detail">{item.description}</span>
                  {/if}
                  <span class="author-plan-section-action">Comment</span>
                </button>
              {:else}
                <div class="author-plan-empty">
                  <strong>Reading the card into a usable plan…</strong>
                  <span>The Architect will introduce the opening, people, scenes, mystery boundary, and arcs here.</span>
                </div>
              {/each}
            </div>

            {#if authorPlanBuildOrder.length}
              <div class="build-order">
                <span>Build order</span>
                <ol>
                  {#each authorPlanBuildOrder as item, index (item.id + '-' + index)}
                    <li>
                      <i aria-hidden="true">{index + 1}</i>
                      <button type="button" disabled={busy} onclick={() => selectPlanSection({ id: item.id || 'build_order', label: 'Build order' })}>{item.body}</button>
                    </li>
                  {/each}
                </ol>
              </div>
            {/if}

            <div class="plan-feedback">
              <div>
                <span>Author feedback</span>
                <p>{authorPlanFocus?.label
                  ? `Suggested focus: ${authorPlanFocus.label}.${authorPlanFocus.summary ? ` ${authorPlanFocus.summary}` : ''} You can comment there, or steer any other section instead.`
                  : 'Nothing needs to be answered in order. Comment on what to preserve, change, make sharper, or leave unresolved.'}</p>
              </div>
              {#if authorPlanFocus?.id}
                <button type="button" disabled={busy} onclick={() => selectPlanSection(authorPlanFocus)}>
                  Comment on {authorPlanFocus.label || 'this focus'}
                </button>
              {/if}
            </div>
          </section>
        </div>
      </section>
    </div>
  {/if}

  <div class="architect-layout">
    <section class="thread-panel" aria-label="Conversation with the Story Architect">
      <section class="inline-plan" aria-label="Current Architect plan draft">
        <header class="inline-plan-head">
          <div>
            <span>{executionReady ? 'Ready for confirmation' : docked ? 'Local change' : 'Emerging plan'}</span>
            <h3>{executionReady ? 'Confirm this plan, then execute it' : docked ? entryLabel : 'Keep talking until the plan feels right'}</h3>
            <p>{docked ? (entrySummary || 'Discuss the selected story element directly.') : authorPlanCopy}</p>
          </div>
          <button bind:this={planReviewButton} type="button" onclick={openPlanSheet}>Inspect full plan</button>
        </header>

        {#if reviewedPatch}
          <article class="reviewed-patch" aria-label="Reviewed Story patch">
            <span>Reviewed patch — no canon change yet</span>
            <pre>{reviewedPatch}</pre>
          </article>
        {/if}

        {#if authorPlanSections.length}
          <nav class="plan-targets" aria-label="Choose a plan target">
            {#each authorPlanSections as item (item.id)}
              <button
                type="button"
                class:active={activePlanSection?.id === item.id}
                class:attention={item.status === 'needs_direction' || item.status === 'needs your direction' || item.status === 'to_shape'}
                disabled={busy}
                onclick={() => targetPlanSection(item)}
              >
                <span>{item.label}</span>
                {#if item.status}<small>{statusLabel(item.status)}</small>{/if}
              </button>
            {/each}
          </nav>

          {#if activePlanSection}
            <article class="plan-focus">
              <div>
                <span>{docked ? 'Current plan note' : `Targeting ${activePlanSection.label}`}</span>
                <p>{activePlanSection.body || activePlanSection.description}</p>
              </div>
              <button type="button" disabled={busy} onclick={() => selectPlanSection(activePlanSection)}>Discuss this target</button>
            </article>
          {/if}
        {:else}
          <div class="inline-plan-empty">
            <strong>Listening for the first planning direction…</strong>
            <span>The focused plan will appear here as the conversation develops.</span>
          </div>
        {/if}

        {#if authorPlanBuildOrder.length}
          <div class="inline-build-order">
            <span>Build order</span>
            <ol>
              {#each authorPlanBuildOrder as item, index (item.id + '-' + index)}
                <li><button type="button" disabled={busy} onclick={() => selectPlanSection({ id: item.id || 'build_order', label: 'Build order' })}><i>{index + 1}</i>{item.body}</button></li>
              {/each}
            </ol>
          </div>
        {/if}

        <footer class="inline-plan-actions">
          <button type="button" disabled={busy} onclick={commentOnWholePlan}>Comment on the whole plan</button>
          {#if executionReady}
            {#if onreject}<button class="discard-plan" type="button" disabled={busy} onclick={() => onreject?.()}>Discard proposal</button>{/if}
            <button class="apply-plan" type="button" disabled={busy} onclick={() => onexecute?.()}>Confirm plan & execute</button>
          {:else}
            <span>Nothing is applied while the plan is still being discussed.</span>
          {/if}
        </footer>
      </section>

      <div class="thread" bind:this={thread} aria-live="polite">
        {#each conversationMessages as message, index ((message.role || 'assistant') + '-' + (message.kind || 'message') + '-' + index + '-' + (message.text || ''))}
          <div class:author={message.role === 'user'} class:assistant={message.role !== 'user'} class:progress={message.kind === 'progress'} class:question={message.kind === 'question'}>
            {#if message.loading}
              <span class="thinking">{activity || 'Reading the live story card…'}</span>
            {:else}
              {#if message.kind === 'question'}<span class="question-label">Plan note</span>{/if}
              {message.text}
              {#if message.meta}
                <small class="message-meta">{message.meta}</small>
              {/if}
            {/if}
          </div>
        {:else}
          <p class="empty-thread">{docked ? `Describe how ${entryLabel} should change.` : 'Describe the change you want, or choose a plan target above to focus the conversation.'}</p>
        {/each}
        {#if error}
          <div class="error" role="status">
            <p>{error}</p>
            {#if fallbackRetry}
              <button class="fallback-retry" type="button" disabled={busy} onclick={() => onfallback?.()}>
                Try configured fallback
              </button>
              <small>Retries this exact direction with {fallbackTarget(fallbackRetry)}. It does not resend automatically.</small>
            {/if}
          </div>
        {/if}
      </div>

      <form class="composer" onsubmit={(event) => { event.preventDefault(); send(); }}>
        <label for="architect-reply">{selectedPlanTitle ? 'Discuss ' + selectedPlanTitle : docked ? 'What should change?' : 'What should we design?'}</label>
        {#if selectedPlanId}
          <div class="selected-plan-scope" aria-live="polite">
            <span>Feedback scope</span>
            <strong>{selectedPlanTitle}</strong>
            <button type="button" onclick={clearPlanScope} aria-label={'Remove feedback scope: ' + selectedPlanTitle}>Remove</button>
          </div>
        {/if}
        <textarea
          id="architect-reply"
          bind:this={composer}
          bind:value={typed}
          oninput={() => ondraft?.()}
          onkeydown={composerKeydown}
          disabled={busy}
          placeholder={docked ? `Describe what should change about ${entryLabel}…` : 'For example: Create a new character who is warm in public, privately competitive, and tied to the harbor…'}
        ></textarea>
        <div class="composer-actions">
          <small>{selectedPlanTitle ? 'This turn is focused on ' + selectedPlanTitle + '.' : 'We will refine the idea together before anything is applied.'}</small>
          <button type="submit" disabled={busy || !typed.trim()}>{busy ? 'Working…' : docked ? 'Continue' : 'Continue with Architect'}</button>
        </div>
      </form>
    </section>

    <aside class="agent-rail" aria-label="Architect working context">
      <section class="coverage">
        <span>Live story card</span>
        <strong>{summary || 'Mapping the established story…'}</strong>
        <div class="coverage-counts">
          <span>{gaps.length} tracked connection{gaps.length === 1 ? '' : 's'}</span>
          {#if gaps.filter((gap) => gap.severity === 'blocker').length}
            <span class="required">{gaps.filter((gap) => gap.severity === 'blocker').length} required</span>
          {/if}
        </div>
      </section>

      <section class="plan" aria-label="Architect plan progress">
        <div class="plan-head">
          <span>Plan progress</span>
          <small>{actions.length} pass{actions.length === 1 ? '' : 'es'} tracked</small>
        </div>
        {#if planStatus(plan)}
          <p class="plan-status">{planStatus(plan)}</p>
        {/if}
        <ol class="plan-steps">
          <li class:done={hasPass('assess') || actions.length > 0}>
            <i aria-hidden="true">1</i>
            <div><b>Map the live card</b><small>Introduce a whole story shape before implementation begins.</small></div>
          </li>
          <li class:active={busy} class:done={hasPass('develop') || hasPass('interview') || hasPass('knowledge') || hasPass('reconcile')}>
            <i aria-hidden="true">2</i>
            <div><b>Build the approved shape</b><small>{busy ? (activity || 'Checking the consequences…') : 'Workers handle bounded implementation after your direction is clear.'}</small></div>
          </li>
          <li class:active={!busy && (!!question || selectedPlanId)}>
            <i aria-hidden="true">3</i>
            <div><b>Incorporate your feedback</b><small>{selectedPlanId ? 'Your next note is attached to ' + selectedPlanTitle + '.' : 'You can steer any plan section without responding in a fixed order.'}</small></div>
          </li>
          <li>
            <i aria-hidden="true">4</i>
            <div><b>Check play readiness</b><small>Keep iterating until the opening can run.</small></div>
          </li>
        </ol>
        {#if actions.length}
          <div class="recent-actions" aria-label="Recent Architect passes">
            <span>Recent activity</span>
            {#each actions.slice(-3).reverse() as action, index ((action?.kind || 'assess') + '-' + (action?.question_id || index))}
              <small>{actionLabel(action)}</small>
            {/each}
          </div>
        {/if}
        <button class="complete-story" type="button" disabled={busy} onclick={reviewNextDecision}>
          <strong>{executionReady ? 'Apply the approved plan' : 'Review the plan'}</strong>
          <span>{executionReady
            ? 'Run the one protected pass prepared from your established direction.'
            : 'Bring the plan back into view without changing the card.'}</span>
        </button>
      </section>

      <section class="tracking" aria-label="Architect plan coverage">
        <span>Plan coverage</span>
        <p>{planTrackingCopy(plan, gaps.length)}</p>
        <div class="tracking-counts">
          <span>{gaps.length} tracked item{gaps.length === 1 ? '' : 's'}</span>
          {#if gaps.filter((gap) => gap.severity === 'blocker').length}
            <span class="required">{gaps.filter((gap) => gap.severity === 'blocker').length} author decision{gaps.filter((gap) => gap.severity === 'blocker').length === 1 ? '' : 's'}</span>
          {/if}
          {#if Array.isArray(plan?.safe_scopes) && plan.safe_scopes.length}
            <span>{plan.safe_scopes.length} planned build area{plan.safe_scopes.length === 1 ? '' : 's'}</span>
          {/if}
        </div>
        <small class="tracking-note">Detailed diagnostics stay with the Architect. The visible plan stays at the story level.</small>
      </section>
    </aside>
  </div>
</section>

<style>
  .architect-surface { box-sizing: border-box; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 16px; height: 100%; min-height: 0; padding: clamp(16px, 2.5vw, 28px); border: 1px solid color-mix(in srgb, var(--accent) 32%, var(--border)); border-radius: 18px; background: linear-gradient(140deg, color-mix(in srgb, var(--accent) 7%, var(--panel)), var(--panel) 52%); box-shadow: 0 18px 50px rgba(0,0,0,.16); }
  .architect-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; padding-bottom: 15px; border-bottom: 1px solid var(--border-soft); } .eyebrow { color: var(--accent); font-size: 10px; font-weight: 850; letter-spacing: .1em; text-transform: uppercase; } h2, p { margin: 0; } h2 { margin-top: 4px; color: var(--text); font-size: clamp(24px, 3.4vw, 34px); letter-spacing: -.04em; } .architect-head p { max-width: 600px; margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.45; }
  .architect-head-actions { display: flex; align-items: flex-start; justify-content: flex-end; gap: 8px; flex: none; } .review-plan-button { display: inline-flex; align-items: baseline; gap: 6px; border: 1px solid color-mix(in srgb, var(--accent) 56%, var(--border)); border-radius: 8px; padding: 8px 9px; background: color-mix(in srgb, var(--accent) 10%, var(--elev)); color: var(--text); font: inherit; font-size: 10px; font-weight: 850; white-space: nowrap; cursor: pointer; } .review-plan-button small { color: var(--accent); font-size: 8px; font-weight: 850; } .review-plan-button:hover, .review-plan-button:focus-visible { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, var(--elev)); outline: none; } .review-plan-button:disabled { opacity: .55; cursor: default; }
  .architect-state { display: flex; align-items: flex-start; gap: 8px; flex: none; max-width: 235px; padding: 8px 10px; border: 1px solid var(--border-soft); border-radius: 10px; background: color-mix(in srgb, var(--panel) 70%, var(--elev)); } .state-dot { width: 7px; height: 7px; flex: none; margin-top: 4px; border-radius: 999px; background: var(--good, #6ec77f); box-shadow: 0 0 0 3px color-mix(in srgb, var(--good, #6ec77f) 15%, transparent); } .architect-state.working .state-dot { background: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent); animation: pulse 1.25s ease-in-out infinite; } .architect-state strong, .architect-state small { display: block; } .architect-state strong { color: var(--text); font-size: 10px; line-height: 1.25; } .architect-state small { margin-top: 3px; color: var(--faint); font-size: 9px; line-height: 1.25; }
  .architect-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(245px, 310px); gap: 16px; min-height: 0; } .thread-panel { display: grid; grid-template-rows: minmax(0, 1fr) minmax(72px, .28fr) auto; gap: 12px; min-height: 0; } .thread { display: flex; flex-direction: column; align-items: flex-start; gap: 9px; min-height: 72px; overflow-y: auto; padding: 2px 4px 2px 0; scrollbar-width: none; } .thread::-webkit-scrollbar { display: none; } .thread > div { box-sizing: border-box; width: min(100%, 670px); padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--border-soft) 86%, transparent); border-radius: 12px; background: var(--elev); color: var(--text); font-size: 13px; line-height: 1.48; white-space: pre-wrap; } .thread > .author { align-self: flex-end; border-color: color-mix(in srgb, var(--accent) 80%, var(--border)); background: var(--accent); color: #0b0e14; } .thread > .progress { width: min(100%, 500px); padding: 8px 10px; border-color: color-mix(in srgb, var(--good, #6ec77f) 34%, var(--border)); border-left: 2px solid var(--good, #6ec77f); border-radius: 0 9px 9px 0; background: color-mix(in srgb, var(--good, #6ec77f) 6%, var(--elev)); color: var(--muted); font-size: 12px; line-height: 1.38; } .thread > .question { border-color: color-mix(in srgb, var(--accent) 62%, var(--border)); background: color-mix(in srgb, var(--accent) 9%, var(--elev)); } .progress-label, .question-label { display: block; margin-bottom: 3px; font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .progress-label { color: var(--good, #6ec77f); } .question-label { color: var(--accent); } .thinking, .empty-thread { color: var(--muted); font-size: 12px; } .message-meta { display: block; margin-top: 6px; color: var(--faint); font-size: 10px; line-height: 1.35; } .author .message-meta { color: color-mix(in srgb, #0b0e14 62%, transparent); } .progress .message-meta { margin-top: 3px; } .error { display: grid; gap: 6px; width: min(100%, 670px); padding: 9px 10px; border: 1px solid color-mix(in srgb, var(--bad, #d0655a) 44%, var(--border)); border-radius: 10px; background: color-mix(in srgb, var(--bad, #d0655a) 8%, var(--elev)); color: var(--bad, #d0655a); font-size: 12px; line-height: 1.35; } .error p { margin: 0; } .error small { color: var(--muted); font-size: 10px; line-height: 1.3; } .fallback-retry { justify-self: start; border: 1px solid color-mix(in srgb, var(--accent) 68%, var(--border)); border-radius: 7px; padding: 6px 8px; background: color-mix(in srgb, var(--accent) 14%, var(--elev)); color: var(--text); font: inherit; font-size: 10px; font-weight: 850; cursor: pointer; } .fallback-retry:hover { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 20%, var(--elev)); } .fallback-retry:disabled { opacity: .5; cursor: default; }
  .thread-panel { grid-template-rows: minmax(0, 1fr) auto auto; }
  .thread { min-height: 0; max-height: 140px; }
  .inline-plan { display: grid; align-content: start; gap: 11px; min-height: 0; overflow-y: auto; padding: 14px; border: 1px solid color-mix(in srgb, var(--accent) 48%, var(--border)); border-radius: 15px; background: linear-gradient(125deg, color-mix(in srgb, var(--accent) 11%, var(--elev)), color-mix(in srgb, var(--panel) 72%, var(--elev))); scrollbar-width: thin; }
  .inline-plan-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; } .inline-plan-head > div { display: grid; gap: 4px; min-width: 0; } .inline-plan-head span, .inline-build-order > span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; } .inline-plan-head h3 { margin: 0; color: var(--text); font-size: 18px; letter-spacing: -.025em; } .inline-plan-head p { max-width: 620px; color: var(--muted); font-size: 11px; line-height: 1.42; } .inline-plan-head > button, .inline-plan-actions button { flex: none; border: 1px solid color-mix(in srgb, var(--accent) 58%, var(--border)); border-radius: 8px; padding: 7px 9px; background: color-mix(in srgb, var(--accent) 10%, var(--elev)); color: var(--text); font: inherit; font-size: 9px; font-weight: 850; cursor: pointer; }
  .inline-plan-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; } .inline-plan-sections > button, .inline-plan-empty { display: grid; align-content: start; gap: 5px; min-width: 0; padding: 10px; border: 1px solid var(--border-soft); border-radius: 10px; background: color-mix(in srgb, var(--panel) 72%, var(--elev)); text-align: left; } .inline-plan-sections > button { color: var(--text); font: inherit; cursor: pointer; } .inline-plan-sections > button:hover, .inline-plan-sections > button.selected { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--elev)); } .inline-plan-sections > button.protected { border-color: color-mix(in srgb, #a989ff 52%, var(--border)); } .inline-plan-sections > button.attention { border-color: color-mix(in srgb, var(--accent) 68%, var(--border)); } .inline-plan-sections > button > span { display: flex; justify-content: space-between; gap: 8px; } .inline-plan-sections b { color: var(--faint); font-size: 9px; letter-spacing: .06em; text-transform: uppercase; } .inline-plan-sections small { color: var(--accent); font-size: 8px; text-transform: capitalize; } .inline-plan-sections strong { color: var(--text); font-size: 11px; line-height: 1.36; } .inline-plan-sections p { color: var(--faint); font-size: 9px; line-height: 1.3; } .inline-plan-sections i { color: var(--accent); font-size: 9px; font-style: normal; font-weight: 800; } .inline-plan-empty { grid-column: 1 / -1; color: var(--muted); font-size: 11px; }
  .reviewed-patch { display: grid; gap: 6px; padding: 9px; border: 1px solid color-mix(in srgb, var(--accent) 58%, var(--border)); border-radius: 9px; background: color-mix(in srgb, var(--accent) 7%, var(--elev)); } .reviewed-patch > span { color: var(--accent); font-size: 8px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .reviewed-patch pre { max-height: 180px; margin: 0; overflow: auto; color: var(--text); font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 9px; line-height: 1.4; white-space: pre-wrap; overflow-wrap: anywhere; scrollbar-width: thin; }
  .inline-build-order { display: grid; gap: 6px; } .inline-build-order ol { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; list-style: none; } .inline-build-order button { display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border-soft); border-radius: 7px; padding: 5px 7px; background: transparent; color: var(--muted); font: inherit; font-size: 9px; cursor: pointer; } .inline-build-order i { display: grid; place-items: center; width: 14px; height: 14px; background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); font-size: 8px; font-style: normal; font-weight: 850; } .inline-plan-actions { display: flex; justify-content: space-between; gap: 8px; padding-top: 2px; } .inline-plan-actions .apply-plan { border-color: var(--accent); background: var(--accent); color: #0b0e14; } .inline-plan-actions .discard-plan { border-color: var(--border-soft); background: transparent; color: var(--muted); }
  .composer { display: grid; gap: 7px; padding: 10px; border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border)); border-radius: 13px; background: color-mix(in srgb, var(--panel) 68%, var(--elev)); } .composer label { color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .selected-plan-scope { display: flex; align-items: center; gap: 6px; min-width: 0; padding: 5px 6px; border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--border)); border-radius: 7px; background: color-mix(in srgb, var(--accent) 8%, var(--elev)); } .selected-plan-scope > span { flex: none; color: var(--accent); font-size: 8px; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; } .selected-plan-scope > strong { overflow: hidden; min-width: 0; color: var(--text); font-size: 10px; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; } .selected-plan-scope > button { flex: none; margin-left: auto; border: 0; padding: 1px 0; background: transparent; color: var(--faint); font: inherit; font-size: 9px; font-weight: 800; cursor: pointer; } .selected-plan-scope > button:hover, .selected-plan-scope > button:focus-visible { color: var(--text); outline: none; } textarea { box-sizing: border-box; width: 100%; min-height: 76px; resize: vertical; border: 0; outline: 0; padding: 2px; background: transparent; color: var(--text); font: inherit; font-size: 13px; line-height: 1.45; } textarea::placeholder { color: var(--faint); } .composer-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; } .composer-actions small { max-width: 470px; color: var(--faint); font-size: 10px; line-height: 1.3; } .composer-actions button { flex: none; border: 1px solid var(--accent); border-radius: 8px; padding: 7px 10px; background: var(--accent); color: #0b0e14; font: inherit; font-size: 10px; font-weight: 850; cursor: pointer; } .composer-actions button:disabled { opacity: .46; cursor: default; }
  .agent-rail { display: grid; align-content: start; gap: 9px; min-height: 0; overflow-y: auto; padding-right: 2px; scrollbar-width: none; } .agent-rail::-webkit-scrollbar { display: none; } .control-graph, .next-question, .coverage, .plan, .tracking { padding: 11px; border: 1px solid var(--border-soft); border-radius: 11px; background: color-mix(in srgb, var(--panel) 72%, var(--elev)); } .control-graph { display: grid; gap: 7px; border-color: color-mix(in srgb, var(--accent) 34%, var(--border)); } .control-graph > span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .control-graph > p { color: var(--muted); font-size: 10px; line-height: 1.34; } .control-graph-nodes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; } .control-graph-nodes button { display: grid; gap: 2px; min-width: 0; border: 1px solid var(--border-soft); border-radius: 6px; padding: 6px; background: transparent; color: var(--text); text-align: left; font: inherit; cursor: pointer; } .control-graph-nodes button:hover { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 9%, transparent); } .control-graph-nodes button.protected { border-color: color-mix(in srgb, #a58cff 38%, var(--border)); } .control-graph-nodes button.attention { border-color: color-mix(in srgb, var(--accent) 58%, var(--border)); } .control-graph-nodes button:disabled { opacity: .55; cursor: default; } .control-graph-nodes b { overflow: hidden; font-size: 9px; text-overflow: ellipsis; white-space: nowrap; } .control-graph-nodes small, .graph-empty { color: var(--faint); font-size: 8px; text-transform: capitalize; } .next-question { border-color: color-mix(in srgb, var(--accent) 48%, var(--border)); background: color-mix(in srgb, var(--accent) 8%, var(--elev)); } .next-question > span, .coverage > span, .tracking > span { display: block; color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .next-question p { margin-top: 6px; color: var(--text); font-size: 13px; font-weight: 650; line-height: 1.42; } .next-question small { display: block; margin-top: 8px; color: var(--muted); font-size: 10px; line-height: 1.35; } .coverage { display: grid; gap: 5px; } .coverage strong { color: var(--muted); font-size: 11px; font-weight: 600; line-height: 1.36; } .coverage-counts { display: flex; flex-wrap: wrap; gap: 5px; } .coverage-counts span { padding: 3px 5px; border: 1px solid var(--border-soft); border-radius: 5px; color: var(--faint); font-size: 9px; font-weight: 750; } .coverage-counts .required { border-color: color-mix(in srgb, var(--bad, #d0655a) 45%, var(--border)); color: var(--bad, #d0655a); }
  .plan { display: grid; gap: 8px; border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); } .plan-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; } .plan-head > span, .recent-actions > span { color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; } .plan-head > small { color: var(--faint); font-size: 9px; } .plan-mission { color: var(--text); font-size: 11px; font-weight: 650; line-height: 1.4; } .plan-status { padding: 6px 7px; border-left: 2px solid var(--accent); background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--muted); font-size: 10px; font-weight: 650; line-height: 1.35; } .plan-steps { display: grid; gap: 7px; margin: 1px 0 0; padding: 0; list-style: none; } .plan-steps li { display: grid; grid-template-columns: 16px minmax(0, 1fr); gap: 7px; align-items: start; color: var(--faint); } .plan-steps i { display: grid; place-items: center; width: 15px; height: 15px; border: 1px solid var(--border-soft); color: var(--faint); font-size: 8px; font-style: normal; font-weight: 850; line-height: 1; } .plan-steps b, .plan-steps small { display: block; } .plan-steps b { color: var(--muted); font-size: 10px; line-height: 1.2; } .plan-steps small { margin-top: 2px; color: var(--faint); font-size: 9px; line-height: 1.28; } .plan-steps li.done i { border-color: color-mix(in srgb, var(--good, #6ec77f) 58%, var(--border)); background: color-mix(in srgb, var(--good, #6ec77f) 16%, transparent); color: var(--good, #6ec77f); } .plan-steps li.done b { color: var(--text); } .plan-steps li.active i { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent); } .plan-steps li.active b { color: var(--text); } .recent-actions { display: grid; gap: 3px; padding-top: 8px; border-top: 1px solid var(--border-soft); } .recent-actions small { overflow: hidden; color: var(--muted); font-size: 9px; line-height: 1.3; text-overflow: ellipsis; white-space: nowrap; } .recent-actions small::before { content: '•'; margin-right: 5px; color: var(--accent); } .complete-story { display: grid; gap: 2px; width: 100%; margin-top: 2px; padding: 8px 9px; border: 1px solid color-mix(in srgb, var(--accent) 65%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--accent) 13%, var(--elev)); color: var(--text); text-align: left; font: inherit; cursor: pointer; } .complete-story:hover { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 19%, var(--elev)); } .complete-story:disabled { opacity: .5; cursor: default; } .complete-story strong { font-size: 10px; } .complete-story span { color: var(--muted); font-size: 9px; line-height: 1.25; }
  .tracking { display: grid; gap: 7px; } .tracking p { color: var(--muted); font-size: 11px; line-height: 1.4; } .tracking-counts { display: flex; flex-wrap: wrap; gap: 5px; } .tracking-counts span { padding: 3px 5px; border: 1px solid var(--border-soft); border-radius: 5px; color: var(--faint); font-size: 9px; font-weight: 750; } .tracking-counts .required { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); color: var(--accent); } .tracking-note { color: var(--faint); font-size: 9px; line-height: 1.32; }

  .plan-sheet-backdrop { position: fixed; z-index: 70; inset: 0; display: grid; justify-items: end; overscroll-behavior: none; background: color-mix(in srgb, #06080d 62%, transparent); backdrop-filter: blur(3px); }
  .plan-sheet { box-sizing: border-box; display: grid; grid-template-rows: auto minmax(0, 1fr); width: min(780px, calc(100vw - 28px)); height: 100dvh; max-height: 100dvh; border-left: 1px solid color-mix(in srgb, var(--accent) 48%, var(--border)); background: var(--panel); box-shadow: -22px 0 70px rgba(0,0,0,.42); }
  .plan-sheet-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 16px 18px; border-bottom: 1px solid var(--border-soft); background: linear-gradient(120deg, color-mix(in srgb, var(--accent) 10%, var(--panel)), var(--panel)); }
  .plan-sheet-head > div { display: grid; gap: 3px; } .plan-sheet-head span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; } .plan-sheet-head h3 { margin: 0; color: var(--text); font-size: 18px; letter-spacing: -.025em; line-height: 1.15; }
  .close-plan-sheet { flex: none; border: 1px solid var(--border-soft); border-radius: 7px; padding: 6px 8px; background: color-mix(in srgb, var(--panel) 72%, var(--elev)); color: var(--muted); font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; } .close-plan-sheet:hover, .close-plan-sheet:focus-visible { border-color: var(--accent); color: var(--text); outline: none; }
  .history-sheet { width: min(440px, 100vw); }
  .history-sheet-scroll { display: grid; align-content: start; gap: 10px; overflow: auto; max-height: calc(100vh - 82px); padding: 15px; }
  .history-entry { display: grid; gap: 4px; padding: 10px 11px; border: 1px solid var(--border-soft); border-radius: 3px; background: color-mix(in srgb, var(--elev) 76%, var(--panel)); }
  .history-entry.user { border-color: color-mix(in srgb, var(--accent) 42%, var(--border-soft)); background: color-mix(in srgb, var(--accent) 8%, var(--elev)); }
  .history-entry > span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; }
  .history-entry p { margin: 0; color: var(--text); font-size: 12px; line-height: 1.45; white-space: pre-wrap; }
  .history-entry small { color: var(--muted); font-size: 9px; line-height: 1.3; }
  .history-empty { margin: 0; color: var(--muted); font-size: 12px; line-height: 1.45; }
  .plan-sheet-scroll { min-height: 0; overflow-y: auto; overscroll-behavior: contain; padding: clamp(14px, 2.4vw, 24px); scrollbar-width: thin; scrollbar-color: color-mix(in srgb, var(--accent) 45%, transparent) transparent; }
  .author-plan { display: grid; grid-template-columns: minmax(190px, .72fr) minmax(0, 2fr); gap: 12px; align-items: stretch; padding: 13px; border: 1px solid color-mix(in srgb, var(--accent) 44%, var(--border)); border-radius: 14px; background: linear-gradient(118deg, color-mix(in srgb, var(--accent) 12%, var(--elev)), color-mix(in srgb, var(--panel) 74%, var(--elev))); }
  .author-plan-lead { display: flex; flex-direction: column; align-items: flex-start; gap: 7px; padding: 3px 4px 3px 1px; }
  .author-plan-lead > span, .plan-feedback > div > span, .build-order > span { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .09em; text-transform: uppercase; }
  .author-plan-lead h4 { margin: 0; color: var(--text); font-size: 16px; letter-spacing: -.025em; line-height: 1.12; }
  .author-plan-lead p { color: var(--muted); font-size: 11px; line-height: 1.43; }
  .whole-plan-comment { margin-top: auto; border: 1px solid color-mix(in srgb, var(--accent) 65%, var(--border)); border-radius: 7px; padding: 6px 8px; background: color-mix(in srgb, var(--accent) 12%, var(--elev)); color: var(--text); font: inherit; font-size: 10px; font-weight: 800; cursor: pointer; }
  .whole-plan-comment:hover, .whole-plan-comment:focus-visible { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 19%, var(--elev)); }
  .whole-plan-comment:disabled { opacity: .5; cursor: default; }
  .author-plan-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; min-width: 0; }
  .author-plan-section, .author-plan-empty { display: grid; align-content: start; gap: 5px; min-width: 0; padding: 9px 10px; border: 1px solid var(--border-soft); border-radius: 9px; background: color-mix(in srgb, var(--panel) 66%, var(--elev)); text-align: left; }
  .author-plan-section { color: var(--text); font: inherit; cursor: pointer; }
  .author-plan-section:hover, .author-plan-section:focus-visible, .author-plan-section.selected { border-color: color-mix(in srgb, var(--accent) 82%, var(--border)); background: color-mix(in srgb, var(--accent) 10%, var(--elev)); outline: none; }
  .author-plan-section.protected { border-color: color-mix(in srgb, #a989ff 52%, var(--border)); }
  .author-plan-section.attention { border-color: color-mix(in srgb, var(--accent) 66%, var(--border)); }
  .author-plan-section:disabled { opacity: .62; cursor: default; }
  .author-plan-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; color: var(--faint); font-size: 9px; font-weight: 850; letter-spacing: .06em; text-transform: uppercase; }
  .author-plan-section-head small { overflow: hidden; color: var(--accent); font-size: 8px; letter-spacing: .03em; text-overflow: ellipsis; white-space: nowrap; }
  .author-plan-section.protected .author-plan-section-head small { color: #b8a2ff; }
  .author-plan-section strong { display: -webkit-box; overflow: hidden; color: var(--text); font-size: 11px; font-weight: 650; line-height: 1.36; -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
  .author-plan-section-detail { display: -webkit-box; overflow: hidden; color: var(--faint); font-size: 9px; line-height: 1.28; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .author-plan-section-action { justify-self: start; color: var(--accent); font-size: 9px; font-weight: 800; }
  .author-plan-empty { grid-column: 1 / -1; color: var(--muted); font-size: 11px; line-height: 1.4; }
  .author-plan-empty strong { color: var(--text); font-size: 11px; }
  .build-order { grid-column: 1 / -1; display: grid; gap: 5px; padding-top: 2px; }
  .build-order ol { display: flex; flex-wrap: wrap; gap: 5px; margin: 0; padding: 0; list-style: none; }
  .build-order li { display: flex; min-width: 0; max-width: 100%; }
  .build-order i { display: grid; place-items: center; width: 16px; flex: none; border: 1px solid color-mix(in srgb, var(--accent) 52%, var(--border)); border-right: 0; border-radius: 6px 0 0 6px; color: var(--accent); font-size: 8px; font-style: normal; font-weight: 850; }
  .build-order button { max-width: 100%; overflow: hidden; border: 1px solid var(--border-soft); border-radius: 0 6px 6px 0; padding: 4px 6px; background: color-mix(in srgb, var(--panel) 76%, var(--elev)); color: var(--muted); font: inherit; font-size: 9px; line-height: 1.25; text-align: left; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
  .build-order button:hover, .build-order button:focus-visible { border-color: var(--accent); color: var(--text); outline: none; }
  .build-order button:disabled { opacity: .58; cursor: default; }
  .plan-feedback { grid-column: 1 / -1; display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; padding-top: 9px; border-top: 1px solid var(--border-soft); }
  .plan-feedback > div { display: grid; gap: 4px; min-width: 0; }
  .plan-feedback p { color: var(--muted); font-size: 10px; line-height: 1.35; }
  .plan-feedback > button { flex: none; max-width: min(45%, 260px); border: 1px solid color-mix(in srgb, var(--accent) 52%, var(--border)); border-radius: 7px; padding: 6px 8px; background: color-mix(in srgb, var(--accent) 10%, var(--elev)); color: var(--text); font: inherit; font-size: 9px; font-weight: 800; line-height: 1.25; text-align: left; cursor: pointer; }
  .plan-feedback > button:hover, .plan-feedback > button:focus-visible { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 18%, var(--elev)); outline: none; }
  .plan-feedback > button:disabled { opacity: .58; cursor: default; }

  @keyframes pulse { 50% { transform: scale(1.2); opacity: .65; } }
  .close-architect { flex: none; border: 1px solid var(--border-soft); border-radius: 8px; padding: 8px 10px; background: transparent; color: var(--muted); font: inherit; font-size: 10px; font-weight: 850; cursor: pointer; }
  .close-architect:hover, .close-architect:focus-visible { border-color: var(--accent); color: var(--text); outline: none; }
  /* Full-screen and conversational by design. Structure is kept quiet until
     a prose plan is ready for explicit confirmation. */
  .architect-surface { border: 0; border-radius: 0; box-shadow: none; }
  .architect-surface.docked { position: relative; box-sizing: border-box; display: block; height: 100%; padding: 12px; border: 0; border-radius: 0; background: color-mix(in srgb, var(--panel) 92%, var(--elev)); box-shadow: none; }
  .docked .architect-head { align-items: flex-start; gap: 10px; padding-bottom: 10px; }
  .docked .architect-head > div:first-child { min-width: 0; }
  .docked .eyebrow { font-size: 8px; }
  .docked h2 { margin-top: 2px; font-size: 21px; }
  .docked .architect-head p { margin-top: 3px; font-size: 9px; line-height: 1.35; }
  .docked .architect-head p b { color: var(--text); }
  .docked .architect-head-actions { align-items: center; gap: 5px; }
  .docked .architect-state { max-width: 120px; padding: 6px 7px; }
  .docked .architect-state strong { font-size: 8px; }
  .docked .architect-state small { display: none; }
  .docked .close-architect { padding: 6px 7px; font-size: 9px; }
  .docked .embedded-close { position: absolute; z-index: 4; top: 9px; right: 9px; background: color-mix(in srgb, var(--panel) 88%, transparent); }
  .history-architect { border: 1px solid var(--border-soft); border-radius: 2px; padding: 6px 7px; background: color-mix(in srgb, var(--panel) 88%, transparent); color: var(--muted); font: inherit; font-size: 9px; font-weight: 800; cursor: pointer; }
  .history-architect:hover, .history-architect:focus-visible { border-color: var(--accent); color: var(--text); outline: none; }
  .docked .embedded-history { position: absolute; z-index: 4; top: 9px; right: 57px; }
  .docked .architect-layout { height: 100%; }
  .docked .thread-panel { padding-top: 30px; }
  .docked .thread-panel { grid-template-rows: minmax(0, 1fr) auto; gap: 8px; }
  .docked .inline-plan { display: none; }
  .docked .inline-plan-head { gap: 8px; }
  .docked .inline-plan-head h3 { font-size: 12px; }
  .docked .inline-plan-head p { display: -webkit-box; overflow: hidden; font-size: 9px; line-height: 1.35; white-space: normal; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .docked .inline-plan-head > button { display: none; }
  .docked .plan-targets { display: none; }
  .docked .plan-focus { display: none; }
  .docked .plan-targets button { flex: none; padding: 4px 7px; }
  .docked .plan-targets small { display: none; }
  .docked .plan-focus { gap: 8px; padding-top: 6px; }
  .docked .plan-focus p { font-size: 9px; -webkit-line-clamp: 3; }
  .docked .plan-focus > button { font-size: 8px; }
  .docked .inline-plan-actions > span { font-size: 8px; }
  .docked .thread { grid-row: 1; width: auto; min-height: 120px; padding: 4px 1px; }
  .docked .thread > div { width: min(94%, 620px); font-size: 11px; }
  .docked .composer { box-sizing: border-box; grid-row: 2; width: 100%; margin: 0; padding: 12px 13px 10px; border-color: var(--border-soft); border-radius: 2px; background: color-mix(in srgb, var(--elev) 72%, var(--panel)); }
  .docked .composer label { color: var(--muted); font-size: 9px; }
  .docked .composer textarea { min-height: 88px; padding: 5px 0; resize: none; font-size: 12px; line-height: 1.5; }
  .docked .composer-actions { padding-top: 8px; border-top: 1px solid var(--border-soft); }
  .docked .composer-actions small { font-size: 8px; }
  .docked .composer-actions button { border-radius: 2px; padding: 7px 12px; font-size: 9px; }
  .architect-layout { grid-template-columns: minmax(0, 1fr); }
  .agent-rail { display: none; }
  .thread-panel { grid-template-rows: auto minmax(180px, 1fr) auto; }
  .thread { grid-row: 2; width: min(100%, 980px); min-height: 180px; max-height: none; margin: 0 auto; padding: 12px 2px; border: 0; border-radius: 0; background: transparent; }
  .thread > div { width: min(86%, 820px); }
  .composer { grid-row: 3; width: min(100%, 980px); margin: 0 auto; }
  .composer textarea { min-height: 96px; }
  .inline-plan { grid-row: 1; width: min(100%, 1180px); max-height: 205px; margin: 0 auto; padding: 11px 14px; border-color: color-mix(in srgb, var(--accent) 30%, var(--border)); background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .inline-plan-head { align-items: center; }
  .inline-plan-head h3 { font-size: 14px; }
  .inline-plan-head p { overflow: hidden; max-width: 820px; color: var(--faint); text-overflow: ellipsis; white-space: nowrap; }
  .inline-plan-sections { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 3px 18px; }
  .inline-plan-sections > button { gap: 2px; padding: 5px 8px; border: 0; border-left: 2px solid var(--border-soft); border-radius: 0; background: transparent; }
  .inline-plan-sections > button:hover, .inline-plan-sections > button.selected { border-left-color: var(--accent); background: color-mix(in srgb, var(--accent) 5%, transparent); }
  .inline-plan-sections strong { display: -webkit-box; overflow: hidden; font-size: 10px; font-weight: 550; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .inline-plan-sections p { display: none; }
  .inline-plan-sections i { font-size: 8px; }
  .plan-targets { display: flex; flex-wrap: wrap; gap: 5px; }
  .plan-targets button { display: inline-flex; align-items: baseline; gap: 5px; border: 1px solid var(--border-soft); border-radius: 999px; padding: 5px 8px; background: transparent; color: var(--muted); font: inherit; cursor: pointer; }
  .plan-targets button:hover, .plan-targets button:focus-visible, .plan-targets button.active { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, transparent); color: var(--text); outline: none; }
  .plan-targets button.attention { border-color: color-mix(in srgb, var(--accent) 58%, var(--border)); }
  .plan-targets span { font-size: 9px; font-weight: 800; }
  .plan-targets small { color: var(--faint); font-size: 8px; text-transform: capitalize; }
  .plan-targets button.active small { color: var(--accent); }
  .plan-focus { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-width: 0; padding: 8px 0 2px; border-top: 1px solid color-mix(in srgb, var(--border-soft) 72%, transparent); }
  .plan-focus > div { display: grid; gap: 3px; min-width: 0; }
  .plan-focus span { color: var(--accent); font-size: 8px; font-weight: 850; letter-spacing: .07em; text-transform: uppercase; }
  .plan-focus p { display: -webkit-box; overflow: hidden; color: var(--text); font-size: 10px; line-height: 1.35; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
  .plan-focus > button { flex: none; border: 0; padding: 5px 0; background: transparent; color: var(--accent); font: inherit; font-size: 9px; font-weight: 850; cursor: pointer; }
  .inline-build-order { display: none; }
  .inline-plan-actions { align-items: center; justify-content: flex-end; padding: 0; }
  .inline-plan-actions > span { margin-right: auto; color: var(--faint); font-size: 9px; }
  .inline-plan-actions button:not(.apply-plan):not(.discard-plan) { display: none; }

  @media (max-width: 860px) { .architect-surface { grid-template-rows: auto auto; height: auto; } .author-plan { grid-template-columns: 1fr; } .architect-layout { grid-template-columns: 1fr; overflow: visible; } .thread-panel { min-height: 620px; } .inline-plan-sections { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 560px) { .architect-surface { padding: 14px; border-radius: 14px; } .architect-head { align-items: stretch; flex-direction: column; } .architect-head-actions { justify-content: space-between; } .architect-state { max-width: none; } .plan-sheet { width: 100vw; border-left: 0; } .plan-sheet-head { padding: 14px; } .plan-sheet-scroll { padding: 14px; } .author-plan-sections { grid-template-columns: 1fr; } .plan-feedback { flex-direction: column; } .plan-feedback > button { max-width: none; } .agent-rail { grid-template-columns: 1fr; } .composer-actions { align-items: flex-end; } .composer-actions small { font-size: 9px; } }
</style>

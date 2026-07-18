<script>
  import { post } from '$lib/api.js';
  import Player from './Player.svelte';

  /**
   * The play tier deliberately receives the public card and a compiled readiness
   * report, never the private runtime contract.  It either explains the next
   * authoring decisions or hands control to the existing live Player.
   */
  let { storyKey, story = null, readiness = null, onedit = null } = $props();

  let localReadiness = $state(null);
  let activeAfterActivation = $state(false);
  let activating = $state(false);
  let error = $state('');
  let previousStoryKey = $state('');

  $effect(() => {
    // A keyed parent normally remounts this component, but reset local lifecycle
    // state too when a shell swaps stories without remounting it.
    if (!previousStoryKey) {
      previousStoryKey = storyKey;
      return;
    }
    if (previousStoryKey !== storyKey) {
      previousStoryKey = storyKey;
      localReadiness = null;
      activeAfterActivation = false;
      activating = false;
      error = '';
    }
  });

  let report = $derived(localReadiness || readiness || null);
  let active = $derived(
    activeAfterActivation
      || story?.fields?.status === 'active'
      || report?.status === 'active'
  );

  const sectionLabels = {
    world: 'World',
    premise: 'Opening',
    first_day: 'First day',
    time_system: 'Entity schedule',
    cast: 'People'
  };

  function labelFor(section) {
    return sectionLabels[section] || 'Story card';
  }

  function edit(section) {
    if (typeof onedit === 'function') onedit(section || 'world');
  }

  let readinessGroups = $derived.by(() => {
    const groups = new Map();
    const blockers = Array.isArray(report?.blockers) ? report.blockers : [];

    for (const raw of blockers) {
      if (!raw || typeof raw !== 'object') continue;
      const section = typeof raw.section === 'string' && raw.section ? raw.section : 'world';
      const message = typeof raw.message === 'string' && raw.message
        ? raw.message
        : 'Make this part of the story explicit.';
      const fix = typeof raw.fix === 'string' ? raw.fix : '';
      const key = `${raw.code || ''}:${message}`;
      let group = groups.get(section);
      if (!group) {
        group = { section, items: [], itemKeys: new Map() };
        groups.set(section, group);
      }
      const prior = group.itemKeys.get(key);
      if (prior) prior.count += 1;
      else {
        const item = { message, fix, count: 1 };
        group.items.push(item);
        group.itemKeys.set(key, item);
      }
    }

    return [...groups.values()].map(({ section, items }) => ({ section, items }));
  });

  async function activate() {
    if (activating || !report?.ready || active) return;
    activating = true;
    error = '';
    try {
      const result = await post(`/stories/${storyKey}/activate`);
      if (!result.ok) {
        localReadiness = result.data?.readiness || report;
        throw new Error(result.data?.error || 'This story still needs a few decisions before it can start.');
      }
      localReadiness = result.data?.readiness || { ...report, ready: true, status: 'active' };
      activeAfterActivation = true;
      // Keep other authoring surfaces in sync without exposing the private
      // compiled contract in the browser.
      window.dispatchEvent(new CustomEvent('story:refresh', { detail: { key: storyKey } }));
    } catch (cause) {
      error = cause?.message || 'Could not start live play.';
    } finally {
      activating = false;
    }
  }
</script>

{#if active}
  <div class="live-play" aria-label="Live story play">
    <Player {storyKey} />
  </div>
{:else}
  <section class="play-gate" aria-labelledby="play-title">
    <header class="gate-header">
      <div>
        <span class="eyebrow">Live play</span>
        <h2 id="play-title">{story?.name || 'This story'}</h2>
      </div>
      {#if report?.ready}
        <span class="state ready">Ready</span>
      {:else}
        <span class="state">Authoring</span>
      {/if}
    </header>

    {#if report?.ready}
      <div class="ready-panel">
        <div>
          <strong>The opening is ready to run.</strong>
          <p>Starting play locks this authored setup into a live session. You can still return to the Story and Director views afterward.</p>
        </div>
        <button type="button" class="start" onclick={activate} disabled={activating}>
          {activating ? 'Starting…' : 'Start play'}
        </button>
      </div>
    {:else if report}
      <p class="intro">Before the narrator can safely begin, make these decisions explicit on the story card.</p>

      {#if readinessGroups.length}
        <div class="readiness-groups" aria-label="Story decisions needed before play">
          {#each readinessGroups as group (group.section)}
            <section class="readiness-group">
              <div class="group-heading">
                <span>{labelFor(group.section)}</span>
                <button type="button" onclick={() => edit(group.section)}>Edit</button>
              </div>
              <div class="readiness-items">
                {#each group.items as item (item.message)}
                  <button type="button" class="readiness-item" onclick={() => edit(group.section)}>
                    <span class="item-copy">
                      <strong>{item.message}{#if item.count > 1} <em>({item.count} scenes)</em>{/if}</strong>
                      {#if item.fix}<small>{item.fix}</small>{/if}
                    </span>
                    <span class="arrow" aria-hidden="true">→</span>
                  </button>
                {/each}
              </div>
            </section>
          {/each}
        </div>
      {:else}
        <div class="empty-state">
          <strong>The card needs one more play-readiness pass.</strong>
          <button type="button" onclick={() => edit('world')}>Open the story card</button>
        </div>
      {/if}
    {:else}
      <div class="empty-state">
        <strong>Checking what this story needs to play.</strong>
        <p>Return to the Story view if this does not resolve.</p>
      </div>
    {/if}

    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </section>
{/if}

<style>
  .live-play { min-height: min(780px, calc(100dvh - var(--chrome-top, 0px) - 32px)); }
  .play-gate { box-sizing: border-box; width: min(800px, 100%); margin: clamp(24px, 8vh, 88px) auto; padding: clamp(20px, 4vw, 34px); border: 1px solid color-mix(in srgb, var(--accent) 28%, var(--border)); border-radius: 16px; background: color-mix(in srgb, var(--accent) 5%, var(--panel)); }
  .gate-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding-bottom: 18px; border-bottom: 1px solid var(--border-soft); }
  .eyebrow { display: block; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .1em; text-transform: uppercase; }
  h2 { margin: 5px 0 0; color: var(--text); font-size: clamp(24px, 4vw, 34px); letter-spacing: -.035em; }
  .state { flex: none; padding: 5px 8px; border: 1px solid var(--border-soft); color: var(--muted); font-size: 10px; font-weight: 800; letter-spacing: .07em; text-transform: uppercase; }
  .state.ready { border-color: color-mix(in srgb, var(--good, #6ec77f) 55%, var(--border)); color: var(--good, #6ec77f); }
  .intro { max-width: 590px; margin: 18px 0; color: var(--muted); font-size: 14px; line-height: 1.55; }
  .ready-panel { display: flex; align-items: center; justify-content: space-between; gap: 22px; margin-top: 20px; padding: 18px; border: 1px solid color-mix(in srgb, var(--good, #6ec77f) 36%, var(--border)); background: color-mix(in srgb, var(--good, #6ec77f) 8%, var(--elev)); }
  .ready-panel strong { color: var(--text); font-size: 15px; }
  .ready-panel p { max-width: 520px; margin: 5px 0 0; color: var(--muted); font-size: 13px; line-height: 1.45; }
  .start { flex: none; border: 0; padding: 10px 13px; background: var(--accent); color: #0b0e14; font: inherit; font-size: 12px; font-weight: 800; cursor: pointer; }
  .start:disabled { cursor: default; opacity: .6; }
  .readiness-groups { display: grid; gap: 16px; }
  .readiness-group { border: 1px solid var(--border-soft); background: var(--elev); }
  .group-heading { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 9px 11px; border-bottom: 1px solid var(--border-soft); color: var(--faint); font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
  .group-heading button, .empty-state button { border: 0; padding: 0; background: transparent; color: var(--accent); font: inherit; font-size: 10px; font-weight: 800; letter-spacing: .05em; cursor: pointer; }
  .group-heading button:hover, .empty-state button:hover { text-decoration: underline; }
  .readiness-items { display: grid; }
  .readiness-item { display: flex; align-items: center; justify-content: space-between; gap: 16px; width: 100%; padding: 12px 11px; border: 0; border-bottom: 1px solid var(--border-soft); background: transparent; color: inherit; text-align: left; font: inherit; transition: background .16s ease; cursor: pointer; }
  .readiness-item:last-child { border-bottom: 0; }
  .readiness-item:hover { background: color-mix(in srgb, var(--accent) 7%, transparent); }
  .item-copy { display: grid; gap: 4px; min-width: 0; }
  .item-copy strong { color: var(--text); font-size: 13px; font-weight: 650; line-height: 1.35; }
  .item-copy em { color: var(--faint); font-size: 11px; font-style: normal; font-weight: 500; white-space: nowrap; }
  .item-copy small { color: var(--muted); font-size: 12px; line-height: 1.4; }
  .arrow { flex: none; color: var(--accent); font-size: 17px; }
  .empty-state { display: grid; gap: 6px; margin-top: 20px; padding: 18px; border: 1px solid var(--border-soft); background: var(--elev); }
  .empty-state strong { color: var(--text); font-size: 14px; }
  .empty-state p { margin: 0; color: var(--muted); font-size: 12px; }
  .error { margin: 14px 0 0; color: var(--bad, #d0655a); font-size: 13px; line-height: 1.4; }
  @media (max-width: 620px) { .play-gate { margin: 20px 0; border-right: 0; border-left: 0; border-radius: 0; } .ready-panel { align-items: stretch; flex-direction: column; } .start { width: 100%; } }
</style>

<script>
  /* A deliberately compact, target-scoped authoring thread. */
  let {
    target = null,
    messages = [],
    suggestions = [],
    busy = false,
    error = '',
    typed = $bindable(''),
    voiceSupported = false,
    listening = false,
    onclose = null,
    onsend = null,
    onchoose = null,
    ontogglemic = null
  } = $props();

  let composer = $state(null);
  const hiddenOpen = '[[hidden]]';
  const hiddenClose = '[[/hidden]]';

  function messageParts(text) {
    const source = String(text || '');
    const tag = /\[\[\s*(\/)?\s*(?:model[-_\s]?)?hidden\s*\]\]/gi;
    const parts = [];
    let depth = 0;
    let cursor = 0;
    let match;
    const push = (value, hidden) => { if (value) parts.push({ text: value, hidden }); };
    while ((match = tag.exec(source))) {
      push(source.slice(cursor, match.index), depth > 0);
      if (match[1]) depth = Math.max(0, depth - 1);
      else depth += 1;
      cursor = tag.lastIndex;
    }
    push(source.slice(cursor), depth > 0);
    return parts.length ? parts : [{ text: '', hidden: false }];
  }

  function addAuthorOnly() {
    if (busy) return;
    const start = composer?.selectionStart ?? typed.length;
    const end = composer?.selectionEnd ?? start;
    const selected = typed.slice(start, end);
    typed = `${typed.slice(0, start)}${hiddenOpen}${selected}${hiddenClose}${typed.slice(end)}`;
    requestAnimationFrame(() => {
      if (!composer) return;
      composer.focus();
      const cursor = start + hiddenOpen.length;
      composer.setSelectionRange(cursor, cursor + selected.length);
    });
  }

  function send() {
    const value = typed.trim();
    if (!value || busy || typeof onsend !== 'function') return;
    onsend(value);
  }

  function closeFromBackdrop(event) {
    if (event.target === event.currentTarget && typeof onclose === 'function') onclose();
  }

  $effect(() => {
    requestAnimationFrame(() => composer?.focus());
  });
</script>

<div class="editorial-overlay" role="presentation" onclick={closeFromBackdrop}>
  <div class="editorial-modal" role="dialog" aria-modal="true" aria-label={`Edit ${target?.label || 'story section'}`} tabindex="-1">
    <header class="editorial-head">
      <div>
        <span class="eyebrow">{target?.item ? 'Specific story item' : 'Story section'}</span>
        <h2>{target?.label || 'Edit story section'}</h2>
        {#if target?.summary}<p>{target.summary}</p>{/if}
      </div>
      <button type="button" class="close" onclick={() => onclose?.()} aria-label="Close editor">×</button>
    </header>

    {#if suggestions.length}
      <section class="suggestion-strip" aria-label="Suggested improvements">
        <span>Possible improvements</span>
        <div>
          {#each suggestions as suggestion, index (suggestion.id || suggestion.title || index)}
            <button type="button" onclick={() => onchoose?.(suggestion)}>
              <b>{suggestion.title || suggestion.label || 'Improve this'}</b>
              {#if suggestion.detail || suggestion.reason}<small>{suggestion.detail || suggestion.reason}</small>{/if}
            </button>
          {/each}
        </div>
      </section>
    {/if}

    <section class="thread" aria-label={`Conversation about ${target?.label || 'this story section'}`}>
      {#each messages.slice(-8) as message, index (`${message.role || 'assistant'}-${index}-${message.text || ''}`)}
        <div class:author={message.role === 'user'} class:assistant={message.role !== 'user'}>
          {#if message.loading}
            <span class="thinking">Finding the most useful improvement…</span>
          {:else}
            {#each message.role === 'user' ? messageParts(message.text) : [{ text: message.text, hidden: false }] as part}
              {#if part.hidden}
                <span class="author-only"><span>Only you</span>{part.text}</span>
              {:else}
                {part.text}
              {/if}
            {/each}
          {/if}
        </div>
      {:else}
        <p class="empty-thread">Preparing a focused set of improvements…</p>
      {/each}
      {#if error}<p class="error">{error}</p>{/if}
    </section>

    <form class="composer" onsubmit={(event) => { event.preventDefault(); send(); }}>
      <input bind:this={composer} bind:value={typed} disabled={busy} placeholder={`What should change about ${target?.label || 'this'}?`} />
      <button class="private-note" type="button" onclick={addAuthorOnly} onpointerdown={(event) => event.preventDefault()} disabled={busy} title="Wrap selected text as author-only" aria-label="Wrap selected text as author-only">
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="5" y="10" width="14" height="10" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v2"/></svg><span>Only me</span>
      </button>
      {#if voiceSupported}
        <button class="icon mic" type="button" class:listening onclick={() => ontogglemic?.()} disabled={busy} title={listening ? 'Stop listening' : 'Speak your change'} aria-label={listening ? 'Stop listening' : 'Speak your change'}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="8" y="3" width="8" height="12" rx="4"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></svg>
        </button>
      {/if}
      <button class="icon send" aria-label="Send change" title="Send" disabled={busy || !typed.trim()}>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 18V6M7 11l5-5 5 5"/></svg>
      </button>
    </form>
  </div>
</div>

<style>
  .editorial-overlay { position: fixed; z-index: 100; inset: 0; display: grid; place-items: center; padding: 22px; background: color-mix(in srgb, #05070b 66%, transparent); backdrop-filter: blur(3px); }
  .editorial-modal { box-sizing: border-box; display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; gap: 11px; width: min(640px, calc(100vw - 44px)); max-height: min(650px, calc(100dvh - 44px)); padding: 16px; border: 1px solid color-mix(in srgb, var(--accent) 38%, var(--border)); border-radius: 16px; background: var(--panel); box-shadow: 0 24px 70px rgba(0,0,0,.5); }
  .editorial-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; } .eyebrow { color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; } h2, p { margin: 0; } h2 { margin-top: 3px; color: var(--text); font-size: 20px; letter-spacing: -.02em; } .editorial-head p { max-width: 510px; margin-top: 5px; color: var(--muted); font-size: 12px; line-height: 1.4; }
  .close { width: 31px; height: 31px; flex: none; border: 1px solid var(--border-soft); border-radius: 8px; background: var(--elev); color: var(--muted); font: inherit; font-size: 20px; line-height: 1; cursor: pointer; } .close:hover { color: var(--text); border-color: var(--muted); }
  .suggestion-strip { display: grid; gap: 6px; padding: 9px; border: 1px solid color-mix(in srgb, var(--accent) 24%, var(--border)); background: color-mix(in srgb, var(--accent) 5%, var(--elev)); } .suggestion-strip > span { color: var(--faint); font-size: 9px; font-weight: 800; letter-spacing: .06em; text-transform: uppercase; } .suggestion-strip > div { display: flex; gap: 6px; overflow-x: auto; padding-bottom: 1px; } .suggestion-strip button { display: grid; flex: 0 0 min(260px, 76%); gap: 2px; padding: 7px 8px; border: 1px solid var(--border-soft); border-radius: 7px; background: var(--panel); color: var(--text); text-align: left; font: inherit; cursor: pointer; } .suggestion-strip button:hover { border-color: var(--accent); } .suggestion-strip b { font-size: 11px; line-height: 1.3; } .suggestion-strip small { color: var(--muted); font-size: 10px; line-height: 1.3; }
  .thread { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; min-height: 110px; overflow-y: auto; padding: 3px 2px; scrollbar-width: none; } .thread::-webkit-scrollbar { display: none; } .thread > div { box-sizing: border-box; width: min(100%, 510px); padding: 9px 10px; border-radius: 10px; background: var(--elev); color: var(--text); font-size: 13px; line-height: 1.43; white-space: pre-wrap; } .thread > .author { align-self: flex-end; background: var(--accent); color: #0b0e14; } .thinking, .empty-thread { color: var(--muted); font-size: 12px; } .error { color: var(--bad, #d0655a); font-size: 12px; }
  .author-only { display: inline; margin: 0 1px; padding: 1px 4px; border: 1px solid color-mix(in srgb, currentColor 34%, transparent); border-radius: 3px; background: color-mix(in srgb, currentColor 9%, transparent); color: inherit; font-weight: 650; white-space: pre-wrap; } .author-only > span { margin-right: 4px; font-size: 9px; font-weight: 850; letter-spacing: .05em; text-transform: uppercase; }
  .composer { display: flex; align-items: center; gap: 7px; padding: 7px; border: 1px solid var(--border-soft); border-radius: 12px; background: var(--elev); } input { flex: 1; min-width: 0; border: 0; outline: 0; padding: 7px 5px; background: transparent; color: var(--text); font: inherit; font-size: 13px; } .private-note { display: inline-flex; align-items: center; gap: 4px; flex: none; min-height: 34px; padding: 0 7px; border: 1px solid color-mix(in srgb, var(--accent) 38%, var(--border)); border-radius: 9px; background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--muted); font: inherit; font-size: 10px; font-weight: 750; cursor: pointer; } .private-note svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; } .private-note:hover { border-color: var(--accent); color: var(--text); }
  .icon { width: 34px; height: 34px; display: grid; place-items: center; flex: none; padding: 0; border: 1px solid var(--border-soft); border-radius: 9px; background: transparent; color: var(--muted); cursor: pointer; } .icon svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; } .mic.listening { color: var(--bad, #d0655a); border-color: var(--bad, #d0655a); } .send { border-color: var(--accent); background: var(--accent); color: #0b0e14; } .icon:disabled, .private-note:disabled { opacity: .42; cursor: default; }
  @media (max-width: 560px) { .editorial-overlay { padding: 10px; } .editorial-modal { width: 100%; max-height: calc(100dvh - 20px); padding: 13px; border-radius: 13px; } .editorial-head p { font-size: 11px; } .private-note span { display: none; } }
</style>

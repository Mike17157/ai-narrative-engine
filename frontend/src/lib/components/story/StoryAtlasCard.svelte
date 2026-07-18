<script>
  /** One compact dramatic kernel inside StoryAtlas. */
  let {
    kernel = {},
    compact = true,
    selected = false,
    canExpand = false,
    onselect = null,
    ontoggle = null,
    ondetail = null
  } = $props();

  let details = $derived(Array.isArray(kernel?.details) ? kernel.details.filter(Boolean) : []);
  let status = $derived(String(kernel?.status || 'empty').replace(/_/g, ' '));
  let hasDetails = $derived(details.length > 0);

  function choose() { onselect?.(kernel); }
  function toggle() { ontoggle?.(kernel); }
  function chooseDetail(detail) { ondetail?.(detail, kernel); }
</script>

<article class="atlas-card" class:selected class:protected={kernel?.protected} data-atlas-kernel={kernel?.id || ''}>
  <div class="atlas-card-head">
    <button type="button" class="atlas-card-main" aria-current={selected ? 'true' : undefined} onclick={choose}>
      <span class="atlas-kicker">{kernel?.protected ? 'Sealed · ' : ''}{kernel?.eyebrow || 'Story kernel'}</span>
      <span class="atlas-titleline"><strong>{kernel?.label || 'Untitled kernel'}</strong><small class:empty={status === 'empty'}>{status}</small></span>
      <span class="atlas-summary">{kernel?.summary || 'Nothing is recorded here yet.'}</span>
    </button>
    {#if canExpand && hasDetails}
      <button type="button" class="atlas-expand" aria-expanded={!compact} aria-label={`${compact ? 'Expand' : 'Compact'} ${kernel?.label || 'story kernel'}`} onclick={toggle}>
        {compact ? 'Expand' : 'Compact'}
      </button>
    {/if}
  </div>

  {#if !compact && hasDetails}
    <div class="atlas-details">
      {#each details as detail, index (detail?.id || index)}
        <button type="button" class="atlas-detail" onclick={() => chooseDetail(detail)}>
          <span class="atlas-detail-name">{detail?.label || 'Untitled detail'}</span>
          {#if detail?.summary}<span class="atlas-detail-summary">{detail.summary}</span>{/if}
          {#if detail?.facts?.length}
            <span class="atlas-facts">
              {#each detail.facts as fact, factIndex (factIndex)}<span>{fact}</span>{/each}
            </span>
          {/if}
        </button>
      {/each}
    </div>
  {/if}
</article>

<style>
  .atlas-card { display: grid; min-width: 0; border: 1px solid var(--border-soft); border-radius: 12px; background: color-mix(in srgb, var(--panel) 88%, var(--elev)); overflow: hidden; transition: border-color .16s, background .16s, box-shadow .16s; }
  .atlas-card:hover { border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); background: color-mix(in srgb, var(--accent) 4%, var(--panel)); }
  .atlas-card.selected { border-color: var(--accent); box-shadow: inset 2px 0 0 var(--accent); }
  .atlas-card.protected { border-style: dashed; border-color: color-mix(in srgb, #b69cff 58%, var(--border-soft)); }
  .atlas-card.protected .atlas-kicker { color: #c6b4f5; }
  .atlas-card-head { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: stretch; min-width: 0; }
  .atlas-card-main { display: grid; gap: 5px; min-width: 0; padding: 12px; border: 0; background: transparent; color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .atlas-card-main:hover { background: color-mix(in srgb, var(--accent) 5%, transparent); }
  .atlas-card-main:focus-visible, .atlas-expand:focus-visible, .atlas-detail:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .atlas-kicker { color: var(--accent); font-size: 9px; font-weight: 850; letter-spacing: .075em; text-transform: uppercase; }
  .atlas-titleline { display: flex; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; gap: 4px 8px; }
  .atlas-titleline strong { min-width: 0; color: var(--text); font-size: 13px; line-height: 1.28; overflow-wrap: anywhere; }
  .atlas-titleline small { flex: none; color: var(--good, #6ec77f); font-size: 8px; font-weight: 800; letter-spacing: .055em; line-height: 1.35; text-transform: uppercase; }
  .atlas-titleline small.empty { color: var(--faint); }
  .atlas-summary { color: var(--muted); font-size: 11px; line-height: 1.42; overflow-wrap: anywhere; }
  .atlas-expand { align-self: stretch; border: 0; border-left: 1px solid var(--border-soft); padding: 8px; background: transparent; color: var(--faint); font: inherit; font-size: 9px; font-weight: 800; letter-spacing: .03em; cursor: pointer; writing-mode: vertical-rl; transform: rotate(180deg); }
  .atlas-expand:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); color: var(--text); }
  .atlas-details { display: grid; gap: 6px; padding: 0 8px 8px; border-top: 1px solid var(--border-soft); }
  .atlas-detail { display: grid; gap: 4px; width: 100%; min-width: 0; padding: 9px 8px; border: 1px solid transparent; border-radius: 8px; background: color-mix(in srgb, var(--accent) 3%, transparent); color: var(--text); font: inherit; text-align: left; cursor: pointer; }
  .atlas-detail:hover { border-color: color-mix(in srgb, var(--accent) 35%, var(--border)); background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .atlas-detail-name { color: var(--text); font-size: 11px; font-weight: 800; line-height: 1.3; overflow-wrap: anywhere; }
  .atlas-detail-summary { color: var(--muted); font-size: 10.5px; line-height: 1.38; overflow-wrap: anywhere; }
  .atlas-facts { display: flex; flex-wrap: wrap; gap: 4px; padding-top: 2px; }
  .atlas-facts span { padding: 2px 5px; border: 1px solid color-mix(in srgb, var(--accent) 20%, var(--border)); border-radius: 4px; color: var(--faint); font-size: 9px; line-height: 1.25; overflow-wrap: anywhere; }
  @media (max-width: 520px) { .atlas-expand { min-width: 42px; writing-mode: initial; transform: none; } }
</style>

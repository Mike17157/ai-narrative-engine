<script>
  // A schema-driven view of the Story context card. The API owns the card shape;
  // this component deliberately knows only layers/content/todos, so new card
  // fields appear without a corresponding frontend release.
  import { get } from '$lib/api.js';

  let { storyKey } = $props();
  let card = $state(null);
  let loading = $state(false);

  async function load() {
    loading = true;
    try { card = await get(`/stories/${storyKey}/card`); }
    finally { loading = false; }
  }
  $effect(() => { if (storyKey) load(); });

  const title = (key) => String(key).replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  const scalar = (v) => v === null ? '—' : String(v);
  const entries = (value) => Object.entries(value || {}).filter(([key]) => key !== 'primary');
</script>

<div class="story-card">
  <header>
    <div><span class="eyebrow">Story card</span><h2>Living story context</h2></div>
    <button class="ghost sm" onclick={load} disabled={loading}>{loading ? 'Loading…' : '↻ Refresh'}</button>
  </header>

  {#if card?.layers?.length}
    <div class="layers">
      {#each card.layers as layer (layer.id)}
        <section class="layer" data-tier={layer.tier}>
          <div class="layer-head">
            <div><span class="tier">{layer.tier}</span><h3>{layer.label}</h3></div>
            <span class="owner">{layer.mutators?.join(' · ')}</span>
          </div>
          <dl class="content">
            {#each Object.entries(layer.content || {}) as [key, value] (key)}
              <div class="field">
                <dt>{title(key)}</dt>
                <dd>
                  {#if Array.isArray(value)}
                    {#if value.length}
                      <div class="records">{#each value as item, i (item?.id || item?.character || i)}
                        {#if item && typeof item === 'object'}
                          <div class="record">{#each entries(item) as [itemKey, itemValue] (itemKey)}<span class="rk">{title(itemKey)}</span><span class="rv">{typeof itemValue === 'object' ? JSON.stringify(itemValue) : scalar(itemValue)}</span>{/each}</div>
                        {:else}<span>{scalar(item)}</span>{/if}
                      {/each}</div>
                    {:else}<span class="empty">None yet</span>{/if}
                  {:else if value && typeof value === 'object'}
                    {#if entries(value).length}<div class="record">{#each entries(value) as [itemKey, itemValue] (itemKey)}<span class="rk">{title(itemKey)}</span><span class="rv">{typeof itemValue === 'object' ? JSON.stringify(itemValue) : scalar(itemValue)}</span>{/each}</div>{:else}<span class="empty">None yet</span>{/if}
                  {:else}<span class:empty={!value}>{scalar(value)}</span>{/if}
                </dd>
              </div>
            {/each}
          </dl>
          {#if layer.todo?.length}
            <div class="todo"><b>Next</b>{#each layer.todo as item (item)}<span>{item}</span>{/each}</div>
          {/if}
        </section>
      {/each}
    </div>
  {:else if !loading}
    <p class="empty">This story card has no layers yet.</p>
  {/if}
</div>

<style>
  .story-card { max-width: 960px; display: flex; flex-direction: column; gap: 16px; }
  header, .layer-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
  .eyebrow, .tier { color: var(--accent); font-size: 10px; font-weight: 750; letter-spacing: .8px; text-transform: uppercase; }
  h2 { margin: 2px 0 0; font-size: 22px; } h3 { margin: 3px 0 0; font-size: 16px; }
  .layers { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
  .layer { border: 1px solid var(--border); border-radius: 12px; padding: 14px; background: var(--elev); }
  .layer[data-tier="progression"] { border-left: 3px solid #9173e8; }
  .layer[data-tier="production"] { border-left: 3px solid #df9f52; }
  .owner { font-size: 11px; color: var(--faint); }
  .content { margin: 14px 0 0; display: grid; gap: 10px; }
  .field { display: grid; gap: 3px; } dt { font-size: 10.5px; text-transform: uppercase; letter-spacing: .35px; color: var(--muted); font-weight: 700; }
  dd { margin: 0; font-size: 13px; line-height: 1.45; white-space: pre-wrap; } .records { display: grid; gap: 6px; } .record { display: grid; grid-template-columns: minmax(72px, auto) 1fr; gap: 3px 10px; padding: 8px; border-radius: 7px; background: var(--elev-2); } .rk { font-size: 10px; text-transform: uppercase; letter-spacing: .3px; color: var(--faint); } .rv { min-width: 0; overflow-wrap: anywhere; }
  .empty { color: var(--faint); font-style: italic; } .todo { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--border-soft); display: grid; gap: 5px; font-size: 12px; color: var(--muted); }
  .todo b { color: var(--text); font-size: 11px; text-transform: uppercase; letter-spacing: .4px; }
</style>

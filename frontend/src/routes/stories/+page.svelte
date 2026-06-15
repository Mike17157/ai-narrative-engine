<script>
  import { goto } from '$app/navigation';
  import { chars } from '$lib/characters.svelte.js';
  import { stories, deleteStory, startWizard } from '$lib/stories.svelte.js';

  function newStory() {
    const c = chars.list.find((x) => !x.story) || chars.list[0];
    startWizard(c?.key || '', c?.name || '');
  }
  const open = (key) => goto(`/stories/${key}/overview`);
</script>

<div class="page"><div class="col wide">
  {#if stories.msg}<div class="msg" class:ok={stories.msg.ok} class:err={stories.msg.err}>{stories.msg.text}</div>{/if}

  {#if stories.list.length}
    <div class="libhead">
      <span class="lo">{stories.list.length} stor{stories.list.length === 1 ? 'y' : 'ies'}</span>
      <button onclick={newStory}>＋ New story</button>
    </div>
    <div class="grid">
      {#each stories.list as s (s.key)}
        <div class="card" onclick={() => open(s.key)} role="button" tabindex="0">
          <div class="cname">{s.name}</div>
          <p class="cprem">{s.premise || 'No premise.'}</p>
          <div class="cmeta">
            <span class="badge">{s.locations} location{s.locations === 1 ? '' : 's'}</span>
            <span class="badge">{s.cast.length} cast</span>
            {#if s.tone}<span class="tone">{s.tone}</span>{/if}
          </div>
          <button class="playbtn" title="Play" onclick={(e) => { e.stopPropagation(); goto(`/stories/${s.key}/play`); }}>▶</button>
          <button class="del" title="Delete story" onclick={(e) => { e.stopPropagation(); deleteStory(s.key); }}>🗑</button>
        </div>
      {/each}
    </div>
  {:else}
    <div class="empty">
      <div class="emk">📖</div>
      <p>No stories yet.</p>
      <span>Storyboard a plausible story from a character, then extract its scenes and cast.</span>
      <button onclick={newStory}>＋ New story</button>
    </div>
  {/if}
</div></div>

<style>
  .libhead { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
  .libhead .lo { font-size: 12px; color: var(--faint); }
  .msg { margin: 10px 0; font-size: 13px; }
  .msg.ok { color: var(--good); } .msg.err { color: var(--bad); }

  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 14px; margin-top: 16px; }
  .card { position: relative; background: var(--panel); border: 1px solid var(--border-soft); border-radius: 14px; padding: 14px; cursor: pointer; }
  .card:hover { border-color: var(--border); background: var(--elev); }
  .cname { font-size: 15px; font-weight: 650; margin-bottom: 6px; }
  .cprem { margin: 0 0 10px; font-size: 12.5px; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .cmeta { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .badge { font-size: 11px; background: var(--elev-2); border: 1px solid var(--border-soft); border-radius: 999px; padding: 2px 9px; color: var(--muted); }
  .tone { font-size: 11px; color: var(--faint); }
  .del { position: absolute; top: 8px; right: 8px; width: 26px; height: 26px; padding: 0; border-radius: 7px; box-shadow: none; background: rgba(10,12,18,.6); border: 1px solid var(--border); color: var(--muted); font-size: 12px; opacity: 0; }
  .card:hover .del { opacity: 1; }
  .del:hover { color: var(--bad); filter: none; }
  .playbtn { position: absolute; top: 8px; right: 40px; width: 26px; height: 26px; padding: 0; border-radius: 7px;
    box-shadow: none; background: rgba(10,12,18,.6); border: 1px solid var(--border); color: #fff; font-size: 11px; opacity: 0; }
  .card:hover .playbtn { opacity: 1; }
  .playbtn:hover { color: var(--accent); border-color: var(--accent); filter: none; }

  .empty { margin: 60px auto; text-align: center; color: var(--muted); display: flex; flex-direction: column; align-items: center; gap: 6px; }
  .emk { font-size: 34px; } .empty p { margin: 0; color: var(--text); font-size: 16px; } .empty button { margin-top: 12px; }
</style>

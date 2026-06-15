<script>
  import { charName } from '$lib/characters.svelte.js';
  import { stories, deleteStory, regenStory } from '$lib/stories.svelte.js';

  let st = $derived(stories.current);
</script>

<div class="page"><div class="col">
  <div class="vacts">
    <button class="ghost sm" onclick={() => regenStory(st)}>↻ Regenerate</button>
    <button class="ghost sm" onclick={() => deleteStory(st.key)}>Delete</button>
  </div>
  {#if st.storyboard?.logline}<p class="logline">{st.storyboard.logline}</p>{/if}
  <p class="prem">{st.premise}</p>
  {#if st.tone}<div class="kv"><b>Tone</b> {st.tone}</div>{/if}
  {#if st.themes?.length}<div class="chips">{#each st.themes as t}<span class="chip">{t}</span>{/each}</div>{/if}
  <div class="kv"><b>Cast</b> {st.cast.map((m) => charName(m.character)).join(', ')}</div>

  {#if st.storyboard?.beats?.length}
    <h4>Storyboard <span class="lo">— {st.storyboard.beats.length} chapters</span></h4>
    <ol class="beats">
      {#each st.storyboard.beats as b, i}
        <li>
          <div class="btitle">{b.title || `Chapter ${i + 1}`}</div>
          <span class="bsum">{b.summary}</span>
          <span class="bmeta">{#if b.location}@ {b.location}{/if}{#if b.characters?.length} · {b.characters.join(', ')}{/if}</span>
        </li>
      {/each}
    </ol>
  {/if}
</div></div>

<style>
  .vacts { display: flex; gap: 8px; justify-content: flex-end; }
  .logline { font-size: 14.5px; color: var(--text); font-style: italic; margin: 8px 0; }
  .prem { font-size: 13.5px; color: var(--muted); margin: 8px 0; }
  .kv { font-size: 13px; color: var(--muted); margin: 4px 0; } .kv b { color: var(--text); margin-right: 6px; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; margin: 8px 0; }
  .chip { font-size: 11.5px; padding: 2px 9px; border-radius: 999px; background: var(--elev); border: 1px solid var(--border-soft); color: var(--muted); }
  h4 { margin: 18px 0 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .4px; color: var(--muted); }
  .lo { color: var(--faint); font-weight: 400; text-transform: none; letter-spacing: 0; }
  .beats { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 12px; }
  .beats li { font-size: 13px; color: var(--text); }
  .btitle { font-weight: 700; font-size: 13.5px; margin-bottom: 2px; }
  .bmeta { color: var(--faint); font-size: 11.5px; margin-left: 6px; }
</style>

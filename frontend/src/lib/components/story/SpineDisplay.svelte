<script>
  // Renders an EmotionalSpine — wound / lie / truth anchors + beats timeline.
  // Used in the wizard spine step and the story overview page.
  let { spine, compact = false } = $props();
</script>

{#if spine}
  <div class="spine" class:compact>

    <!-- Wound / Lie / Truth anchors -->
    <div class="anchors">
      <div class="anchor wound">
        <span class="anchor-label">Wound</span>
        <span class="anchor-text">{spine.wound || '—'}</span>
      </div>
      <div class="arrow">→</div>
      <div class="anchor lie">
        <span class="anchor-label">Lie they tell themselves</span>
        <span class="anchor-text">{spine.lie || '—'}</span>
      </div>
      <div class="arrow">→</div>
      <div class="anchor truth">
        <span class="anchor-label">Truth they must accept</span>
        <span class="anchor-text">{spine.truth || '—'}</span>
      </div>
    </div>

    <!-- Emotional beats — horizontal station track -->
    {#if spine.beats?.length}
      <div class="beats-track">
        <div class="bt-label">Emotional beats</div>
        <div class="beats">
          {#each spine.beats as beat, i}
            <div class="beat">
              <div class="beat-num">{i + 1}</div>
              <div class="beat-body">
                <span class="beat-inf">{beat.inflection}</span>
                {#if !compact && beat.description}
                  <span class="beat-desc">{beat.description}</span>
                {/if}
              </div>
              {#if i < spine.beats.length - 1}
                <div class="beat-connector"></div>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Heart -->
    {#if spine.heart}
      <div class="heart-row">
        <span class="heart-icon">♡</span>
        <span class="heart-text">{spine.heart}</span>
      </div>
    {/if}

  </div>
{/if}

<style>
  .spine {
    display: flex; flex-direction: column; gap: 14px;
    background: var(--panel, #1a1d27);
    border: 1px solid var(--border-soft, rgba(255,255,255,.08));
    border-radius: 12px; padding: 16px 18px;
  }

  /* Wound → Lie → Truth row */
  .anchors {
    display: flex; align-items: flex-start; gap: 8px; flex-wrap: wrap;
  }
  .anchor {
    flex: 1; min-width: 140px; border-radius: 9px; padding: 10px 13px;
    display: flex; flex-direction: column; gap: 4px;
  }
  .anchor.wound  { background: rgba(255,120,90,.08); border: 1px solid rgba(255,120,90,.22); }
  .anchor.lie    { background: rgba(255,200,60,.07); border: 1px solid rgba(255,200,60,.2); }
  .anchor.truth  { background: rgba(100,210,120,.07); border: 1px solid rgba(100,210,120,.22); }
  .anchor-label {
    font-size: 9.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .5px;
  }
  .wound .anchor-label  { color: rgba(255,130,100,.9); }
  .lie .anchor-label    { color: rgba(255,200,80,.9); }
  .truth .anchor-label  { color: rgba(100,210,130,.9); }
  .anchor-text { font-size: 12.5px; color: var(--text, #e0e4f0); line-height: 1.45; font-style: italic; }
  .arrow {
    flex: none; align-self: center; color: var(--faint, #555b78);
    font-size: 16px; padding-top: 14px;
  }

  /* Beat track */
  .beats-track { display: flex; flex-direction: column; gap: 6px; }
  .bt-label { font-size: 9.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .4px; color: var(--faint, #555b78); }
  .beats {
    display: flex; flex-wrap: wrap; gap: 0; align-items: stretch;
  }
  .beat {
    display: flex; align-items: flex-start; gap: 0; position: relative;
  }
  .beat-num {
    width: 22px; height: 22px; flex: none; border-radius: 50%;
    background: rgba(109,140,255,.18); border: 1.5px solid rgba(109,140,255,.35);
    color: var(--accent, #6d8cff); font-size: 10px; font-weight: 700;
    display: grid; place-items: center; margin-top: 2px; flex-shrink: 0;
  }
  .beat-body {
    padding: 2px 10px 8px 8px;
    display: flex; flex-direction: column; gap: 2px;
    max-width: 180px;
  }
  .beat-inf {
    font-size: 11.5px; font-weight: 700; color: var(--text, #e0e4f0);
    line-height: 1.3;
  }
  .beat-desc {
    font-size: 11px; color: var(--muted, #8a92b0); line-height: 1.4;
    font-style: italic;
  }
  .beat-connector {
    position: absolute; right: 0; top: 11px;
    width: 10px; height: 1px; background: rgba(109,140,255,.25);
  }

  /* Heart */
  .heart-row {
    display: flex; align-items: flex-start; gap: 8px;
    padding: 8px 12px; border-radius: 8px;
    background: rgba(109,140,255,.06); border: 1px solid rgba(109,140,255,.15);
  }
  .heart-icon { font-size: 14px; color: var(--accent, #6d8cff); flex: none; margin-top: 1px; }
  .heart-text { font-size: 12.5px; color: var(--text, #e0e4f0); line-height: 1.5; font-style: italic; }

  /* Compact mode — smaller footprint for overview sidebar */
  .spine.compact { padding: 12px 14px; gap: 10px; }
  .spine.compact .anchor { padding: 8px 10px; }
  .spine.compact .anchor-text { font-size: 12px; }
  .spine.compact .beat-body { max-width: 150px; }
  .spine.compact .beat-desc { display: none; }
</style>

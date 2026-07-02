<script>
  // The ONE header tab bar for every story surface — genesis (new-story) AND the committed editor —
  // so both share the same chrome: Overview · Cast · World · Plot · Web. Callers pass the tab list
  // (a surface can `disabled` a tab that isn't reachable yet, e.g. genesis Plot before a cast exists)
  // and bind `active`. See [[navigation-pattern]] — this is IN-story sub-nav, not the app's top nav.
  let { tabs, active = $bindable() } = $props();
</script>

<div class="tabs">
  {#each tabs as t (t.id)}
    <button class="tab" class:on={active === t.id} disabled={t.disabled}
            onclick={() => (active = t.id)} title={t.disabled ? t.hint || '' : ''}>{t.label}</button>
  {/each}
</div>

<style>
  .tabs { display: flex; gap: 22px; border-bottom: 0.5px solid var(--border); margin: 12px 0 18px; }
  .tab { background: none; border: 0; border-bottom: 2px solid transparent; padding: 9px 2px; margin-bottom: -1px;
    font-size: 14px; font-weight: 600; color: var(--muted); cursor: pointer; transition: color .12s; }
  .tab:hover:not(:disabled) { color: var(--text); }
  .tab.on { color: var(--text); border-bottom-color: var(--accent); }
  .tab:disabled { opacity: .38; cursor: not-allowed; }
</style>

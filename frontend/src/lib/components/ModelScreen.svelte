<script>
  import ConnectionPanel from './ConnectionPanel.svelte';

  // One model's setup: its Model panel (passed as children) and its own Connection.
  let { connKind, children } = $props();
  let sub = $state('model'); // model | connection
</script>

<div class="screen">
  <div class="leftbar">
    <button class:on={sub === 'model'} onclick={() => (sub = 'model')}>Model</button>
    <button class:on={sub === 'connection'} onclick={() => (sub = 'connection')}>Connection</button>
  </div>
  <div class="content">
    {#if sub === 'model'}{@render children()}{:else}<ConnectionPanel kind={connKind} />{/if}
  </div>
</div>

<style>
  .screen { display: grid; grid-template-columns: 160px 1fr; gap: 18px; align-items: stretch; flex: 1; min-height: 0; }
  .leftbar { display: flex; flex-direction: column; gap: 4px; }
  .leftbar button {
    text-align: left; background: none; color: var(--muted); box-shadow: none;
    border-radius: 9px; padding: 9px 12px; font-weight: 560; font-size: 14px;
  }
  .leftbar button:hover { color: var(--text); background: var(--elev); filter: none; }
  .leftbar button.on { color: #fff; background: var(--elev-2); box-shadow: inset 0 0 0 1px var(--border); }
  /* flex column so a panel's system-prompt textarea can stretch to fill height */
  .content { min-width: 0; min-height: 0; display: flex; flex-direction: column; overflow: auto; }
</style>

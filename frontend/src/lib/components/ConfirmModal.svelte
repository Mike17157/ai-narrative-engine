<script>
  import { confirmState, resolveConfirm } from '$lib/confirm.svelte.js';
  const cancel = () => resolveConfirm(false);
  const ok = () => resolveConfirm(true);
</script>

{#if confirmState.open}
  <div class="overlay" onclick={cancel} role="presentation">
    <div class="dlg" role="dialog" aria-modal="true" onclick={(e) => e.stopPropagation()}>
      <h3 class="title">{confirmState.title}</h3>
      {#if confirmState.message}<p class="msg">{confirmState.message}</p>{/if}
      <div class="acts">
        <button class="ghost" onclick={cancel}>{confirmState.cancelLabel}</button>
        <button class:danger={confirmState.danger} onclick={ok}>{confirmState.confirmLabel}</button>
      </div>
    </div>
  </div>
{/if}

<svelte:window onkeydown={(e) => { if (confirmState.open && e.key === 'Escape') cancel(); }} />

<style>
  .overlay { position: fixed; inset: 0; z-index: 80; background: rgba(6, 8, 12, .62);
    display: grid; place-items: center; padding: 24px; backdrop-filter: blur(2px); animation: fade .12s ease; }
  .dlg { width: min(92vw, 420px); background: var(--panel); border: 1px solid var(--border);
    border-radius: var(--radius-lg, 14px); box-shadow: var(--shadow, 0 18px 50px rgba(0,0,0,.55));
    padding: 18px 18px 16px; animation: pop .13s ease; }
  .title { margin: 0 0 8px; font-size: 16px; font-weight: 680; color: var(--text); }
  .msg { margin: 0 0 16px; font-size: 13.5px; line-height: 1.55; color: var(--muted); }
  .acts { display: flex; justify-content: flex-end; gap: 10px; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  .acts .danger { background: var(--bad); color: #fff; box-shadow: none; }
  .acts .danger:hover { filter: brightness(1.08); }
  @keyframes fade { from { opacity: 0; } }
  @keyframes pop { from { opacity: 0; transform: translateY(-8px) scale(.98); } }
</style>

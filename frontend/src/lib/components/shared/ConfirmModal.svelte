<script>
  import Modal from '$lib/components/shared/Modal.svelte';
  import { confirmState, resolveConfirm } from '$lib/confirm.svelte.js';
  const cancel = () => resolveConfirm(false);
  const ok = () => resolveConfirm(true);
</script>

<Modal open={confirmState.open} onClose={cancel} width="420px">
  <h3 class="title">{confirmState.title}</h3>
  {#if confirmState.message}<p class="msg">{confirmState.message}</p>{/if}
  <div class="acts">
    <button class="ghost" onclick={cancel}>{confirmState.cancelLabel}</button>
    <button class:danger={confirmState.danger} onclick={ok}>{confirmState.confirmLabel}</button>
  </div>
</Modal>

<style>
  .title { margin: 0 0 8px; font-size: 16px; font-weight: 680; color: var(--text); }
  .msg { margin: 0 0 16px; font-size: 13.5px; line-height: 1.55; color: var(--muted); }
  .acts { display: flex; justify-content: flex-end; gap: 10px; }
  .acts button { padding: 8px 16px; font-size: 13.5px; border-radius: 9px; }
  .acts .danger { background: var(--bad); color: #fff; box-shadow: none; }
  .acts .danger:hover { filter: brightness(1.08); }
</style>

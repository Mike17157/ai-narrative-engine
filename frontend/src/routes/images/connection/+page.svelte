<script>
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';

  // Connections moved to the canonical Settings home. Redirect there once on mount.
  onMount(() => { goto('/settings/connections'); });
</script>

<div class="moved">
  <p>Connections now live in <a href="/settings/connections">Settings ▸ Connections</a>.</p>
  <p class="muted">Redirecting…</p>
</div>

<style>
  .moved { padding: 40px; text-align: center; color: var(--text); }
  .moved a { color: var(--accent); }
  .muted { color: var(--muted); font-size: 13px; }
</style>

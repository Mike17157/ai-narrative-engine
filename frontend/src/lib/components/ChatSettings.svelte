<script>
  import ModelScreen from './ModelScreen.svelte';
  import TextGenPanel from './TextGenPanel.svelte';
  import ImagePromptPanel from './ImagePromptPanel.svelte';
  import ImageModelPanel from './ImageModelPanel.svelte';

  // The chat window's model setup. Three models, each with its own model picker
  // and its own connection: the chat model, the image-prompt model, the image model.
  let tab = $state('chat'); // chat | imgprompt | image
  const tabs = [
    { id: 'chat', label: 'Chat model' },
    { id: 'imgprompt', label: 'Image prompt model' },
    { id: 'image', label: 'Image model' }
  ];
</script>

<div class="cs">
  <div class="toptabs">
    {#each tabs as t (t.id)}
      <button class:on={tab === t.id} onclick={() => (tab = t.id)}>{t.label}</button>
    {/each}
  </div>
  {#if tab === 'chat'}
    <ModelScreen connKind="text"><TextGenPanel /></ModelScreen>
  {:else if tab === 'imgprompt'}
    <ModelScreen connKind="image_prompt"><ImagePromptPanel /></ModelScreen>
  {:else}
    <ModelScreen connKind="image"><ImageModelPanel /></ModelScreen>
  {/if}
</div>

<style>
  .cs { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .toptabs { flex: none; display: flex; gap: 6px; margin-bottom: 16px; border-bottom: 1px solid var(--border-soft); }
  .toptabs button {
    background: none; box-shadow: none; color: var(--muted); font-weight: 600; font-size: 14px;
    border-radius: 9px 9px 0 0; padding: 8px 16px; border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  .toptabs button:hover { color: var(--text); background: var(--elev); filter: none; }
  .toptabs button.on { color: #fff; border-bottom-color: var(--accent); }
</style>

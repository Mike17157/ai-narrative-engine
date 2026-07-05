// The WORKFLOW modal opener — one situational modal that walks a story's unfinished sections
// conversationally. Any "edit this / fill this" affordance (a card in the rail, a queue chip,
// a missing-section button) calls openWorkflow(storyKey, layer?) to raise the SAME modal,
// optionally focused on one layer. Mounted once (StoryWorkspace); state lives here so every
// trigger shares it. See WorkflowModal.svelte.
export const workflow = $state({ open: false, storyKey: '', startLayer: '' });

export function openWorkflow(storyKey, startLayer = '') {
  workflow.storyKey = storyKey;
  workflow.startLayer = startLayer || '';
  workflow.open = true;
}

export function closeWorkflow() {
  workflow.open = false;
}

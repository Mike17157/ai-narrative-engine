import { redirect } from '@sveltejs/kit';

// Settings is a sectioned shell — /settings itself has no content; land on System
// (environment, ComfyUI, trainer install), the canonical entry point.
export function load() {
  redirect(307, '/settings/system');
}

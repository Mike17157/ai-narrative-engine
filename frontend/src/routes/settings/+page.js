import { redirect } from '@sveltejs/kit';

// Settings is a sectioned shell — /settings itself has no content; land on Models.
export function load() {
  redirect(307, '/settings/models');
}

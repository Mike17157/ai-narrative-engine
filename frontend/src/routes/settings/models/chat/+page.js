import { redirect } from '@sveltejs/kit';

// The chat/image model split was collapsed into one Models page.
export function load() {
  redirect(307, '/settings/models');
}

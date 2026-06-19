import { redirect } from '@sveltejs/kit';

// The chat/image connection split was collapsed into one Connections page.
export function load() {
  redirect(307, '/settings/connections');
}

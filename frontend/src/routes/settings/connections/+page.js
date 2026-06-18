import { redirect } from '@sveltejs/kit';

// Connections is a folder of leaves (chat / image-prompt / image); the index has no content.
export function load() {
  redirect(307, '/settings/connections/chat');
}

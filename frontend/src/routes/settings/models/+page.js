import { redirect } from '@sveltejs/kit';

// Models is a folder of leaves (chat / image-prompt / image); the index has no content.
export function load() {
  redirect(307, '/settings/models/chat');
}

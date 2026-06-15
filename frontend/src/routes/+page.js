import { redirect } from '@sveltejs/kit';

// The app's home is the chat screen.
export function load() {
  redirect(307, '/chat');
}

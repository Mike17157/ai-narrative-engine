import { redirect } from '@sveltejs/kit';

// The app's home is the Story Library (chat is a vestigial legacy surface kept
// reachable from the rail). Landing on /stories means a new user sees their
// library — and the persona gate overlays it — rather than an empty chat.
export function load() {
  redirect(307, '/stories');
}

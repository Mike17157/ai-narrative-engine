import { redirect } from '@sveltejs/kit';

// The app's home is the Story Library. Landing on /stories means a new user
// sees their library — and the persona gate overlays it.
export function load() {
  redirect(307, '/stories');
}

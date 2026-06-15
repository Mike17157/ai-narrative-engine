import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    // SPA: a single index.html fallback, served by FastAPI in production.
    adapter: adapter({ fallback: 'index.html', pages: 'build', assets: 'build' })
  }
};

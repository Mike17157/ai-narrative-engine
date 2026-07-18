import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// `loom story-serve` points the existing Story frontend at the parallel lean
// API. The default preserves the normal full-app development workflow.
const apiOrigin = process.env.LOOM_API_ORIGIN || 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    // In dev, the Svelte app runs here and proxies API calls to FastAPI.
    proxy: { '/api': { target: apiOrigin, changeOrigin: true } }
  }
});

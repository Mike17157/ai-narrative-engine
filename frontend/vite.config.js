import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    // In dev, the Svelte app runs here and proxies API calls to FastAPI.
    proxy: { '/api': 'http://127.0.0.1:8000' }
  }
});

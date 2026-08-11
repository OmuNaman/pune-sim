import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwind from '@tailwindcss/vite'

// The API is `uv run punesim ui --dev` on 8619. Proxying it means the browser
// sees one origin in development too, so nothing needs CORS-specific code paths
// that then differ from production.
export default defineConfig({
  plugins: [react(), tailwind()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8619', changeOrigin: true },
    },
  },
  build: {
    // Served by FastAPI from ui/dist (see api/app.py UI_DIST)
    outDir: 'dist',
    sourcemap: true,
  },
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The console is a separate app. In dev it proxies API calls to the center's
// config-service so you don't fight CORS locally; in prod, point VITE_API_BASE
// at the real center. The dev server runs on 5173.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // anything under /api -> config-service, stripping the /api prefix
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://localhost:8080',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})

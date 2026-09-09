/* Configuration Vite — proxy dev vers la gateway FastAPI (port 8000). */

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Cible du proxy : VITE_API_URL si défini, sinon la gateway locale.
const API_TARGET = process.env.VITE_API_URL || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      // REST de la gateway (POST /projects, /pipeline/run, /edits/scoped, /credits…)
      '/api': { target: API_TARGET, changeOrigin: true },
      // WebSocket temps réel /ws?project_id=X&since_seq=Y — routage en direct
      '/ws': { target: API_TARGET, changeOrigin: true, ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1200, // three.js dépasse la limite par défaut
  },
});

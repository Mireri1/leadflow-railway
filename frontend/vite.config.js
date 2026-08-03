import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://localhost:8000' } },
  build: {
    outDir: 'dist',
    // Vendor chunk = stable hash across app-only deploys → browser keeps the
    // cached react bundle instead of re-downloading everything each release.
    rollupOptions: { output: { manualChunks: { vendor: ['react', 'react-dom'] } } },
  },
})

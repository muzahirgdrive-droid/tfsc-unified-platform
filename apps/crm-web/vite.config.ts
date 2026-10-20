import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

const api = process.env.CRM_API_URL ?? 'http://127.0.0.1:8766';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    dedupe: ['libphonenumber-js'],
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      '@tfsc/refine': fileURLToPath(new URL('../../packages/refine/ts/src/index.ts', import.meta.url)),
    },
  },
  server: { proxy: { '/api': { target: api, changeOrigin: false } } },
  build: {
    target: 'es2022',
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-dom/client'],
          tanstack: ['@tanstack/react-query', '@tanstack/react-router'],
          charts: ['echarts/core', 'echarts/charts', 'echarts/components', 'echarts/renderers'],
          grid: ['@glideapps/glide-data-grid'],
          phone: ['libphonenumber-js'],
        },
      },
    },
  },
  worker: { format: 'es' },
  test: { environment: 'jsdom', include: ['test/**/*.test.ts?(x)'] },
});

/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const config = {
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    watch: {
      ignored: ['**/.pytest_cache/**'],
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    exclude: ['**/node_modules/**', '**/dist/**', '**/.pytest_cache/**'],
    watchExclude: ['**/.pytest_cache/**'],
  },
}

export default defineConfig(config as Parameters<typeof defineConfig>[0])

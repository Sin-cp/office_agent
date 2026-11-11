import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// Browser requests use the same origin; Vite forwards /api to FastAPI.
const proxy = { '/api': 'http://127.0.0.1:8000' }

export default defineConfig({
  plugins: [vue()],
  server: { host: '127.0.0.1', port: 5174, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4174, strictPort: true, proxy },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'element-plus': ['element-plus'],
          'docx-preview': ['docx-preview'],
        },
      },
    },
  },
  test: { setupFiles: ['./tests/setup.ts'] },
})

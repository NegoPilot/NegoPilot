import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 화면은 Spring 공개 API만 부른다. 개발 서버가 /api와 /health를 Spring으로 넘긴다 (supplier-chat 계약).
const backend = process.env.NEGOPILOT_BACKEND_URL ?? 'http://127.0.0.1:8080'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: backend },
      '/health': { target: backend },
    },
  },
})

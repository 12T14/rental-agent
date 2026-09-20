import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    watch: {
      usePolling: process.platform === 'win32',
      interval: 300
    },
    proxy: {
      '/api': 'http://127.0.0.1:8090'
    }
  }
})

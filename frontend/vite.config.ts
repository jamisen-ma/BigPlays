import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API = process.env.BIGPLAYS_API ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/clips': { target: API, changeOrigin: true },
    },
  },
})

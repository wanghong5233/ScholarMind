import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(() => {
  return {
    server: {
      port: 3000,
      host: '127.0.0.1',
      strictPort: false, // 端口被占用时自动尝试下一个
    },
    resolve: {
      alias: [
        {
          find: /^@\//,
          replacement: '/src/',
        },
      ],
    },

    plugins: [react()],
  }
})

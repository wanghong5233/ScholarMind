import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig(() => {
  return {
    server: {
      port: 5173,
      host: '0.0.0.0',
      strictPort: false, // 端口被占用时自动尝试下一个
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    plugins: [
      react(),
      {
        name: 'print-admin-url',
        configureServer(server) {
          server.httpServer?.once('listening', () => {
            const config = server.config
            const port = (server.httpServer?.address() as { port: number })?.port ?? config.server.port ?? 5173
            const rawHost = typeof config.server.host === 'string' ? config.server.host : 'localhost'
            const host = rawHost === '0.0.0.0' ? 'localhost' : rawHost
            const base = config.base?.replace(/\/$/, '') || ''
            const url = `http://${host}:${port}${base}/admin`
            // eslint-disable-next-line no-console
            console.log(`  ➜  Admin:   ${url}`)
          })
        },
      },
    ],
    resolve: {
      alias: [
        {
          find: /^@\//,
          replacement: '/src/',
        },
      ],
    },
  }
})

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const defaultBackendPort = 8000;

export default defineConfig(({ command, isPreview }) => {
  // Share the backend's shell setting only for the local Vite development server.
  const override = command === 'serve' && !isPreview ? process.env.BACKEND_PORT : undefined;
  const port = override === undefined ? defaultBackendPort : Number(override);
  if ((override !== undefined && !/^[0-9]+$/.test(override)) ||
      !Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('BACKEND_PORT must be a decimal port from 1 to 65535.');
  }

  return {
    plugins: [react(), tailwindcss()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: `http://127.0.0.1:${port}`,
          changeOrigin: true,
        },
      },
    },
  };
});

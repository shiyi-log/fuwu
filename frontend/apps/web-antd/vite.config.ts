import { defineConfig } from '@vben/vite-config';

import { loadEnv } from 'vite';

export default defineConfig(async (config) => {
  const env = loadEnv(config?.mode ?? 'development', process.cwd());

  return {
    application: {},
    vite: {
      server: {
        host: '127.0.0.1',
        port: Number(env.VITE_PORT || 5666),
        strictPort: true,
        proxy: {
          '/api': {
            changeOrigin: true,
            // fuwu Django API
            target: env.VITE_FUWU_API_PROXY || 'http://127.0.0.1:8088',
            ws: true,
          },
        },
      },
    },
  };
});

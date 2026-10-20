import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        proxy: {
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            // fuwu Django API
            target: 'http://localhost:8000/api',
            ws: true,
          },
        },
      },
    },
  };
});

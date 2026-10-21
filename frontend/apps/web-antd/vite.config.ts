import { defineConfig } from '@vben/vite-config';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      server: {
        port: 1122,
        proxy: {
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            // fuwu Django API
            target: 'http://localhost:1123/api',
            ws: true,
          },
        },
      },
    },
  };
});

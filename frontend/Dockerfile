FROM node:22.18.0-bookworm-slim AS build

WORKDIR /workspace
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
ENV CI=1
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc turbo.json ./
COPY apps ./apps
COPY packages ./packages
COPY internal ./internal
COPY docs ./docs
COPY playground ./playground
COPY scripts ./scripts
RUN pnpm install --frozen-lockfile
RUN pnpm --filter @vben/web-antd build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/web-antd/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

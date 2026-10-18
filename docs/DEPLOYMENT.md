# fuwu 启动与部署

## 本地启动

服务端的主入口是 Django，兼容的轻量标准库入口保留用于离线 smoke test。使用 Python 3.14 与 uv 启动 Django：

```bash
uv run python manage.py runserver 0.0.0.0:8000
```

默认监听 `http://127.0.0.1:8000`，健康检查为 `GET /healthz`。服务端默认使用本地 SQLite 和内存对象存储，适合单测与开发。需要 S3 兼容对象存储时设置 `FUWU_STORAGE=s3`，并通过环境、IAM role 或 root 拥有且权限为 `0600` 的 `AWS_CREDENTIALS_FILE` 提供写入凭据。前端/updater 只使用匿名 HTTPS 读取对象，不能把 AWS 凭据放入浏览器环境变量或仓库。

若只需验证领域服务而不启动 Django，可运行 `uv run fuwu-server`。

首次使用或切换数据库后检查迁移计划：

```bash
uv run python manage.py makemigrations --check --dry-run
uv run python manage.py migrate
```

发行记录当前由 `fuwu_service.core.SQLiteRepository` 管理自己的 SQLite 表；Django 的内置迁移只负责 Django 基础应用。后续切换 MySQL/Redis adapter 时，必须为对应 adapter 增加独立迁移和回读验收。

## 前端启动

```bash
cd frontend
pnpm install --frozen-lockfile
cp apps/web-antd/.env.example apps/web-antd/.env.local
pnpm --filter @vben/web-antd typecheck
pnpm dev:antd
```

前端 Vben 应用默认监听 `http://127.0.0.1:5666`，开发环境通过 Vite 代理把 `/api` 转发到 `http://127.0.0.1:8000/api`。页面不会显示 AWS secret、session token 或私有 staging 路径；发布请求仅传递服务端签发的受保护 `staging_ref` / job 引用。

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Compose 提供 `api`、`frontend`、MySQL 和 Redis。当前服务端仍以 SQLite/文件 repository 为默认实现，MySQL/Redis 是替换边界和部署依赖；接入生产 repository 前，应完成数据库迁移和连接池配置。`FUWU_STORAGE=memory` 不会创建云资源；真实 S3 验收需要单独提供 bucket、匿名读策略、写入 IAM role 或受保护凭据，并确认 `heyue` / `ap-southeast-1` / `releases` 配置。

## 认证令牌

开发时通过 `FUWU_AUTH_TOKENS` 设置 JSON 映射，例如：

```dotenv
FUWU_AUTH_TOKENS={"local-read":["release.storage.read"],"local-admin":["release.storage.read","release.storage.publish","release.storage.rollback","superadmin"]}
```

生产环境应由独立身份系统签发 bearer token，并在网关或 secret manager 注入；不要把真实 token 提交到 Git、数据库、日志或前端构建产物。

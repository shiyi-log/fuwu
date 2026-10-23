# 启动与部署说明

## 环境要求

- 直接运行后端：Python 3.14 和 uv。
- 容器部署：Docker Engine 与 Docker Compose 插件。
- 从源码运行 Vben 前端：Node.js 22.18.0 和 pnpm 10.33.0。
- 为 SQLite 数据库、受保护 staging 作业和对象存储凭据提供持久化空间。

## 直接运行后端

在仓库根目录安装依赖并准备 staging 目录：

```bash
uv sync --frozen
mkdir -p data/staging
chmod 700 data/staging
export FUWU_DATABASE="$PWD/data/fuwu.sqlite3"
export FUWU_STAGING_DIR="$PWD/data/staging"
```

首次创建超级管理员。命令会交互式读取用户名、显示名称和密码，不设默认账号：

```bash
uv run --frozen python -m fuwu_service.accounts bootstrap --database "$FUWU_DATABASE"
```

仓库根目录的 `fuwu.config.json` 保存首个超级管理员的用户名、显示名称和密码环境变量名称，不保存密码本身。也可以通过配置文件初始化：

```bash
export FUWU_SUPERADMIN_PASSWORD='部署环境中的强密码'
uv run --frozen python -m fuwu_service.accounts bootstrap-config --database "$FUWU_DATABASE"
```

该命令只允许在空账号库中创建一次超级管理员；成员注册、停用和密码重置由登录后的超级管理员在“账号管理”页面执行，接口权限为 `users.manage`。

启动轻量 API 服务：

```bash
FUWU_HOST=127.0.0.1 FUWU_PORT=8088 uv run --frozen fuwu-server
```

也可通过 Django 开发服务器检查适配器：

```bash
uv run --frozen python manage.py check
uv run --frozen python manage.py runserver 127.0.0.1:8088 --noreload
```

同一时间同一端口只启动一个服务入口。直接运行时健康检查地址为 `http://127.0.0.1:8088/healthz`；Compose 容器内部健康检查仍使用 `8000`，宿主端口由 `.env` 中的 `FUWU_PORT` 控制，示例为 `8088`。

## 前端开发

使用仓库锁定的 Node.js 和 pnpm 版本：

```bash
cd frontend
pnpm install --frozen-lockfile
cp apps/web-antd/.env.example apps/web-antd/.env.local
pnpm dev:antd
```

开发服务默认监听 `http://127.0.0.1:5666`，并代理 `/api` 到后端。类型检查和生产构建命令为：

```bash
pnpm --filter @vben/web-antd typecheck
pnpm --filter @vben/web-antd build
```

从 GitHub Release 远端 staging 大型 Docker 归档时，管理页面将 `/api/release-storage/stage-remote` 的浏览器超时默认设为 15 分钟，可在前端构建环境设置 `VITE_FUWU_STAGE_REMOTE_TIMEOUT_MS` 调整，允许范围为 60 秒至 60 分钟。随附 Nginx 配置将 API 代理读取和发送超时设为 20 分钟，部署环境应为更长的浏览器超时保留额外代理时间。服务端下载完成后仍需单独提交 `/api/release-storage/publish`。只有浏览器请求超时（408/504）或无 HTTP 状态的网络连接中断等无法判断请求是否已经完成的情况，页面才会保留原 staging 引用和 manifest 派生的发布字段，提示直接继续发布；不能再次发起同一个 staging 引用，否则会得到 `staging_conflict`。其他 4xx、502、503 以及 GitHub 资产、校验、授权或配置错误都属于明确失败，服务端会清理未完成的 staging，必须修正后重新 staging。切换项目或频道会丢弃当前页面中的待发布引用，避免跨项目提交。

## Docker Compose

```bash
cp .env.example .env
chmod 600 .env
```

修改 `.env` 中的 Django 密钥、数据库示例密码、端口和 S3 目标。不要提交 `.env`。然后启动：

```bash
docker compose --env-file .env up --build -d
docker compose ps
```

首次启动后初始化管理员：

```bash
docker compose exec api uv run --frozen --no-dev python -m fuwu_service.accounts bootstrap --database /data/fuwu.sqlite3
```

默认前端端口为 `3331`，API 宿主端口为 `8088`，容器内部端口为 `8000`，数据存于 Compose 命名卷。API 容器同时挂载受保护的 secrets 目录，只读。对外提供服务时，应使用 HTTPS 反向代理并将管理 API 限制在可信网络内。

### GitHub 账号授权应该在系统设置里完成

系统设置中的全局“GitHub 账号授权”接受 Personal Access Token。服务会通过 GitHub API 校验账号身份；远端 staging 时再按项目登记的仓库读取 Release。令牌只写入 `fuwu.config.json` 指定的 secrets 文件（默认 `secrets/github_accounts.json`）；该文件及父目录必须分别为 `0600` 和 `0700`，并加入部署主机的秘密管理或持久化卷。也可以使用 `FUWU_GITHUB_CREDENTIALS_FILE` 指向容器外挂载的 secrets 文件。令牌不会进入 SQLite、审计日志、API 响应或前端构建产物。

只有拥有 `settings.manage` 的账号可以查看全局授权；提交或撤销令牌还必须使用 HTTPS，本机回环连接仅适合初始化。远端 Release staging 需要 `release.storage.publish`，按请求项目绑定仓库读取公开或私有 Release，并继续校验远端 `SHA256SUMS` 与 manifest。授权接口为 `GET/POST/DELETE /api/settings/github-authorization`；令牌不得放入项目请求、发布请求或日志。

Compose 启动 MySQL 和 Redis 作为可选部署服务；当前发行、账号、设置与审计仓库仍使用 SQLite。Django migration 仅管理 Django 自有模型，不会迁移发行领域数据。接入其他数据库前必须实现对应仓库适配器、迁移、备份与历史数据读回测试。

## S3 兼容存储

通过 `.env` 设置 `FUWU_STORAGE=s3`，并填写 `FUWU_S3_BUCKET`、`FUWU_S3_REGION`、`FUWU_S3_ENDPOINT` 和 `FUWU_S3_PREFIX`。示例环境文件使用占位值，不绑定实际存储目标。

服务端凭据来源可以是系统设置页、环境变量、受保护凭据文件或云平台 IAM role。通过系统设置保存凭据要求 HTTPS 或 localhost 页面及超级管理员权限；进程必须以 root 身份运行，凭据写入 root 所有且权限为 `0600` 的文件，父目录权限为 `0700`。凭据不写入数据库、API 响应、任务状态、日志或前端构建产物。自定义 S3 Endpoint 必须在服务端允许列表中配置，避免服务被用作任意地址代理。

对象上传使用 AWS SigV4。发布时会校验服务端写入权限并执行对象 HEAD 与摘要回读。更新器和下载客户端始终匿名 HTTPS GET，不使用服务端写凭据。配置写入权限与确认匿名下载可用是独立验收项。

## 数据备份与恢复

当前业务数据使用 SQLite。替换镜像前先备份数据库文件和所在目录；需要一致性备份时使用 SQLite backup API，或暂停写入后复制主数据库及 WAL 文件。对象存储发行对象不可变，回滚只改变 `latest.json` 指针，不删除历史发行。

如果发布补偿返回 `pointer_reconciliation_required`，应先核对本地渠道指针、发行审计记录与对象存储的 `latest.json`，完成一致性检查后再恢复发布。

## 验证

```bash
uv run --frozen python manage.py check
uv run --frozen python manage.py makemigrations --check --dry-run
uv run --frozen python manage.py migrate --plan
uv run --frozen --extra dev pytest
```

以上是本地代码检查。真实对象存储访问、生产数据库迁移、容器外部可达性及匿名客户端下载应分别保存验收记录。

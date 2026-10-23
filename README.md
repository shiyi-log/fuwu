# fuwu 通用发行管理服务

fuwu 为多个软件项目提供发行记录、制品校验、稳定版与测试版渠道指针、回滚、权限控制和审计。项目之间按独立仓库标识和授权范围隔离。

## 技术栈

- 后端：Python 3.14、uv、轻量 HTTP 服务及 Django API 适配器。
- 前端：Vben Admin 5.7.0、Vue 3、Ant Design Vue。
- 前端运行环境：Node.js 22.18.0、pnpm 10.33.0。
- 数据仓库：初始实现使用 SQLite；仓库接口可替换。Compose 中的 MySQL、Redis 服务不代表应用已接入这两种数据仓库。
- 制品存储：S3 兼容接口与 AWS SigV4；安装器和更新器通过匿名 HTTPS 读取。

## 本地启动

需要 Python 3.14 和 uv。在仓库根目录运行：

```bash
uv sync --frozen
mkdir -p data/staging
chmod 700 data/staging
export FUWU_DATABASE="$PWD/data/fuwu.sqlite3"
export FUWU_STAGING_DIR="$PWD/data/staging"
uv run --frozen python -m fuwu_service.accounts bootstrap --database "$FUWU_DATABASE"
FUWU_HOST=127.0.0.1 FUWU_PORT=8088 uv run --frozen fuwu-server
```

管理员初始化为交互式流程，只需对新数据库执行一次。仓库根目录的 `fuwu.config.json` 内置首个超级管理员的非敏感配置；如需按该配置初始化，可将密码放在配置指定的环境变量中，再执行：

```bash
export FUWU_SUPERADMIN_PASSWORD='请在部署环境中设置的强密码'
uv run --frozen python -m fuwu_service.accounts bootstrap-config --database "$FUWU_DATABASE"
```

密码不会写入配置文件、数据库、日志或 API 响应；也可以继续使用上面的交互式 `bootstrap` 命令。健康检查地址为 `http://127.0.0.1:8088/healthz`。本地默认使用 SQLite 和内存对象存储。

超级管理员登录后可在“账号管理”中注册成员、配置角色/权限和项目范围、停用账号及重置成员密码；成员可在个人资料中使用当前密码修改自己的密码。密码、角色、权限、项目范围和停用状态变更会撤销受影响账号的现有会话。

前端在另一终端启动：

```bash
cd frontend
pnpm install --frozen-lockfile
cp apps/web-antd/.env.example apps/web-antd/.env.local
pnpm dev:antd
```

开发前端默认监听 `http://127.0.0.1:5666`，并将 `/api` 请求转发至本地后端。

## Docker Compose

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env` 中的部署配置和示例密码，然后启动：

```bash
docker compose --env-file .env up --build -d
docker compose ps
docker compose exec api uv run --frozen --no-dev python -m fuwu_service.accounts bootstrap --database /data/fuwu.sqlite3
```

默认管理界面端口为 `3331`，API 宿主端口为 `8088`（容器内部仍为 `8000`）。首次启动后通过交互命令创建管理员。对外部署应使用 HTTPS 反向代理，并限制 API 端口的网络访问。

Compose 可启动 MySQL 与 Redis 服务，但当前业务数据仍保存在 SQLite。切换数据库前，需要实现对应 repository adapter、迁移和读回验证。

## 存储凭据

系统设置允许超级管理员通过安全连接保存对象存储凭据。凭据保存在服务端 root 所有、权限为 `0600` 的独立文件中；Bucket、区域、Endpoint 和前缀等非敏感配置保存在数据库。API 仅返回配置状态和受限权限用户可见的 Access Key 脱敏提示，不返回 Secret Access Key 或 Session Token。环境变量或云平台 IAM role 也可提供服务端凭据。客户端发行下载始终使用匿名 HTTPS。

系统设置支持全局 GitHub Personal Access Token。服务端只接受安全连接中的令牌，调用 GitHub `/user` 校验账号后，将令牌写入 `fuwu.config.json` 指定的 `0600` secrets 文件（可用 `FUWU_GITHUB_CREDENTIALS_FILE` 覆盖路径）；令牌不写入 SQLite、响应或日志。远端 Release staging 会按项目登记的仓库使用该全局授权读取私有 Release，并校验远端 `SHA256SUMS` 后再进入普通发布流程。

## 文档

- [项目台账](PROJECT_LEDGER.md)
- [API 接口文档](docs/API.md)
- [Heyue 匿名安装与源码归档说明](docs/HEYUE_BUILD_INSTRUCTIONS.md)
- [启动与部署说明](docs/DEPLOYMENT.md)
- [发行一致性说明](docs/PUBLISH_CONSISTENCY.md)
- [版本记录](docs/VERSION_HISTORY.md)

CI 检查 Markdown 结构、Python 与 Django、后端测试，以及 Vben 前端类型检查和生产构建。真实对象存储权限、生产部署和外部客户端验收需分别记录，不能由本地测试或镜像健康检查替代。

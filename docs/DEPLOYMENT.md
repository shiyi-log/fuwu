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

### 服务器本机修改或恢复密码

服务器操作员可在交互终端运行密码恢复脚本，无需旧密码或 SMTP。使用运行中服务的同一账号数据库，并填写要恢复的邮箱或用户名；省略 `--username` 时会交互询问：

```bash
bash scripts/reset_password.sh --database "$FUWU_DATABASE" --username admin
```

脚本会读取项目根目录的 `.env`。本地库已初始化且 `.env` 中已设置 `FUWU_DATABASE` 时，可直接执行 `bash scripts/reset_password.sh --username admin`，无需再传数据库路径。用同一份本地配置启动后端时，在现有启动命令的 `uv run` 后添加 `--env-file .env`，以确保服务和脚本使用同一个数据库。

也可直接运行 Python 命令，容器部署使用容器内的数据库路径：

```bash
uv run --frozen python -m fuwu_service.accounts reset-password --database "$FUWU_DATABASE" --username admin
docker compose exec api uv run --frozen --no-dev python -m fuwu_service.accounts reset-password --database /data/fuwu.sqlite3 --username admin
```

新密码需为 12 至 256 字符，终端会隐藏输入并要求输入两次。数据库优先使用 `--database`，也可由当前终端或 `.env` 中的 `FUWU_DATABASE` 明确指定；脚本拒绝缺失或未初始化的数据库、未知或停用账号、无隐藏输入能力的终端，不接受 `--password` 参数。两次密码不一致或按 Ctrl+C 取消时，原密码保持不变。直接执行 Python 模块时需自行传入 `--env-file .env` 给 `uv run`，或设置环境变量。

恢复在同一事务中更新密码哈希、撤销该账号全部会话与找回验证码、清除该账号的登录失败限流并写入 `host-cli` 安全事件；客户端级登录限流仍按原规则到期。不修改角色、项目范围或启用状态，也不会修改发行数据。服务无需重启，下次登录使用新密码。执行此命令的权限边界是主机数据库写权限，请在服务主机上通过已授权的运维账号执行。

### 邮箱登录与邮件验证码找回密码

账号用户名可填写邮箱地址。用户在登录页选择“忘记密码”后，请求一次性验证码并通过邮件修改密码；找回接口不会说明邮箱是否已登记。验证码 10 分钟过期，每个邮箱 60 秒冷却、每小时最多发送 3 次，最多允许 5 次错误尝试；成功使用后立即作废并撤销该账号的所有现有会话。默认开发密钥下验证码找回会失败，生产必须配置随机 `DJANGO_SECRET_KEY`。

要启用发信，给服务进程配置 SMTP 环境变量。`.env.example` 提供字段模板，Compose 会将以下值传入 API 容器：

```dotenv
EMAIL_HOST=smtp.example.com
EMAIL_PORT=587
EMAIL_HOST_USER=service@example.com
EMAIL_HOST_PASSWORD=由部署环境安全注入
EMAIL_TIMEOUT=10
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=Fuwu <service@example.com>
```

不要将真实 SMTP 密码写入仓库、镜像或聊天。使用 STARTTLS 时设置 `EMAIL_USE_TLS=true`；使用隐式 SSL 时设置 `EMAIL_USE_SSL=true` 且关闭 TLS。修改 `.env` 后重建或重启 API 容器以加载配置。生产必须同时配置强随机、至少 32 字符的 `DJANGO_SECRET_KEY`，它用于验证码摘要保护；Compose 启动脚本会拒绝开发占位密钥或过短值，轮换已配置的密钥会使尚未使用的验证码失效。

找回密码 POST 接口只接受 HTTPS 或 loopback 请求。Compose 默认只将前端和 API 宿主端口绑定到 `127.0.0.1`，由宿主机 TLS 入口转发至前端 Nginx。配置外层 TLS 代理时，先确认 Nginx 实际看到的代理地址，再将精确地址或 CIDR 写入 `FUWU_TRUSTED_PROXY_CIDRS`。Nginx 只对这些来源读取精确的 `http` 或 `https` `X-Forwarded-Proto` 值；未列入的来源、逗号拼接值和其他输入均按内层 `$scheme` 处理。TLS 入口必须覆盖传入头，不能追加用户提供的值。默认 `FUWU_TRUST_X_FORWARDED_PROTO=0` 不信任协议头；只在完成入口 allowlist 与 Docker 网络隔离后显式设为 `1`，让后端识别前端 Nginx 已净化的 HTTPS 头。若 API 可被其他网络客户端直连，应先修复网络隔离。不要将不受信任的容器加入 API 所在网络。

已有管理员账号可由服务器操作员在本机把唯一活跃超级管理员的用户名改为其可访问的邮箱。该命令不要求知道旧管理员密码，但需能登录服务主机，并要求确认当前账号名和目标邮箱：

```bash
docker compose exec api uv run --frozen --no-dev python -m fuwu_service.accounts set-superadmin-email --database /data/fuwu.sqlite3
```

直接运行部署可将 `--database` 改为 `$FUWU_DATABASE`。命令只修改账号名，不重置密码；完成后管理员可在登录页请求验证码并自行设置新密码。新建实例应在首次 `bootstrap` 时直接使用可访问邮箱作为管理员用户名。

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

开发服务默认监听 `http://127.0.0.1:5666`，将 `/api` 请求代理到 `http://127.0.0.1:8088` 并保留原路径。若需要调整端口或后端地址，在 `apps/web-antd/.env.local` 中设置 `VITE_PORT` 和 `VITE_FUWU_API_PROXY`，代理地址只填后端 origin，不附加 `/api`。开发服务仅监听本机回环地址；指定端口被占用时会报错。上述代理配置用于 Vite 开发服务，生产环境仍由反向代理配置 API 路由。

类型检查和生产构建命令为：

```bash
pnpm --filter @vben/web-antd typecheck
pnpm --filter @vben/web-antd build
```

从 GitHub Release 远端 staging 大型 Docker 归档或自动同步最新发行时，管理页面将 `/api/release-storage/stage-remote`、`/api/release-storage/sync-latest` 的浏览器超时默认设为 15 分钟，可在前端构建环境设置 `VITE_FUWU_STAGE_REMOTE_TIMEOUT_MS` 调整，允许范围为 60 秒至 60 分钟。随附 Nginx 配置将 API 代理读取和发送超时设为 20 分钟，部署环境应为更长的浏览器超时保留额外代理时间。手工 `stage-remote` 完成后仍需单独提交 `/api/release-storage/publish`；发行页自动同步则在同一请求中完成 staging、校验与发布。仅手工 staging 请求在浏览器超时（408/504）或无 HTTP 状态的网络中断等无法判断是否完成时，会保留原 staging 引用和 manifest 派生字段，允许继续提交；不能重复同一 staging 引用，否则会得到 `staging_conflict`。自动同步若响应超时，下次检查会重新发现上游版本，并跳过已发布的内容。其他 4xx、502、503 以及 GitHub 资产、校验、授权或配置错误都属于明确失败，服务端会清理未完成的 staging。切换项目或频道会丢弃页面中的待发布引用，避免跨项目提交。

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

默认前端端口为 `3331`，API 宿主端口为 `8088`，容器内部端口为 `8000`，数据存于 Compose 命名卷。默认两个端口只绑定回环地址。API 容器同时挂载受保护的 secrets 目录，只读。对外提供服务时，应使用 HTTPS 反向代理并将管理 API 限制在可信网络内。

前端 Nginx 除 `/api/` 外还会将 `/releases/` 原路径代理给 API，并关闭下载代理缓冲。必须从最终公开 HTTPS 域名检查 `/releases/{key}` 与 `/api/downloads/{project}/{version}/{filename}` 的状态、`Content-Type`、`Content-Length`、`Cache-Control` 和 `X-Fuwu-SHA256`；未知发行路径应返回 404，不能回退到管理页面 HTML。

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

运行 `python3 scripts/check_deployment.py` 可在不发布宿主端口的临时 Docker 网络中检查随附 Nginx 配置、下载路由头部、HEAD/GET 行为以及可信代理 scheme 处理；该脚本使用 mock API 和临时 TLS 证书，不连接生产系统。构建 API 镜像后，加 `--api-image fuwu-api:0.1.0-acceptance` 会再用真实 API 和全新临时 SQLite 验证：HTTP 和伪造协议头被拒绝，可信 HTTPS 链路可以调用找回接口。测试使用未登记邮箱，不发送邮件。

该隔离链路测试不能代替真实入口、SMTP、S3、生产数据库备份或匿名客户端下载验收，后者必须分别保存证据。Docker 构建上下文排除了 `.env`、`secrets/`、`data/`、staging、数据库、备份和生成归档，不应把运行配置打入镜像。

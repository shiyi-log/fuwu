# 项目台账

更新时间：2026-11-11

## 项目目标

建设可供多个软件项目使用的通用发行管理服务，提供项目登记、发行、制品完整性校验、stable/beta 渠道指针、回滚、账号权限和操作审计。项目说明页面读取绑定原项目的 README，避免在 Fuwu 中重复维护安装和构建文案。

## 2026-11-06 本地恢复与启动

- 源码从 GitHub `main` 恢复，基线提交为 `019579ed594d8f89728421a5c2fa8ceeaa1ab6af`，版本为 `0.0.8`。
- 使用 Python `3.14.8`、uv `0.13.1`、Node.js `22.23.3` 和 pnpm `10.33.0`，已按锁文件安装前后端依赖；锁文件未修改。
- 修复 Vben 开发配置中写死的 `1122/1123` 端口：前端默认仅监听 `127.0.0.1:5666`，`/api` 保留路径代理到 `127.0.0.1:8088`；支持 `VITE_PORT`、`VITE_FUWU_API_PROXY`，端口冲突时明确失败。
- 从本机 2026-11-02 的 Fuwu 数据备份中复制 SQLite 到本地 `data/fuwu.sqlite3`，源备份保持不变。完整性检查为 `ok`；备份内账号、项目、发行记录均为零，因此初始化了新的本地 `admin`。随机密码仅保存在被 Git 忽略、权限为 `0600` 的本地私有文件中，未写入文档或提交。
- 2026-11-06 00:44（Asia/Shanghai）读回：前端首页 HTTP `200`；后端 `/healthz` HTTP `200`；经前端代理登录、查询个人资料、项目、系统设置、账号以及登出均为 HTTP `200`。数据库有 1 个本地账号、0 个项目、0 条发行记录；对象存储为 `MemoryObjectStore`。
- 本地检查：Django `check`、`makemigrations --check --dry-run`、Vben `typecheck`、生产 `build` 和 `git diff --check` 通过。
- 服务在本机后台运行；该记录仅证明本地启动与 API 联通，不代表生产部署、真实 S3 读写或发行下载验收，也不配置开机自启。

## 2026-11-10 自动读取与自动发布实测

- 本地使用既有 `5666/8088` 前后端端口；已核对监听进程目录并补登记到本机端口管理器。启动新代码前备份原 SQLite；原数据库无项目、发行或存储配置，本次仍使用 `MemoryObjectStore`。
- 通过前端 `/api` 代理完成真实 HTTP 登录、GitHub 账号及仓库访问校验，登记本地 `heyue` 项目并绑定 `shiyi-log/heyue`。GitHub 读取返回正式版 `v1.0.14`、revision `85cc0de44b1d007f7f9c5e665863578439a7d44b`，GitHub 发布时间为 `2026-11-08T08:03:08Z`。
- 首次 `POST /api/release-storage/sync-latest` 于 18:30:56（Asia/Shanghai）完成，HTTP `201`，耗时 `132.067s`；自动下载、protected staging、SHA256/大小校验、不可变对象写入及 HEAD 校验后更新 stable 指针。重复调用为 HTTP `200 already_published`，发行记录保持 1 条，未重复 staging。
- 匿名 HTTP `200` 读回 `latest.json`、版本化 manifest、`SHA256SUMS` 及两个完整资产。Docker 包 `534877370` 字节，SHA256 `6d126cd4c17206fc0e06f8fd3d8469fb55a96b639b80b0013133c7c49271f551`；Python 包 `2436429` 字节，SHA256 `4c9f8fda9f381bb37c4d049acfced93220c81bc12d98f10e579c1dcb8de88233`；manifest 摘要与 latest 声明一致，校验清单摘要及资产集合与 manifest 一致。
- 浏览器实际登录并进入发行页，自动发起同步；页面展示 `v1.0.14`、“已同步”、`latest`、`verified` 和发布审计。点击“立即检查并同步”直接执行并返回已同步，没有发布确认弹窗。
- 保持页面打开且不再点击，18:35:15（Asia/Shanghai）观察到五分钟定时器自动发起的同步完成，HTTP `200`，耗时 `4.275s`；发行记录及 remote staging 审计仍各 1 条。真实只读账号 GET latest 为 HTTP `200`；POST sync-latest 和越项目读取为 `403`；匿名 POST 为 `401`。临时只读账号已停用并撤销会话。
- 实测中补齐 Django `sync-latest` URL 注册；修复超时后下一次 `already_published` 不刷新清单的问题；GET/POST latest 共用资产名称归一化。后端全量 `139 passed`、96 subtests；Django check、迁移漂移检查、前端 focused Vitest `28 passed`、typecheck、生产 build 通过。
- 本次证据为真实私有 GitHub 读取、真实制品下载及本地内存存储发布；未测试 S3 写入或生产部署。内存对象会随进程退出丢失，SQLite 发行记录会保留。自动检查由打开的发行页驱动，每 5 分钟运行；未添加服务端常驻调度器。本地原始结果存于被 Git 忽略的 `data/run/live-test-20261110.json`，不包含密码或令牌。

## 2026-11-10 Fuwu 0.0.9 正式发行与线上部署

- `main` 提交 `6271b0769b296aa820acf44961f1f885bbd4d119` 的 CI run `42838556744` 成功；精确提交标签 `v0.0.9` 已推送，CD run `42839566021` 成功并创建正式 GitHub Release，包含源码归档及 `SHA256SUMS`。
- 正式源码包 SHA256 校验通过。生产备份位于 `/www/wwwroot/fuwu/.codex-deploy-backup-20261110T152351Z`，目录权限为 `0700`；包含旧源码归档、SQLite 一致性快照、`.env`、Secrets 与 revision。快照 `integrity_check=ok`；部署前后 `projects=2`、`releases=7`、`latest=2`。5.1 GB staging 未更改。
- `admin@3.0.199.9:/www/wwwroot/fuwu` 已部署提交 `6271b0769b296aa820acf44961f1f885bbd4d119`；`uv sync --frozen --no-dev`、Django `check` 通过，迁移漂移检查显示无变化，运行包版本为 `0.0.9`。原有 root `fuwu-server` 进程已重启。
- 生产本机与公网 `/healthz` 均 HTTP `200`；首页已切换到 `index-index-5ZzUQ4Ae.js`；匿名 `POST /api/release-storage/sync-latest` 返回预期 `401`。生产自动同步功能尚未用已认证账号执行写入验收。
- 线上 Heyue stable `latest.json` 仍为 `1.0.7`、revision `a26ceb9e9b0501cac31b93de75323bfb0dff509f`；GitHub 最新正式版为 `1.0.14`。本次升级的是 Fuwu 服务，未推进 Heyue stable 指针。

## 2026-11-11 邮箱验证码找回密码候选

- 本地版本已推进到 `0.1.0`；只形成工作树候选，尚未创建 tag、GitHub Release 或生产部署。
- 登录支持邮箱或原用户名；新增公开的邮件验证码发送和密码重置端点。账号表保留原结构；SQLite 幂等增加验证码、限速和安全事件表。
- 验证码为 6 位数字，有效期 10 分钟；每个邮箱冷却 60 秒、每小时最多发送 3 次、5 次错误后作废。数据库只存 `DJANGO_SECRET_KEY` HMAC 摘要；成功重置会撤销全部会话。服务器本机 `set-superadmin-email` 命令可将现有超级管理员改用邮箱名登录。
- SMTP 通过部署环境配置，找回 API 除 loopback 外要求 HTTPS。可信 TLS 反向代理覆盖 `X-Forwarded-Proto` 后，可显式开启 `FUWU_TRUST_X_FORWARDED_PROTO`。
- 后端全量 `160 passed`（包含本机恢复脚本）；Django check、migration drift check、`uv lock --check`、`uv build`、compileall、Compose config、前端 typecheck/build 和 `git diff --check` 通过。API 邮件验证使用 Django locmem backend；真实 SMTP 投递尚未验证。
- 生产尚未部署 `0.1.0`；现有管理员邮箱与 SMTP 生产配置仍待设置。线上 Heyue stable 当前仍为 `1.0.7`，未执行生产自动同步。

## 2026-11-11 本机密码恢复脚本

- 在 `0.1.0` 工作树候选中新增 `scripts/reset_password.sh`，调用 `python -m fuwu_service.accounts reset-password`；支持邮箱或旧用户名，密码双次隐藏输入，不接收明文密码参数。
- 必须用 `--database` 或 `FUWU_DATABASE` 指定现有账号数据库，以 SQLite `mode=rw` 打开且先验证账号表；缺失/未初始化数据库、未知/停用账号或无法隐藏输入的终端会失败退出。
- 恢复事务只更新目标账号密码哈希、撤销该账号会话与找回验证码、清理账号级登录限流并记录 `host-cli` 审计；账号角色和启用状态不变。输入期间账号身份变化时拒绝提交。
- 专项 `12 passed`、6 subtests；全量 `160 passed`、106 subtests；Django check、migration drift check、Shell 语法与 `git diff --check` 通过。使用临时数据库和随机合成密码运行真实 PTY 流程，确认双次输入不回显、新密码可登录、旧密码/会话/验证码失效，审计写入且无关数据保持一致。未执行本地真实账号或生产账号的密码重置。

## 2026-11-11 本地数据库补齐与路径固定

- 核对根目录 `fuwu.sqlite3` 为空库，实际本地数据库为 `data/fuwu.sqlite3`。先通过 SQLite backup API 创建 `data/backups/before-local-init-20261111T142402Z.sqlite3` 一致性快照（备份目录 `0700`，文件 `0600`），再运行现有 repository、账号和管理存储的幂等初始化。
- 实际数据库增加 `account_password_reset_codes`、`account_password_reset_limits`、`account_security_events` 三张表。初始化前后所有既有表的行逐项一致，账号及密码哈希未修改；保留 2 个账号（1 个启用管理员、1 个停用测试账号）、1 个项目、1 条发行、1 条 latest，`integrity_check=ok`。
- 创建被 Git 忽略、权限 `0600` 的本地 `.env`，固定实际数据库和 staging 的绝对路径，并生成本地独立随机 Django 密钥；未配置 SMTP。密码恢复脚本读取根目录 `.env`，命令行 `--database` 仍优先。服务启动需使用 `uv run --env-file .env ...` 读取同一配置。
- 本轮未启动服务、未重置管理员密码、未修改生产环境。

## 2026-11-11 线上管理员密码恢复与登录验收

- 经当前可用 SSH 身份核对 `admin@3.0.199.9`：生产服务仍运行 `0.0.9 / 6271b0769b296aa820acf44961f1f885bbd4d119`，实际数据库为 `/www/wwwroot/fuwu/fuwu.sqlite3`。部署账号模块 SHA256 与发布提交一致；唯一启用的超级管理员为 `admin`。
- 23:01（Asia/Shanghai）通过 SQLite backup API 创建 `.codex-account-recovery-20261111T150119Z/fuwu.sqlite3` 一致性备份，目录 `0700`、文件 `0600`。新密码通过加密 SSH 标准输入传入，调用已部署版本的 PBKDF2 哈希方法；在同一事务内更新密码、撤销 32 个旧会话、清理目标账号登录限流，并向既有 `operation_logs` 写入 `host_password_reset / host-cli` 审计。没有升级服务源码或增加数据库表。
- 恢复后 `integrity_check=ok`；账号角色和启用状态保持一致，原有 2 个项目、7 条发行、2 条 latest、27 项制品逐行核对保持一致。恢复 request ID 为 `host-recovery-9dc1e1d63efeaedee8fa4277`，不含凭据。
- 通过公网 HTTPS 使用新密码登录、读取个人资料、项目、账号、系统设置及健康检查均 HTTP `200`；身份为 `admin`、角色含 `superadmin/super`，运行版本读回 `0.0.9`。API 测试会话已登出。
- 浏览器已实际登录 `https://fuwu.1213.cc` 并进入项目列表，显示超级管理员、Bark 和 Heyue 两个项目；浏览器会话为用户保留。新密码独立保存在本机 Git 忽略的私有文件，权限 `0600`，与本地开发账号分开。

## 当前基线

| 项目 | 当前记录 |
| --- | --- |
| 源码版本 | Fuwu `0.0.9` 已发布并部署；`0.0.8` 保留为上一生产基线 |
| 版本格式 | 三段数字，每段一位；`0.0.9` 后进位为 `0.1.0` |
| 后端 | Python 3.14、uv、轻量 HTTP 服务、Django 适配器 |
| 前端 | Vben Admin 5.7.0、Vue 3、Ant Design Vue |
| 前端工具 | Node.js 22.18.0、pnpm 10.33.0 |
| 业务数据 | 当前使用 SQLite；MySQL/Redis repository adapter 尚未实现 |
| 对象存储 | 可替换接口；S3 兼容 provider 使用 AWS SigV4 |
| 发布基线 | Fuwu `v0.0.9` / `6271b07` 已正式发布并部署；线上源码 revision 与版本标记一致 |
| 本机部署证据 | `0.0.4` 的 API、前端镜像已无缓存重建；Docker 配置校验通过；完整运行态仍需单独记录 |
| 云端部署证据 | `admin@3.0.199.9` 的 `/www/wwwroot/fuwu` 已运行 `6271b07`，`127.0.0.1:8088` 与公网 `/healthz` 均为 200；生产首页引用 `v0.0.9` bundle |
| 远端引用 | `v0.0.3` 至 `v0.0.9` 正式标签/发行已建立；`v0.0.9` CI、CD 与生产部署均通过 |

## 里程碑

| 编号 | 目标 | 状态 |
| --- | --- | --- |
| M0 | Vben 基础、项目/API 文档和 CI 建立 | 已完成 |
| M1 | Django 适配、SQLite repository、账号与项目权限 | 已完成；以当前测试结果复核 |
| M2 | SigV4 对象存储、受保护 staging 发布、回滚和审计 | 本地实现完成；真实云存储需单独验收 |
| M3 | 项目导航、发行管理、日志、系统设置与原项目说明读取 | 本地实现完成；浏览器验收需单独记录 |
| M4 | Docker Compose 构建及健康检查 | 本机 `0.0.3` 发布基线镜像无缓存重建；API、前端和 MySQL 健康检查通过，Redis 运行中但未配置健康检查；账号登录也已本地验证 |
| M5 | 清理公开仓库全部可达历史和发行元数据 | `main` 已对齐；`v0.0.3` 标签和 GitHub Release 已创建，云端部署仍需单独验收 |
| M6 | GitHub 授权、远端 Release staging、根目录配置和成员管理 | 远端 staging 与成员管理本地实现完成；当前候选改为系统设置全局 PAT，接口和文档正在复核；真实 PAT、对象存储、云端部署和客户端验收仍需单独记录 |
| M7 | 前端安全性改进：统一会话过期处理和重定向安全防护 | 开发中；`0.0.5` 版本 |

## API 文档登记

| 接口组 | 方法与路径 | 身份/权限 | 请求与响应 | 错误行为 | 实现或验证依据 |
| --- | --- | --- | --- | --- | --- |
| 健康检查 | `GET /healthz` | 公开 | 返回服务状态 | 固定健康响应 | `fuwu_service/api.py`、`release_api/urls.py` |
| 账号 | `/api/auth/*`、`/api/user/info`、`/api/roles`、`/api/users*` | 会话；账号管理需 `users.manage` | 登录、登出、账号、角色和密码操作 | 固定认证与权限错误 | `fuwu_service/accounts.py`、`fuwu_service/api.py` |
| 邮件验证码找回 | `POST /api/auth/password-reset/code`、`POST /api/auth/password-reset` | 公开；HTTPS 或 loopback | 向账号邮箱发送 10 分钟验证码，验证后重置密码并撤销会话 | 对未知邮箱返回相同响应；验证码只保存 HMAC 摘要并限时、限次、限发 | `fuwu_service/accounts.py`、`fuwu_service/auth.py`、`release_api/urls.py`、`tests/test_accounts.py`、`tests/test_auth.py` |
| 项目 | `/api/projects*`、`GET /api/projects/{id}/documentation` | 已登录查询；写操作需 `projects.manage` 和项目范围 | 通用仓库元数据；项目说明接口按绑定仓库读取原始 README | 非法项目输入、未授权 GitHub 或上游失败均使用固定错误；不回显令牌和上游正文 | `fuwu_service/projects.py`、`fuwu_service/github_auth.py`、`fuwu_service/api.py`、相关测试 |
| 发行 | `/api/releases` | `release.storage.read` 和项目范围 | 按项目/渠道返回版本、摘要、制品与 HEAD 状态 | 固定查询与授权错误 | `fuwu_service/core.py`、`tests/test_core.py` |
| 存储任务 | `/api/release-storage/status`、`/publish`、`/rollback`、`/audit` | 分别为 `read`、`publish`、`rollback` 权限；回滚需超级管理员确认 | staging 引用、不可变发行、latest 指针和审计 | 请求冲突、存储校验失败及恢复待核对均使用固定错误 | `fuwu_service/core.py`、`tests/test_publish_consistency.py` |
| 操作日志 | `GET /api/logs` | `logs.read` 和项目范围 | 结构化操作记录 | 固定查询错误；不记录请求正文或令牌 | `fuwu_service/administration.py`、`tests/test_management_api.py` |
| 系统设置 | `GET/PATCH /api/settings`、`PATCH /api/settings/storage` | 登录查询；更改需 `settings.manage`；凭据配置还需超级管理员和安全连接 | 通用偏好、非敏感存储目标及服务端凭据配置 | 凭据未进入响应、数据库或日志 | `fuwu_service/storage_settings.py`、`release_api/urls.py`、`tests/test_storage_settings.py` |
| GitHub 全局授权 | `/api/settings/github-authorization` | `settings.manage`；写操作需安全连接 | 校验 GitHub 账号；只返回安全身份元数据；远端 staging 按项目登记仓库使用该授权 | PAT 不进入数据库、日志或 API 响应；撤销全局授权并清除受保护凭据记录 | `fuwu_service/github_auth.py`、`fuwu_service/api.py`、`tests/test_github_authorization_api.py` |
| 远端发行发现与自动同步 | `GET /api/release-storage/latest`、`POST /api/release-storage/sync-latest`、`POST /api/release-storage/stage-remote` | latest 查询用 `release.storage.read`；同步和 staging 用 `release.storage.publish`；均受项目范围限制 | 发行页自动读取 GitHub 正式版；发布权限用户自动下载到受保护 staging、校验并发布，跳过已导入版本和降级；`stage-remote` 仍可单独使用 | 同版本不同内容拒绝覆盖；只允许 GitHub HTTPS 域名；不返回下载 URL 或凭据 | `fuwu_service/core.py`、`fuwu_service/api.py`、`frontend/apps/web-antd/src/views/release-management/index.vue`、相关测试 |
| 更新器读取 | 对象存储 HTTPS `GET .../latest.json`；服务代理 `GET /api/downloads/{project}/{channel}/latest.json` | 匿名只读 | stable latest 指向不可变 v1 manifest；服务代理校验数据库、manifest 和指针摘要 | 客户端验证 SHA256 后暂存；latest 不缓存 | `fuwu_service/core.py`、`fuwu_service/server.py`、`release_api/views.py`、`tests/test_public_latest.py` |
| Fuwu 对外下载 | `GET /api/downloads/{project}/{version}/{filename}` | 公开；仅已发布 stable 制品 | 由数据库发行记录解析 manifest、`SHA256SUMS` 和资产，再用服务端凭据流式读取；兼容 `vX.Y.Z` 版本段并归一化为数字版本 | 未发布版本、未知资产和路径遍历固定返回 404；不暴露对象存储凭据 | `fuwu_service/core.py`、`fuwu_service/server.py`、`release_api/views.py`、`tests/test_public_latest.py`、`tests/test_public_download.py` |
| Heyue 匿名 Bootstrap | `GET`、`HEAD /api/downloads/heyue/bootstrap/install.sh` | 公开；固定 Fuwu 引导资源 | 返回带 `X-Fuwu-SHA256` 的固定 shell 引导；引导在本地校验 stable latest、manifest、SHA256SUMS 和 Python 归档后运行已校验安装器 | 只允许 GET/HEAD；资源不可用为 503；不接受 GitHub、对象键或凭据 | `fuwu_service/bootstrap.py`、`fuwu_service/heyue_bootstrap.sh`、`fuwu_service/server.py`、`release_api/views.py`、`tests/test_public_latest.py` |
| Heyue Python Bootstrap | `GET`、`HEAD /api/downloads/heyue/bootstrap.py` | 公开；固定 Python 包装引导 | 先校验 shell 引导响应摘要，再进入完整 stable 发行链 | 只允许 GET/HEAD；资源不可用为 503；不接受 GitHub、对象键或凭据 | `fuwu_service/heyue_bootstrap.py`、`fuwu_service/bootstrap.py`、`fuwu_service/server.py`、`release_api/views.py`、`tests/test_public_latest.py` |

详细字段、请求样例、权限、错误码与行为见 [API 接口文档](docs/API.md)。尚未生成独立 OpenAPI schema。

## 版本与发布记录

| 日期 | 变更 | 验证状态 |
| --- | --- | --- |
| 2026-10-23 | 增加匿名 stable `latest.json` 服务代理，严格绑定已发布数据库记录与不可变 manifest；补充 Django 与轻量 HTTP 路由 | 本地 `tests/test_public_latest.py`、`tests/test_public_download.py`、`tests/test_api.py` 共 8 passed；线上部署待从 GitHub 拉取新 revision 后复核 |
| 2026-10-23 | 修复 GitHub Release 远端 staging 的长耗时请求：前端单独配置 15 分钟超时、代理模板 20 分钟超时；仅 408/504 或无 HTTP 状态的网络中断保留 staging 引用继续发布；项目或频道切换会丢弃旧上下文；轻量服务吞掉已断开的响应管道错误；待确认的 staging 禁止重复下载 | 后端 `115 passed, 88 subtests passed`；前端 Vitest `32 passed`、typecheck、生产构建、Django check、migration check、compileall、`git diff --check` 均通过；未将此本地候选直接当作线上部署证据 |
| 2026-10-24 | 增加按项目登记仓库自动发现最新正式 GitHub Release，并支持 `latest:true` 服务端下载到受保护 staging；前端提供一键发现、下载和预填发布表单，仍需显式 publish | 后端全量 `120 passed`、88 项子测试；前端 latest focused Vitest、typecheck、生产构建和 `git diff --check` 通过；远端部署与 Bark 线上 staging 待本轮单独验收 |
| 2026-11-10 | 发行页自动读取 GitHub 最新正式版；发布权限用户自动 staging、校验并发布；只读 latest 查询改用 `release.storage.read`；服务端阻止降级和同版本不同内容覆盖 | 后端 `139 passed`、96 subtests；Django check、迁移漂移检查、前端 focused Vitest `28 passed`、typecheck、生产构建通过；真实 Heyue `v1.0.14` 下载、内存存储自动发布、重复跳过、匿名下载摘要和浏览器验收通过；S3 与生产部署未验证 |
| 2026-10-24 | 兼容 Heyue 对象键映射到匿名 API 时的 `vX.Y.Z` 版本路径；只归一化单个前缀 `v`，仍由核心版本校验和已发布资产白名单决定 | 本地公开下载、Django、轻量 HTTP 路由 `9 passed`；远端 `4354f28` 重启后 `v1.0.4` manifest/SHA256SUMS HTTP 200；8000 未监听 |
| 2026-10-24 | 发布 Fuwu `v0.0.7` 并修复 Heyue Bootstrap 的 stable/manifest 身份绑定与固定安装 argv；远端运行文件同步到 `4844ae4` | 本地 `127 passed`、92 subtests、Django check、`uv lock --check`、`uv build`、Shell/Python Bootstrap 语法和真实远端 `--help` 演练通过；远端 `127.0.0.1:8088` 健康、8000 未监听；Heyue stable 保持 `v1.0.4` |
| 2026-10-24 | 修复 Heyue Shell Bootstrap 的 TTY 继承：不再用 `python3 - "$@" <<'PY'` 让 Python 源码占用标准输入，而是写入受保护临时文件后启动，保证交互安装器继承真实终端 | 本地 `133 passed`、94 subtests、Shell/Python 编译、`uv lock --check`、`uv build`、PTY 回归和 `git diff --check` 通过；待创建 `v0.0.8` 标签、GitHub Release、部署并做远端安装复验 |
| 2026-10-25 | `v0.0.8` 已完成 GitHub Release、生产部署和线上 Bootstrap 读回；Fuwu 生产 revision `468972d`，线上 `install.sh` SHA256 为 `3df052860ee2c98fe1fa8d3595d4bd124f15128154c4f3b81e76c97c580a2a5d` | 生产 `healthz` HTTP 200、8088 监听、8000 无监听，线上 Shell `bash -n` 通过；Heyue 测试服务器的修复后完整安装和初始化仍需独立复验 |
| 2026-10-24 | 将 Heyue 构建说明明确为固定 Bootstrap 安装连接；同一连接每次读取 stable/latest 并自动跟随最新稳定版，版本化地址保留用于锁定版本和审计 | 文档与现有 `/api/downloads/heyue/bootstrap.py`、`bootstrap/install.sh` 契约一致；本轮仅更新说明，未改变下载或发布逻辑 |
| 2026-10-24 | 项目说明页改为读取绑定原项目的 README；Fuwu 不再复制维护 Heyue 安装说明，保留原有项目字段以兼容既有记录 | 后端全量 `132 passed`、`94` 项子测试，Django check、前端 typecheck、生产构建和 `git diff --check` 通过；真实私有仓库读取、浏览器验收和线上部署仍未完成 |

当前包版本来自 `pyproject.toml`；版本摘要、兼容性、迁移和验证状态见[版本记录](docs/VERSION_HISTORY.md)。标签或镜像存在不等于 CI、真实对象存储或外部客户端验收通过。

## 设计决定

- 每个项目固定绑定一个代码仓库，项目范围、发行记录和渠道指针相互隔离。
- 发布只接受受保护 staging/job 引用；上传前校验 manifest、版本、revision、大小和 SHA256。
- 不可变对象上传后逐个 HEAD 核对；全部通过后才更新 `latest.json`。
- 同一项目和版本出现不同发行摘要时拒绝覆盖；回滚只移动指针，不删除历史对象。
- AWS 密钥仅从受保护服务端来源读取。系统设置保存的凭据使用 root 所有、`0600` 文件；前端只显示受限权限下的脱敏 Access Key 提示，不回显 secret/token。
- SQLite/S3 之间没有分布式事务；多实例部署前需实现共享事务仓库、跨实例锁和持久化恢复任务。
- 对象存储 provider 凭据只用于服务端写操作；安装器和更新器始终匿名 HTTPS 读取。

## 风险与依赖

- 当前业务数据仍使用 SQLite。Compose 中启动 MySQL/Redis 不表示应用数据已迁移到对应服务。
- 本地单测与 Docker 健康检查不能替代真实 AWS 读写、匿名客户端下载或生产迁移验收。
- Git 历史重写不能清除外部 fork、缓存、既有克隆和第三方保存的发行资产副本。
- 公开历史清理需要逐一复核所有分支、标签、PR 正文、Release 说明和资产；标签触发的发布工作流必须避免意外重复发布。

## 验证证据

| 日期 | 范围 | 结果 | 限制 |
| --- | --- | --- | --- |
| 2026-10-23 | Heyue v1.0.2 远端 Fuwu 发布与公开下载 | 远端 Fuwu 从绑定 Release 下载并校验 5 个资产，发布 HTTP 201；稳定发行记录、manifest、`SHA256SUMS` 和不可变对象已写入 S3；线上服务 revision `d64a89b` 在 `127.0.0.1:8088` 提供匿名 `/api/downloads/heyue/1.0.2/...`；外部 HTTPS 对 Docker、前端、安装器、Python 和源码归档均返回 HTTP 200，大小与 SHA256 全部匹配；项目构建说明已改为 Fuwu 地址且不含 GitHub 下载 URL | S3 bucket 原生匿名 GET 仍返回 403，因此公开下载由 Fuwu 代理流式提供；未将管理员账号、PAT、AWS 密钥或其他凭据写入记录；Heyue 客户端实际安装/升级仍需独立验收 |
| 2026-10-24 | Fuwu `v0.0.7` 远端部署与 Bootstrap 验收 | 远端运行文件 SHA256 与提交 `4844ae4` 一致；进程恢复在 `127.0.0.1:8088`，`/healthz` HTTP 200，`8000` 无监听；匿名 Bootstrap `--help` 真实执行返回 0；近期日志错误模式 0，敏感凭据模式 0 | Heyue `v1.0.5` 尚未冻结；stable/latest 保持 Heyue `v1.0.4` |
| 2026-10-21 | 0.0.3 正式发布本机验证 | 发布提交 `566603e` 的功能代码沿用已验证基线；82 项 pytest、Django check、迁移漂移检查、Python 编译和 `git diff --check` 通过；无缓存重建 `fuwu-api:0.0.3` 与 `fuwu-frontend:0.0.3`，前端使用 Node.js 22.18.0 / pnpm 10.33.0 构建；API、前端、MySQL 健康检查为 healthy，Redis 运行中但未配置健康检查；API `/healthz`、登录、用户信息、登出和前端首页访问通过，未认证 `/api/` 代理按预期返回 401 | 本机 SQLite 初始化账号仅用于验收并在结束时清除；MySQL/Redis 未接入业务仓库；云端主机状态未检查；真实对象存储和外网 HTTPS 未验证 |
| 2026-10-21 | 0.0.4 实现与发布验收 | `HEAD cfc2de2` 精确提交；`uv run --frozen pytest -q` 为 102 passed、80 subtests passed；Django check、迁移漂移检查、Python compileall、`git diff --check`、前端 typecheck/build、`docker-compose config` 和 API/frontend 无缓存镜像构建均通过；CI run `39505736490`、CD run `39505984483` 成功；GitHub `v0.0.4` Release 含源码归档与 `SHA256SUMS`，归档校验通过；远端 staging 的 checksum、manifest、清理、授权 header 与普通 publish 消费由 mock GitHub 测试覆盖 | 未使用真实 GitHub PAT 验证私有 Release；未执行真实对象存储写入、云端部署或生产客户端验收；本机未使用仓库声明的 Node.js 22.18.0 / pnpm 10.33.0 进行交互式前端构建，但 Docker 构建使用声明版本 |
| 2026-10-21 | GitHub `v0.0.3` 发布工作流 | CD run `39484401604` 成功：精确标签校验、成功 CI 复用、源码归档、`SHA256SUMS` 校验和复核及 GitHub Release 创建全部通过 | 仅证明 GitHub Release 与源代码包；不代表云端部署或真实对象存储验收 |
| 2026-10-20 | Docker 运行状态 | `fuwu-api:0.0.3` 与 `fuwu-frontend:0.0.3` 容器 healthy；API `/healthz` 与前端首页 HTTP 检查成功 | 当时为既有镜像，不包含后续源码改动；不代表生产或对象存储验收 |
| 2026-10-20 | 历史清理候选审计 | 已发现历史路径和消息仍有规则命中，候选尚未满足推送条件 | 远端 refs 未改写 |
| 已完成 | GitHub Actions | 发布基线的文档、Python 3.14/Django/测试和 Vben 前端 typecheck/build 全部通过 | 仅证明该提交的仓库 CI |
| 待补 | 真实对象存储与云端部署 | 分别记录准确请求、主机和服务证据 | 不能由本地检查或仓库 CI 推断 |

## 下一步

1. 修复和验证系统设置存储路由、脱敏信息权限及前端展示。
2. 完成中文文档与 CI 文档检查，执行本地后端测试、Django 与迁移检查。
3. 在保留 `0.0.3` Docker 内容的清理候选中清理路径和历史元数据，并逐 ref 扫描。
4. 复核 `v0.0.3` Release 资产并完成云端部署和真实对象存储的独立验收。
5. 在受控环境中使用真实项目 GitHub 授权完成一次私有 Release 下载验收，并记录对象存储、云端部署和客户端真实下载证据。

## 变更日志

| 日期 | 变更 |
| --- | --- |
| 2026-10-23 | `0.0.6` 候选：GitHub PAT 授权统一到系统设置全局接口；畸形或旧项目级凭据文件 fail closed；公开 API/部署文档同步更新；本地测试与 Django 检查结果单独记录 |
| 2026-10-22 | 开发 `0.0.5`：前端安全性增强，统一会话过期处理，引入重定向路径验证防止开放重定向攻击 |
| 2026-10-21 | 完成并发布 `0.0.4`：加入 GitHub 项目授权、远端 Release staging、根目录配置和超级管理员成员管理；CI/CD、Release 资产和归档校验通过 |
| 2026-10-21 | 完成 `v0.0.3` 正式发布：标签固定 `566603e`，CD run `39484401604` 创建 GitHub Release；云端主机状态未检查 |
| 2026-10-20 | 补充中文公开台账、准确 API 契约、凭据安全边界和验证记录；历史清理仍待远端审计 |

# 项目台账

更新时间：2026-10-23

## 项目目标

建设可供多个软件项目使用的通用发行管理服务，提供项目登记、发行、制品完整性校验、stable/beta 渠道指针、回滚、账号权限和操作审计。公开文档只定义通用接口与构建方式，不登记部署专用项目资料。

## 当前基线

| 项目 | 当前记录 |
| --- | --- |
| 源码版本 | `0.0.6` 本地候选；当前 `main` 提交 `2a1dcac`；`v0.0.4` 为上一版已记录发布 |
| 版本格式 | 三段数字，每段一位；`0.0.9` 后进位为 `0.1.0` |
| 后端 | Python 3.14、uv、轻量 HTTP 服务、Django 适配器 |
| 前端 | Vben Admin 5.7.0、Vue 3、Ant Design Vue |
| 前端工具 | Node.js 22.18.0、pnpm 10.33.0 |
| 业务数据 | 当前使用 SQLite；MySQL/Redis repository adapter 尚未实现 |
| 对象存储 | 可替换接口；S3 兼容 provider 使用 AWS SigV4 |
| 发布基线 | 当前本地 `main` 提交 `2a1dcac`；`0.0.6` 尚无本台账记录的正式 GitHub Release |
| 本机部署证据 | `0.0.4` 的 API、前端镜像已无缓存重建；Docker 配置校验通过；完整运行态仍需单独记录 |
| 云端部署证据 | 本次仅核对 GitHub 仓库与本机 Compose；云端主机和外部服务状态未检查 |
| 远端引用 | `v0.0.3`、`v0.0.4` 标签和 GitHub Release 均已创建；云端部署仍需单独验收 |

## 里程碑

| 编号 | 目标 | 状态 |
| --- | --- | --- |
| M0 | Vben 基础、项目/API 文档和 CI 建立 | 已完成 |
| M1 | Django 适配、SQLite repository、账号与项目权限 | 已完成；以当前测试结果复核 |
| M2 | SigV4 对象存储、受保护 staging 发布、回滚和审计 | 本地实现完成；真实云存储需单独验收 |
| M3 | 项目导航、发行管理、日志、系统设置与构建说明 | 本地实现完成；浏览器验收需单独记录 |
| M4 | Docker Compose 构建及健康检查 | 本机 `0.0.3` 发布基线镜像无缓存重建；API、前端和 MySQL 健康检查通过，Redis 运行中但未配置健康检查；账号登录也已本地验证 |
| M5 | 清理公开仓库全部可达历史和发行元数据 | `main` 已对齐；`v0.0.3` 标签和 GitHub Release 已创建，云端部署仍需单独验收 |
| M6 | GitHub 授权、远端 Release staging、根目录配置和成员管理 | 远端 staging 与成员管理本地实现完成；当前候选改为系统设置全局 PAT，接口和文档正在复核；真实 PAT、对象存储、云端部署和客户端验收仍需单独记录 |
| M7 | 前端安全性改进：统一会话过期处理和重定向安全防护 | 开发中；`0.0.5` 版本 |

## API 文档登记

| 接口组 | 方法与路径 | 身份/权限 | 请求与响应 | 错误行为 | 实现或验证依据 |
| --- | --- | --- | --- | --- | --- |
| 健康检查 | `GET /healthz` | 公开 | 返回服务状态 | 固定健康响应 | `fuwu_service/api.py`、`release_api/urls.py` |
| 账号 | `/api/auth/*`、`/api/user/info`、`/api/roles`、`/api/users*` | 会话；账号管理需 `users.manage` | 登录、登出、账号、角色和密码操作 | 固定认证与权限错误 | `fuwu_service/accounts.py`、`fuwu_service/api.py` |
| 项目 | `/api/projects*` | 已登录查询；写操作需 `projects.manage` 和项目范围 | 通用仓库元数据、公共接口说明、构建说明 | 非法项目输入不回显字段 | `fuwu_service/projects.py`、`fuwu_service/core.py` |
| 发行 | `/api/releases` | `release.storage.read` 和项目范围 | 按项目/渠道返回版本、摘要、制品与 HEAD 状态 | 固定查询与授权错误 | `fuwu_service/core.py`、`tests/test_core.py` |
| 存储任务 | `/api/release-storage/status`、`/publish`、`/rollback`、`/audit` | 分别为 `read`、`publish`、`rollback` 权限；回滚需超级管理员确认 | staging 引用、不可变发行、latest 指针和审计 | 请求冲突、存储校验失败及恢复待核对均使用固定错误 | `fuwu_service/core.py`、`tests/test_publish_consistency.py` |
| 操作日志 | `GET /api/logs` | `logs.read` 和项目范围 | 结构化操作记录 | 固定查询错误；不记录请求正文或令牌 | `fuwu_service/administration.py`、`tests/test_management_api.py` |
| 系统设置 | `GET/PATCH /api/settings`、`PATCH /api/settings/storage` | 登录查询；更改需 `settings.manage`；凭据配置还需超级管理员和安全连接 | 通用偏好、非敏感存储目标及服务端凭据配置 | 凭据未进入响应、数据库或日志 | `fuwu_service/storage_settings.py`、`release_api/urls.py`、`tests/test_storage_settings.py` |
| GitHub 全局授权 | `/api/settings/github-authorization` | `settings.manage`；写操作需安全连接 | 校验 GitHub 账号；只返回安全身份元数据；远端 staging 按项目登记仓库使用该授权 | PAT 不进入数据库、日志或 API 响应；撤销全局授权并清除受保护凭据记录 | `fuwu_service/github_auth.py`、`fuwu_service/api.py`、`tests/test_github_authorization_api.py` |
| 远端发行 staging | `POST /api/release-storage/stage-remote` | `release.storage.publish`；项目下载使用项目授权 | GitHub Release 资产、SHA256SUMS 与 manifest 校验后生成受保护 staging | 只允许 GitHub HTTPS 域名；失败清理临时目录；不更新 latest | `fuwu_service/core.py`、`tests/test_remote_stage.py` |
| 更新器读取 | 对象存储 HTTPS `GET .../latest.json`；服务代理 `GET /api/downloads/{project}/{channel}/latest.json` | 匿名只读 | stable latest 指向不可变 v1 manifest；服务代理校验数据库、manifest 和指针摘要 | 客户端验证 SHA256 后暂存；latest 不缓存 | `fuwu_service/core.py`、`fuwu_service/server.py`、`release_api/views.py`、`tests/test_public_latest.py` |
| Fuwu 对外下载 | `GET /api/downloads/{project}/{version}/{filename}` | 公开；仅已发布 stable 制品 | 由数据库发行记录解析 manifest、`SHA256SUMS` 和资产，再用服务端凭据流式读取 | 未发布版本、未知资产和路径遍历固定返回 404；不暴露对象存储凭据 | `fuwu_service/core.py`、`fuwu_service/server.py`、`tests/test_public_download.py` |

详细字段、请求样例、权限、错误码与行为见 [API 接口文档](docs/API.md)。尚未生成独立 OpenAPI schema。

## 版本与发布记录

| 日期 | 变更 | 验证状态 |
| --- | --- | --- |
| 2026-10-23 | 增加匿名 stable `latest.json` 服务代理，严格绑定已发布数据库记录与不可变 manifest；补充 Django 与轻量 HTTP 路由 | 本地 `tests/test_public_latest.py`、`tests/test_public_download.py`、`tests/test_api.py` 共 8 passed；线上部署待从 GitHub 拉取新 revision 后复核 |

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

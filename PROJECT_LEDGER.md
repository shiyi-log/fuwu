# 项目台账

更新时间：2026-10-21

## 项目目标

建设可供多个软件项目使用的通用发行管理服务，提供项目登记、发行、制品完整性校验、stable/beta 渠道指针、回滚、账号权限和操作审计。公开文档只定义通用接口与构建方式，不登记部署专用项目资料。

## 当前基线

| 项目 | 当前记录 |
| --- | --- |
| 源码版本 | `0.0.3` GitHub `main` 候选；正式发布状态待标签与 Release 复核 |
| 版本格式 | 三段数字，每段一位；`0.0.9` 后进位为 `0.1.0` |
| 后端 | Python 3.14、uv、轻量 HTTP 服务、Django 适配器 |
| 前端 | Vben Admin 5.7.0、Vue 3、Ant Design Vue |
| 前端工具 | Node.js 22.18.0、pnpm 10.33.0 |
| 业务数据 | 当前使用 SQLite；MySQL/Redis repository adapter 尚未实现 |
| 对象存储 | 可替换接口；S3 兼容 provider 使用 AWS SigV4 |
| 候选基线 | GitHub `main` 与本地分支对齐于 `229c1ce`；文件树与已验证候选一致 |
| 本机部署证据 | 本机 Compose 使用该候选文件树重建 API/前端 `0.0.3` 镜像；API、前端、MySQL 健康检查为 healthy，Redis 运行中但未配置健康检查；API 健康、浏览器登录和前端访问通过 |
| 云端部署证据 | 本次仅核对 GitHub 仓库与本机 Compose；云端主机和外部服务状态未检查 |
| 远端引用 | GitHub `main` 已对齐 `229c1ce`；`v0.0.3` 标签、Release、其他 refs 和云端部署仍未更新 |

## 里程碑

| 编号 | 目标 | 状态 |
| --- | --- | --- |
| M0 | Vben 基础、项目/API 文档和 CI 建立 | 已完成 |
| M1 | Django 适配、SQLite repository、账号与项目权限 | 已完成；以当前测试结果复核 |
| M2 | SigV4 对象存储、受保护 staging 发布、回滚和审计 | 本地实现完成；真实云存储需单独验收 |
| M3 | 项目导航、发行管理、日志、系统设置与构建说明 | 本地实现完成；浏览器验收需单独记录 |
| M4 | Docker Compose 构建及健康检查 | 本机 `0.0.3` 候选镜像基于与 `229c1ce` 相同的文件树重建；API、前端和 MySQL 健康检查通过，Redis 运行中但未配置健康检查；账号登录也已本地验证 |
| M5 | 清理公开仓库全部可达历史和发行元数据 | 进行中；`main` 已对齐 `229c1ce`，标签、Release 和其他 refs 仍待复核 |

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
| 更新器读取 | 对象存储 HTTPS `GET .../latest.json` | 匿名只读 | 渠道 latest 指向不可变 v1 manifest | 客户端验证 SHA256 后暂存 | `fuwu_service/core.py`、兼容性测试 |

详细字段、请求样例、权限、错误码与行为见 [API 接口文档](docs/API.md)。尚未生成独立 OpenAPI schema。

## 版本与发布记录

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
| 2026-10-21 | 0.0.3 候选本机验证 | 与 GitHub `main` 候选 `229c1ce` 文件树一致；82 项 pytest 通过，Django check、迁移漂移检查、Python 编译和 `git diff --check` 通过；无缓存重建 `fuwu-api:0.0.3` 与 `fuwu-frontend:0.0.3`，前端使用 Node.js 22.18.0 / pnpm 10.33.0 构建；API、前端、MySQL 健康检查为 healthy，Redis 运行中但未配置健康检查；API `/healthz` 为 200，登录/用户信息/项目读取/登出通过，浏览器显示登录成功及空项目列表；前端首页访问成功，未认证 `/api/` 代理按预期返回 401 | 本机 SQLite 初始化账号仅用于验收并在结束时清除；MySQL/Redis 未接入业务仓库；云端主机状态未检查；真实对象存储和外网 HTTPS 未验证 |
| 2026-10-21 | GitHub `main` 候选 CI | `229c1ce` 的 GitHub Actions CI 全部通过：文档、Python 3.14/Django/测试、Vben 前端 typecheck/build | 仅证明该提交的仓库 CI；不代表云端部署或真实对象存储验收 |
| 2026-10-20 | Docker 运行状态 | `fuwu-api:0.0.3` 与 `fuwu-frontend:0.0.3` 容器 healthy；API `/healthz` 与前端首页 HTTP 检查成功 | 当时为既有镜像，不包含后续源码改动；不代表生产或对象存储验收 |
| 2026-10-20 | 历史清理候选审计 | 已发现历史路径和消息仍有规则命中，候选尚未满足推送条件 | 远端 refs 未改写 |
| 已完成 | GitHub Actions | `229c1ce` 的文档、Python 3.14/Django/测试和 Vben 前端 typecheck/build 全部通过 | 仅证明该提交的仓库 CI |
| 待补 | 真实对象存储与云端部署 | 分别记录准确请求、主机和服务证据 | 不能由本地检查或仓库 CI 推断 |

## 下一步

1. 修复和验证系统设置存储路由、脱敏信息权限及前端展示。
2. 完成中文文档与 CI 文档检查，执行本地后端测试、Django 与迁移检查。
3. 在保留 `0.0.3` Docker 内容的清理候选中清理路径和历史元数据，并逐 ref 扫描。
4. 复核 `main` 的 `229c1ce`、标签、PR、Release、资产和云端部署；创建正式标签前确保标签工作流不会意外重复发布。

## 变更日志

| 日期 | 变更 |
| --- | --- |
| 2026-10-21 | 对齐本地与 GitHub `main` 候选 `229c1ce`，记录本机 Compose 验证和该提交 CI 结果；云端主机状态未检查 |
| 2026-10-20 | 补充中文公开台账、准确 API 契约、凭据安全边界和验证记录；历史清理仍待远端审计 |

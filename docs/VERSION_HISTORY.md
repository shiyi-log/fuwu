# 版本记录

托管项目发行版本使用无前导零的三段 SemVer 数字，每段可包含多位数字，例如 `1.0.14`；Git 标签在数字版本前加 `v`。Fuwu 自身版本沿用单数字进位序列：某段到达 9 后向左进位并将低位归零，例如 `0.0.9` 的下一版为 `0.1.0`、`0.9.9` 的下一版为 `1.0.0`；该序列不使用 `0.0.10`。

## 版本一览

| 版本 | 源码状态 | 日期 | 数据迁移 | API 兼容性 | 验证状态 |
| --- | --- | --- | --- | --- | --- |
| `0.0.1` | 已进入当前历史 | 待核实 | SQLite 首次建表；无跨库迁移 | 初始项目、发行、制品、渠道与 latest 接口 | 不将当前候选测试追溯为该版独立验收 |
| `0.0.2` | 本地标签 `v0.0.2` 与远端 Release 存在；远端历史及说明清理待完成 | 2026-10-20 | SQLite 表初始化幂等；未声明向 MySQL/Redis 迁移 | 增加账号、项目范围、项目导航、日志和服务设置；保持既有 v1 manifest 字段 | Release/PR 元数据只读复核完成；远端资料替换与标签工作流检查待完成 |
| `0.0.3` | GitHub Release 已发布；`v0.0.3` 固定提交 `566603e` | 2026-10-21 | 保持 SQLite；无生产数据迁移声明 | 增加系统设置中的托管存储配置及项目构建说明；发行读取和 v1 manifest 保持兼容 | GitHub Actions 和 CD 发布工作流全部通过；本机无缓存 Docker 构建、Compose 健康、API 和登录/访问验证通过；云端主机部署、外部存储和生产环境未由此证明 |
| `0.0.4` | GitHub Release 已发布；`v0.0.4` 固定提交 `cfc2de2` | 2026-10-21 | 保持 SQLite；无生产数据迁移声明 | 增加 GitHub 项目授权、远端 Release staging、根目录配置初始化和成员管理界面；保持既有发布与 v1 manifest 兼容 | 102 项 pytest、80 项子测试、Django/迁移、编译、前端 typecheck/build、CI/CD、Release 资产和源码归档校验通过；真实私有 PAT、对象存储、云端部署和生产验收不由此版本自动证明 |
| `0.0.6` | 当前候选工作树；本轮准备创建正式标签与 Release | 2026-10-24 | 保持 SQLite；无数据迁移 | 将 GitHub 授权统一为 `/api/settings/github-authorization` 全局接口；增加 Heyue Python/shell Bootstrap；远端 staging、publish 和 v1 manifest 字段保持兼容 | 本地 `126 passed`、92 subtests、Django check、`uv build`、Bootstrap 编译和远端 8088/Bootstrap 只读验收通过；Heyue `v1.0.5` 发布与 stable 指针由联合发布流程单独记录 |
| `0.0.7` | GitHub Release 已发布；标签固定 `4844ae4` | 2026-10-24 | 保持 SQLite；无数据迁移 | Heyue Bootstrap 强绑定 stable 指针与不可变 manifest，并固定传入已核验版本和交互模式；stable/latest 指针保持兼容 | 本地 `127 passed`、92 subtests、Django check、`uv lock --check`、`uv build`、嵌入 Python/Shell 语法和 argv 回归通过；远端 8088 健康、8000 未监听、真实 Bootstrap `--help` 通过；Heyue stable 保持 `v1.0.4` |
| `0.0.8` | GitHub Release 已发布；标签固定 `468972d` | 2026-10-25 | 保持 SQLite；无数据迁移 | Heyue Shell Bootstrap 将 Python 主体写入受保护临时文件后启动，交互安装器继承调用端 TTY；stable/latest、匿名下载和固定安装 argv 契约保持兼容 | 本地 `133 passed`、94 subtests、Django check、`uv lock --check`、`uv build`、Shell/Python 编译和 PTY 回归通过；生产 revision `468972d`、8088 健康 200、线上 Bootstrap 摘要读回通过；Heyue 测试服务器修复后完整安装待复验 |
| `0.0.9` | GitHub Release 与生产部署已完成；生产 revision `6271b07` | 2026-11-10 | 保持 SQLite；无数据迁移 | 托管项目发行版本接受完整三段 SemVer；latest GitHub Release 改为 `release.storage.read` 只读权限；新增受 `release.storage.publish` 保护的 `/api/release-storage/sync-latest`，自动发现、暂存、校验并发布新正式版，避免降级和同版本覆盖 | 后端 `139 passed`、96 subtests，Django check、迁移漂移检查、前端 focused Vitest `28 passed`、typecheck 和生产构建通过；本地内存存储真实自动发布与制品 SHA256 校验通过；生产健康检查通过，但生产自动同步写入验收待完成 |
| `0.1.0` | GitHub Release 已发布；标签/原生产提交 `3172ed1` | 2026-11-16 | SQLite 幂等增加三张恢复表，原有业务记录保持；旧代码对加表库回退兼容已演练 | 核心拆分兼容旧导入；增加邮箱恢复，代理 HTTPS 判定收紧；发行及匿名下载契约兼容 | 后端167、前端73、CI/CD、真实S3隔离发布回退、生产升级与公网资产摘要通过；SMTP未配置，真实邮件投递待验收 |
| `0.1.1` | 正式 Release 已发布；标签固定 `ab52d74fe79234944136b7d4ef7bdd7f12485c96`；首次生产 bootstrap 完成后已在线升级至 `0.1.2` | 2026-11-23 | 无 schema 迁移；更新前使用 SQLite backup API 创建一致性快照 | 新增受权限保护的自更新检查、安装任务、状态查询、systemd 重启、版本健康检查与失败回滚；既有发行管理 API 保持兼容 | 后端188/158 subtests、前端82/typecheck/build、精确提交 CI/CD 及正式源码/更新包摘要通过；首次 bootstrap 与后续管理台 `0.1.1 → 0.1.2` 成功任务分别验收；生产未故障注入验证自动回滚 |
| `0.1.2` | GitHub Latest Release 已发布；标签与生产 revision 为 `bcec84936ba147d0ac4ded6ccc1f5d0e98d561b6`；管理台实际在线更新成功 | 2026-11-23 | 无 schema 迁移 | 配置 `FUWU_INSTALL_ROOT` 时，默认配置及相对 GitHub 凭据路径以稳定安装根解析；显式覆盖优先级、现有 API 和更新包格式保持兼容 | 本地后端193/158 subtests、前端82/typecheck/build、Django/迁移/版本/锁文件/编译、精确提交 CI/CD 和正式资产摘要通过；生产任务 succeeded/100%，健康版本、静态文件、数据库/备份完整性、配置保持与刷新恢复均读回通过 |
| `0.1.3` | 准备发布；当前工作树候选 | 2026-11-27 | 无 schema 迁移 | 增加 SMTP 自动检测工具、完善 SMTP 文档；新增受控降级版本列表端点；强制更新深层依赖修复安全漏洞；既有 API 保持兼容 | 待验证 |

## 0.1.3 SMTP 工具与依赖安全更新（2026-11-27）

- 新增 `scripts/detect_smtp_config.py` 自动检测工具，支持 30+ 邮箱服务商（QQ、163、Gmail、Outlook、企业邮箱等），自动生成 `.env` 配置格式。
- 完善 `docs/SMTP_GUIDE.md`，增加配置示例、测试工具和故障排除指南。
- 新增 `/api/downloads/{project}/versions.json` 端点，列出已发布稳定版本供受控降级选择。
- 使用 pnpm overrides 强制更新深层依赖：node-forge (1.4.0 → 1.4.4), vue-i18n (11.4.4 → 11.4.24), minimatch (→ 11.0.9), glob (→ 14.0.6), qs (→ 6.17.1), follow-redirects (→ 1.16.3)，happy-dom (20.9.0 → 21.0.4)。
- Python 依赖更新：Django (6.0.9 → 6.0.10), asgiref (3.12.1 → 3.13.0), pytest (9.1.1 → 9.2.0) 等。
- 无数据库 schema 迁移；API 与 0.1.2 保持兼容。
- 本地验证：后端 193 passed/158 subtests（SMTP 配置测试、公开版本端点测试）、前端 391 passed（42 个测试文件）均通过。

## 0.1.2 配置路径兼容修正正式发行（2026-11-23）

- 配置 `FUWU_INSTALL_ROOT` 的直接运行部署将默认 `fuwu.config.json` 和相对 GitHub 凭据路径解析到稳定安装根，避免 `current` 切换后误读新版本目录。
- 显式 `FUWU_CONFIG_FILE`、`FUWU_GITHUB_CREDENTIALS_FILE` 继续优先；未配置安装根时保持本地源码根路径行为。
- 无数据库 schema 迁移；API 与 0.1.1 更新包格式保持兼容。
- 正式标签固定 `bcec84936ba147d0ac4ded6ccc1f5d0e98d561b6`；[CI 45173550490](https://github.com/shiyi-log/fuwu/actions/runs/45173550490)、[CD 45174683717](https://github.com/shiyi-log/fuwu/actions/runs/45174683717) 均成功，[v0.1.2](https://github.com/shiyi-log/fuwu/releases/tag/v0.1.2) 已为最新正式 Release。源码包和完整更新包按正式 `SHA256SUMS` 在本地独立校验通过。
- 本地后端 `193 passed`、158 subtests，前端 `82 passed`、typecheck/build，Django、迁移、版本、锁文件及编译检查通过。
- Chrome 既有 Fuwu 标签页使用原账号登录，从“系统更新”页面确认开始 `0.1.1 → 0.1.2` 更新。任务 `9861f46ceac387b5a811072562937c51` 于 `2026-11-23T12:51:53.283664Z` 开始，`12:52:23.036450Z` 完成；页面与独立 HTTPS API 均为 `status=succeeded`、`progress=100`、`errorCode=null`。
- SSH 当前链接、包清单和 `.cloud-revision` 均读回精确 `0.1.2 / bcec84936ba147d0ac4ded6ccc1f5d0e98d561b6`，systemd active/enabled，本机和公网健康检查均为目标版本；公网 HTML 与主 JS 和活动版本逐字节一致。更新后数据库及自动备份 `integrity_check=ok`，4 个项目、24 条发行、5 条 latest、1 个账号不变；`projects`、`releases`、`latest`、`account_users` 四表与自动备份逐行完全一致。备份目录 `0700`、文件 `0600`，现有配置和凭据内容保持，staging/logs/secrets 与 Nginx 配置保持。
- 刷新并使用原凭据重新登录后，任务恢复显示更新成功、当前/最新均 `0.1.2` 和“已是最新版本”；旧静态资源导致的登录跳转失败已通过页面刷新恢复，未重置密码。本地视觉证据见项目台账。未在生产注入失败验证 updater 自动回滚。

## 0.1.0 正式发行（2026-11-16）

- 合并核心模块拆分与邮箱账号/验证码找回、主机密码恢复工具，保留旧 `core` 导入和发行 API 契约。
- 修复代理请求误用本机初始化例外、密码恢复页异步竞态、容器密钥注入及 `/releases/` 下载代理；新增前端业务测试与产品版本 CI 检查。
- 数据库只幂等增加找回验证码、限速、安全事件表；旧账号、密码哈希、发行与渠道指针保持兼容。应用回退不删除新增表。
- 本轮后端 `167 tests / OK`、前端 `73 passed`、指定 Node 22.18.0 构建、Django/锁文件/打包通过；真实 S3 独立前缀发布、HEAD/GET、幂等、冲突、回退通过，生产渠道指针不变。SMTP 未配置、真实投递未验收；精确提交 CI `43745268674`、CD `43745536296` 和生产升级通过，公网登录/恢复入口及 Heyue 1.0.24 完整资产摘要读回通过。

## 0.1.1 在线更新正式发行（2026-11-23）

- 管理台新增受 `fuwu.update.manage` 权限保护的“系统更新”页面；支持查看稳定版、确认并提交更新、轮询持久任务状态，以及页面刷新后恢复任务查询。
- GitHub Release 工作流从与 tag 一致的精确提交构建完整更新包，包含后端、依赖锁文件和前端静态构建产物；`SHA256SUMS` 同时覆盖源码包和更新包。
- 后端 updater 固定 GitHub 仓库与正式 Release 契约，校验资产大小、SHA256、归档路径与更新清单；使用独立 systemd transient worker、SQLite 一致性备份、不可变版本目录及原子 `current` 链接切换。
- 服务重启后要求 `/healthz` 返回目标版本；切换后启动或健康检查失败时恢复旧版本并验证旧版健康。更新器不覆盖 `.env`、SQLite、staging、logs 或 secrets，也不隐式处理非兼容数据库迁移。
- 正式标签固定 `ab52d74fe79234944136b7d4ef7bdd7f12485c96`；[CI 45172745086](https://github.com/shiyi-log/fuwu/actions/runs/45172745086)、[CD 45173416540](https://github.com/shiyi-log/fuwu/actions/runs/45173416540) 均成功，[v0.1.1](https://github.com/shiyi-log/fuwu/releases/tag/v0.1.1) 已正式发布；源码包和完整更新包按正式 `SHA256SUMS` 在本地独立校验通过。
- 生产完成旧 root 直接运行部署的首次 SSH bootstrap 时，当前链接和包清单读回 `0.1.1 / ab52d74`，`fuwu-server.service` active/enabled；SQLite `integrity_check=ok`，4 个项目、24 条发行、5 条 latest、1 个账号，现有 `.env`、根配置和 GitHub 凭据内容一致。备份与操作证据见项目台账；随后从管理台实际升级至上文 `0.1.2`。
- 首次引导的公网检查因 Cloudflare 拒绝 Python 默认 User-Agent 进入引导脚本回退分支；使用明确验收 User-Agent 重试后成功，Nginx 配置未修改。关键管理 API 的登录、用户、权限和项目读取均 HTTP 200；浏览器旧脚本动态 import 失败经当前资源重新加载恢复。bootstrap、管理台成功更新与未执行的生产 updater 失败回滚验收分别记录。

## 0.0.2 变更

- 增加登录会话、角色与直接权限、项目范围控制。
- 增加项目登记和按项目隔离的发行管理。
- 增加操作日志、服务设置、项目说明及构建方式字段。
- 增加发布和回滚幂等约束、不可变对象条件写入、逐对象 HEAD 校验与指针补偿。
- 保持 v1 manifest 及匿名 HTTPS 制品读取方式。
- 提供 Docker Compose 部署和 Python、Django、Vben 前端 CI 检查。

## 0.0.3 变更

- 完善系统设置中的 S3 兼容存储目标与服务端凭据管理。
- 存储凭据使用服务端 root 专属 `0600` 文件；数据库仅保存非敏感存储目标，API 不回显 secret/token。
- 项目说明页可登记通用 API 说明与构建方式。
- Docker 镜像及 Compose 默认版本标记为 `0.0.3`。
- 补充中文 API、部署、项目台账和版本文档。

## 0.0.4 变更

- 增加 GitHub Personal Access Token 授权基础能力。令牌只保存在服务端受保护的 `0600` 凭据文件中；账号身份元数据可查询，令牌不进入数据库、日志或 API 响应。
- 增加从绑定项目的 GitHub Release 下载资产到受保护 staging job 的流程。支持私有 Release，校验 draft/prerelease、资产路径、远端 `SHA256SUMS`、manifest 摘要和文件大小；下载后仍须通过普通 publish 流程更新 `latest.json`。
- 增加根目录 `fuwu.config.json`，内置超级管理员用户名、显示名称、密码环境变量名和 GitHub 凭据文件相对路径，不保存明文密码；增加 `bootstrap-config` 初始化命令。
- 增加超级管理员成员管理界面和 API：成员注册、角色/权限/项目范围维护、账号停用、管理员密码重置；成员可通过 `/api/auth/password` 自行修改密码，相关变更会撤销现有会话。

## 0.0.6 变更

- 将 GitHub 授权统一到系统设置的全局 `GET/POST/DELETE /api/settings/github-authorization`；不再提供项目级 GitHub 授权路由。
- 远端 Release staging 对带 `project` 的请求使用全局 PAT，并强制 GitHub 仓库与项目登记值一致；不带 `project` 的公开 Release 请求仍使用匿名访问。
- 全局凭据文件只接受 `{"version":1,"account":...}` 结构；旧项目级或未知顶层结构会 fail closed，不会被误当作空授权。
- 本版候选不包含真实生产 PAT、对象存储写入、云端部署或 Heyue 客户端验收；这些证据需要单独记录。
- 修复远端 Release staging 的长耗时交互：前端默认等待 15 分钟并支持 `VITE_FUWU_STAGE_REMOTE_TIMEOUT_MS`，代理模板等待 20 分钟；仅浏览器 408/504 或无 HTTP 状态的网络中断可沿用原 staging 引用提交 publish，其他业务失败必须重新 staging，项目/频道切换会清理旧上下文，避免重复下载和跨项目提交。
- 增加 `GET /api/release-storage/latest?project={id}`，服务端按项目登记的 repository 和全局 GitHub 授权选择最新 `draft=false`、`prerelease=false` Release，并校验远端 manifest 与资产清单。
- 增加 `POST /api/release-storage/stage-remote` 的 `latest: true` 模式；服务端自动发现、下载和校验正式 Release 到受保护 staging，响应只返回脱敏发行摘要，不自动发布或更新 `latest.json`。
- 发行管理页面增加“自动下载并 staging 最新正式 Release”操作，成功后预填普通 publish 表单；保留按 Tag 获取和手工确认路径。
- 增加 Heyue 匿名 Bootstrap `/api/downloads/heyue/bootstrap.py` 及兼容的 `/api/downloads/heyue/bootstrap/install.sh`；引导固定使用 Fuwu stable 发行链，校验响应摘要、manifest、`SHA256SUMS`、Python 归档和归档元数据后才运行安装器，不依赖 GitHub、PAT、管理会话或 AWS 凭据。
- 项目说明页读取绑定原项目的根目录 README，并显示来源仓库和文件信息；安装与构建说明随原项目更新，Fuwu 不再复制维护 Heyue 文案。原有 `public_api`、`build_instructions` 字段和设置接口继续保留以兼容既有数据；本轮后端全量 `132 passed`、`94` 项子测试、Django check、前端 typecheck 和生产构建通过，真实私有仓库读取与线上部署仍需单独验收。
- 本次候选的本地验证为后端 `120 passed`、88 项子测试，前端 latest focused Vitest、typecheck、生产构建和编译检查通过；真实 GitHub 授权、远端部署和 Bark 线上 staging 仍单独记录。

## 0.0.6 远端验收补充

- 远端 Release staging 在存在 asset ID 时优先使用 GitHub asset API，并继续校验 `SHA256SUMS`、manifest、大小和摘要；没有 asset ID 的旧元数据仍兼容经过主机校验的浏览器下载地址。
- 增加匿名 Fuwu 下载入口 `/api/downloads/{project}/{version}/{filename}`。它只解析已发布 stable 发行中的 manifest、`SHA256SUMS` 和资产，并以服务端受保护凭据流式读取对象；客户端无需 GitHub 或 AWS 授权。
- 2026-10-23 在远端发布 Heyue `1.0.2`，外部 HTTPS 下载代理对 Docker、前端、安装器、Python 和源码归档的 HTTP 200、文件大小和 SHA256 均已实测通过。S3 原生匿名策略仍是独立限制，不能用设置页连通性替代。

## 升级与发布说明

- SQLite 初始化会幂等创建当前领域表，现有发行和渠道指针由 SQLite repository 保存。
- Django migration 管理 Django 自有表，不自动将发行、账号或审计数据迁移至 MySQL 或 Redis。
- 本地源码测试、GitHub Actions、Docker 构建/健康检查、真实对象存储读写和独立客户端验收是不同证据类型，分别记录。
- 正式发布候选前，版本字段必须与 `pyproject.toml` 一致，标签格式为 `vX.Y.Z`。2026-11-27 移除 CI 和 CD 工作流后，创建标签不再触发原有的自动构建和发布。
- 发布说明只包含通用项目能力、公开接口和构建方式，不包含具体部署目标或项目专属验收资料。

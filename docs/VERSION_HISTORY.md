# 版本记录

版本号使用三段数字，每段只允许一位数字。某段到达 9 后向左进位并将低位归零：`0.0.9` 的下一版为 `0.1.0`，`0.9.9` 的下一版为 `1.0.0`。`0.0.10` 等两位数字段版本无效。Git 标签在数字版本前加 `v`。

## 版本一览

| 版本 | 源码状态 | 日期 | 数据迁移 | API 兼容性 | 验证状态 |
| --- | --- | --- | --- | --- | --- |
| `0.0.1` | 已进入当前历史 | 待核实 | SQLite 首次建表；无跨库迁移 | 初始项目、发行、制品、渠道与 latest 接口 | 不将当前候选测试追溯为该版独立验收 |
| `0.0.2` | 本地标签 `v0.0.2` 与远端 Release 存在；远端历史及说明清理待完成 | 2026-10-20 | SQLite 表初始化幂等；未声明向 MySQL/Redis 迁移 | 增加账号、项目范围、项目导航、日志和服务设置；保持既有 v1 manifest 字段 | Release/PR 元数据只读复核完成；远端资料替换与标签工作流检查待完成 |
| `0.0.3` | GitHub Release 已发布；`v0.0.3` 固定提交 `566603e` | 2026-10-21 | 保持 SQLite；无生产数据迁移声明 | 增加系统设置中的托管存储配置及项目构建说明；发行读取和 v1 manifest 保持兼容 | GitHub Actions 和 CD 发布工作流全部通过；本机无缓存 Docker 构建、Compose 健康、API 和登录/访问验证通过；云端主机部署、外部存储和生产环境未由此证明 |
| `0.0.4` | GitHub Release 已发布；`v0.0.4` 固定提交 `cfc2de2` | 2026-10-21 | 保持 SQLite；无生产数据迁移声明 | 增加 GitHub 项目授权、远端 Release staging、根目录配置初始化和成员管理界面；保持既有发布与 v1 manifest 兼容 | 102 项 pytest、80 项子测试、Django/迁移、编译、前端 typecheck/build、CI/CD、Release 资产和源码归档校验通过；真实私有 PAT、对象存储、云端部署和生产验收不由此版本自动证明 |
| `0.0.6` | 当前 `main` 本地候选 `2a1dcac`；正式 Release 待定 | 2026-10-23 | 保持 SQLite；无数据迁移 | 将 GitHub 授权统一为 `/api/settings/github-authorization` 全局接口；远端 staging、publish 和 v1 manifest 字段保持兼容 | 本地 focused/全量测试、Django check、迁移漂移检查和 diff check 结果单独记录；未由此版本证明真实 PAT、GitHub 私有 Release、对象存储、云端部署或 Heyue 客户端验收 |

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

## 0.0.6 远端验收补充

- 远端 Release staging 在存在 asset ID 时优先使用 GitHub asset API，并继续校验 `SHA256SUMS`、manifest、大小和摘要；没有 asset ID 的旧元数据仍兼容经过主机校验的浏览器下载地址。
- 增加匿名 Fuwu 下载入口 `/api/downloads/{project}/{version}/{filename}`。它只解析已发布 stable 发行中的 manifest、`SHA256SUMS` 和资产，并以服务端受保护凭据流式读取对象；客户端无需 GitHub 或 AWS 授权。
- 2026-10-23 在远端发布 Heyue `1.0.2`，外部 HTTPS 下载代理对 Docker、前端、安装器、Python 和源码归档的 HTTP 200、文件大小和 SHA256 均已实测通过。S3 原生匿名策略仍是独立限制，不能用设置页连通性替代。

## 升级与发布说明

- SQLite 初始化会幂等创建当前领域表，现有发行和渠道指针由 SQLite repository 保存。
- Django migration 管理 Django 自有表，不自动将发行、账号或审计数据迁移至 MySQL 或 Redis。
- 本地源码测试、GitHub Actions、Docker 构建/健康检查、真实对象存储读写和独立客户端验收是不同证据类型，分别记录。
- 正式发布候选前，版本字段必须与 `pyproject.toml` 一致，标签格式为 `vX.Y.Z`；发布工作流会据标签运行。
- 发布说明只包含通用项目能力、公开接口和构建方式，不包含具体部署目标或项目专属验收资料。

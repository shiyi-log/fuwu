# fuwu 项目台账

> 更新时间：2026-10-19
> 记录原则：实现、本地测试、CI、真实 AWS 验收和 Heyue 独立验收分开记录。

## 项目身份与目的

| 字段 | 内容 |
| --- | --- |
| 项目 | fuwu 通用发行管理服务 |
| 目的 | 为 Heyue 及未来项目提供独立的发行、对象校验、latest 指针、回滚和审计服务 |
| 仓库 | [shiyi-log/fuwu](https://github.com/shiyi-log/fuwu)（公开） |
| 本地路径 | `/Users/shiyi/Documents/ChatGPT/fuwu` |
| 默认分支 | `main` |
| 当前数字版本 | `0.0.1`（工作树待提交） |
| Git tag 规则 | `version=0.0.1` 派生 `tag=v0.0.1` |

## 当前基线（as of 2026-10-19）

- 后端入口：[manage.py](/Users/shiyi/Documents/ChatGPT/fuwu/manage.py)、[config/urls.py](/Users/shiyi/Documents/ChatGPT/fuwu/config/urls.py)、[release_api/views.py](/Users/shiyi/Documents/ChatGPT/fuwu/release_api/views.py)。领域服务和存储适配器在 [fuwu_service/core.py](/Users/shiyi/Documents/ChatGPT/fuwu/fuwu_service/core.py)。
- Python 运行时约束为 `>=3.14`，依赖由 `pyproject.toml` 和 `uv.lock` 管理。
- 本地 repository 为 SQLite；对象存储默认 MemoryObjectStore，可切换 S3 SigV4。MySQL/Redis 保留为部署替换边界。
- 前端是完整 Vben Admin 5.7.0 monorepo，二开入口为 `frontend/apps/web-antd`，使用 Vue 3、Ant Design Vue、Node 22.18.0、pnpm 10.33.0。
- 当前工作树包含待提交实现；提交前使用 `git diff --check` 和测试命令复核。

## 目标与里程碑

| 编号 | 里程碑 | 交付物 | 状态 | 完成证据 |
| --- | --- | --- | --- | --- |
| M0 | 仓库与文档 | 公开 GitHub 仓库、README、台账、API 文档、版本记录 | 已完成 | Git remote、本文档及 `docs/` |
| M1 | Django 服务基线 | 健康检查、Django 路由、轻量兼容入口、SQLite repository | 已完成 | `uv run --frozen python manage.py check`、`/healthz` |
| M2 | 发行存储核心 | project/release/artifact/channel/latest、SigV4、发布、校验、回滚、审计、RBAC | 已完成（本地） | `tests/test_core.py`、`tests/test_api.py` |
| M3 | Vben 发行管理 | 项目/频道筛选、版本和 digest、HEAD、job 进度、审计、发布/回滚确认 | 已完成（本地构建） | `frontend/apps/web-antd` typecheck/build（Node 22.18.0 环境） |
| M4 | Heyue v1 合同 | 匿名 latest/manifest/SHA256SUMS/资产校验；保留 GitHub 默认路径 | 已完成（Docker/Python direct opt-in） | Heyue commits `de68c5b...`、`7ac40d1...`、`5d93dfd...`、`1f3c2b6...`；对象存储定向测试 204 个通过，完整 updater 回归 182 个通过，安装工作流 329 个通过 |
| M5 | 交付与外部验收 | Docker Compose、CI/CD、真实 S3、匿名 HTTPS、Heyue 独立升级 | 部分完成 | 本地 Compose/CI 配置已写；真实 AWS 和独立升级待验收 |

## API 文档登记

完整字段和错误合同见 [docs/API.md](/Users/shiyi/Documents/ChatGPT/fuwu/docs/API.md)。

| 方法 | 路径 | 权限 | 状态 |
| --- | --- | --- | --- |
| GET | `/healthz` | 无 | 已实现 |
| GET | `/api/projects` | `release.storage.read` | 已实现 |
| GET | `/api/releases` | `release.storage.read` | 已实现，返回 latest、对象 HEAD、artifacts |
| GET | `/api/release-storage/status` | `release.storage.read` | 已实现，返回 validation/publish/rollback job |
| POST | `/api/release-storage/publish` | `release.storage.publish` | 已实现，requestId 幂等 |
| POST | `/api/release-storage/rollback` | `release.storage.rollback` + superadmin | 已实现，二次确认 |
| GET | `/api/release-storage/audit` | `release.storage.read` | 已实现 |

## 版本与发布记录

### 0.0.1（开发基线，未发布）

- 数字版本：`0.0.1`。
- Tag：预期为 `v0.0.1`；不要把 `v` 写进内部数字版本。
- 版本递增：每个组件只能为 `0` 到 `9`；`0.0.9 -> 0.1.0`，禁止 `0.0.10`。
- API 影响：首次提供发行管理 API；发布和回滚使用独立权限。
- 数据边界：SQLite/file repository 可替换 MySQL/Redis；未声明生产数据库迁移完成。
- 验证：本地单测、Django check/migration check 和 Vben 构建通过；未完成真实 AWS/匿名公网/独立 Heyue updater 验收。

## 决策与假设

- fuwu 不依赖 X-ray；Heyue 只把它作为独立 release-storage provider 使用。
- 发布只接受受保护 staging/job 引用；服务端不接受任意文件路径、S3 URL 或云凭据。
- immutable 版本对象位于 `releases/{project}/vX.Y.Z/`；stable/beta 指针分别位于 channel 目录。
- AWS 凭据只从 root 0600 文件、环境或 IAM role/container credentials 读取；审计、数据库、前端和日志不保存密钥。
- updater 只匿名 HTTPS GET。fuwu 真实目标配置为 bucket `heyue`、region `ap-southeast-1`、prefix `releases`，这些值不等于已完成 AWS 验收。

## 风险、阻塞与依赖

| 项目 | 当前状态 | 影响/下一步 |
| --- | --- | --- |
| 真实 AWS bucket 权限与匿名读策略 | 待确认 | 需要独立执行 SigV4 上传、HEAD digest、匿名 latest/manifest/资产 GET |
| Heyue Python/uv updater 直接匿名读取 | 代码已完成，外部验收待做 | 配置 `releaseSource=object-storage` 后匿名读取；仍需新主机实际升级证据 |
| 生产 repository | 待实现 | SQLite 是初版替换边界；生产 MySQL/Redis adapter、连接池、迁移需单独验收 |
| CI 最新运行 | 待推送后刷新 | 本地 workflow 已改 Python 3.14 + uv；推送后记录实际 run 和执行步骤 |
| 前端依赖安装 | 受 Node 22.18.0/pnpm 10.33.0 约束 | 在指定 Node/pnpm 环境复跑 typecheck/build |

## 验证与证据

### 本地 fuwu

```bash
uv run --frozen python -m unittest discover -s tests -p 'test_*.py' -v
uv run --frozen python manage.py check
uv run --frozen python manage.py makemigrations --check --dry-run
uv run --frozen python manage.py migrate --plan
```

当前定向测试基线：17 个测试通过；Django check 和 migration check 通过。测试覆盖版本进位、路径遍历、SigV4 请求、0600 凭据、同版本冲突、requestId 幂等、失败 HEAD 不更新 latest、回滚、权限和 Django API。

### 本地 Heyue

Heyue checkout 的独立提交为 `de68c5b304543c2ddc0e4cea8561949970f94d9c`，定向测试 91 个通过，安装工作流定向测试 329 个通过。该证据证明代码和离线/模拟测试，不证明真实 AWS 或公网升级。

### 外部验收边界

以下项目仍需真实环境证据：AWS bucket `heyue` 的写入/HEAD/匿名 HTTPS；Heyue updater 在新主机上的 staging、SHA256 校验和实际升级；CI 最新 run 的执行步骤；Docker Compose 在干净主机上的启动。

## 下一步

1. 推送当前 fuwu 改动并记录 GitHub Actions CI/CD 实际运行结果。
2. 在真实 AWS 目标执行一次受保护 staging 发布和匿名读验收；记录对象 key、digest、时间和 actor，不记录密钥。
3. 在新主机执行 Heyue Python/uv object-storage staging、构建和升级验收，保留匿名 HTTPS、摘要和回滚证据。
4. 设计 MySQL/Redis repository adapter 和生产身份系统；保留 SQLite/MemoryObjectStore 作为本地测试实现。

## 变更日志

| 日期 | 变更 |
| --- | --- |
| 2026-10-19 | 创建 Django/uv 发行服务、S3 SigV4 provider、发布/回滚/审计/RBAC API、Vben 5.7.0 二开页面、Heyue v1 匿名对象存储合同、CI/CD、API 文档和版本记录。 |
| 2026-10-19 | 明确数字版本规则：`0.0.9` 后进入 `0.1.0`，禁止 `0.0.10`；Git tag 使用 `v` 前缀但不写入内部 version。 |

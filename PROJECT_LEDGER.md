# fuwu 项目台账

> 台账状态：初始化  
> 更新时间：2026-10-19  
> 数据依据：当前本地仓库结构、Git 远程配置与现有文档；未将目标设计误记为已实现能力。

## 项目概览

| 字段 | 内容 |
| --- | --- |
| 项目名称 | fuwu |
| 项目目标 | 建立可维护的 Django + Django REST Framework 服务项目及其 API 文档、版本记录和交付验证流程 |
| 负责人 | 待指定 |
| 仓库 | [shiyi-log/fuwu](https://github.com/shiyi-log/fuwu)（公开） |
| 本地路径 | `/Users/shiyi/Documents/ChatGPT/fuwu` |
| 默认分支 | `main` |
| 当前版本 | 未发布（`Unreleased`） |
| 当前提交 | `23d4334`（修复无依赖缓存时的 CI 基线） |
| 项目状态 | 文档、CI/CD 结构初始化；暂无 Django 运行时代码 |
| 更新时间 | 2026-10-19 |

## 技术栈

| 层级 | 目标技术 | 当前状态 |
| --- | --- | --- |
| 语言 | Python 3.x | 待确认版本 |
| Web 框架 | Django | 目标栈，尚未安装/初始化 |
| API 框架 | Django REST Framework | 目标栈，尚未安装/初始化 |
| 数据库 | 待定（开发环境可先使用 SQLite） | 未配置 |
| API 格式 | HTTP/HTTPS + JSON | 已在 `docs/API.md` 记录约定 |
| 版本策略 | Semantic Versioning（`MAJOR.MINOR.PATCH`） | 已在 `docs/VERSION_HISTORY.md` 记录 |

## 环境台账

| 环境 | 用途 | 配置状态 | 地址/入口 | 负责人 |
| --- | --- | --- | --- | --- |
| 本地开发 | 开发、单元测试和 API 联调 | 待配置 Django 项目与依赖 | 待定 | 待指定 |
| 测试/CI | 自动化检查、迁移和接口测试 | 尚未配置 | 待定 | 待指定 |
| 生产 | 对外服务 | 尚未规划 | 待定 | 待指定 |

## 里程碑

| 编号 | 里程碑 | 目标交付物 | 状态 | 完成标准 |
| --- | --- | --- | --- | --- |
| M0 | 仓库初始化 | Git 仓库、README、文档目录 | 已完成 | `main` 分支可访问，工作区无未提交变更（以实际状态为准） |
| M1 | Django 基础工程 | Django 项目、依赖文件、开发启动说明 | 待开始 | `manage.py check` 通过，开发服务可启动 |
| M2 | API 基线 | DRF 配置、版本化路由、serializer、权限约定 | 规划中 | 至少一个真实接口完成登记、测试和错误响应验证 |
| M3 | 数据层 | model、migration、测试数据库流程 | 规划中 | migration 检查和测试库迁移通过 |
| M4 | 可交付版本 | 发布标签、版本记录、CI 验证证据 | 规划中 | 版本记录、API 文档、测试与发布信息一致 |
| M5 | CI/CD 基线 | `.github/workflows/ci.yml`、`.github/workflows/cd.yml` | 已完成 | CI 可在当前文档阶段通过；CD 在 SemVer 标签上构建并发布源码包 |

## 风险与待决策事项

| 风险/事项 | 影响 | 应对措施 | 状态 | 负责人 |
| --- | --- | --- | --- | --- |
| Django 与 DRF 版本尚未锁定 | 构建结果和 API 行为可能漂移 | 确定 Python/Django/DRF 兼容矩阵并写入依赖文件 | 待处理 | 待指定 |
| 数据库未选型 | 迁移、并发和部署方式无法验收 | 明确开发、测试、生产数据库及备份策略 | 待处理 | 待指定 |
| 鉴权方案未确定 | API 权限与安全验收无法完成 | 选择 session、token 或 JWT 等方案并记录权限矩阵 | 待处理 | 待指定 |
| API 业务范围未定义 | 无法登记真实接口或确定 MVP | 补充领域模型、资源和首批接口清单 | 待处理 | 待指定 |
| CI/CD 与部署入口未配置 | 不能独立证明构建和发布可用 | 建立 CI、迁移检查、API 测试和发布流程 | 待处理 | 待指定 |
| CD 发布权限与保护规则未配置 | 标签发布可能缺少人工审批或环境保护 | 在 GitHub 环境中配置 required reviewers、分支保护和发布权限 | 待处理 | 待指定 |

## 验证记录

| 日期 | 环境 | 检查项 | 命令/证据 | 结果 |
| --- | --- | --- | --- | --- |
| 2026-10-19 | 本地仓库 | 文档结构与 Markdown 基本检查 | `git diff --check`；检查 `docs/API.md`、`docs/VERSION_HISTORY.md`、`docs/PROJECT_LEDGER.md` | 通过 |
| 2026-10-19 | GitHub | 仓库可见性 | `gh repo view shiyi-log/fuwu` | 已改为公开 |
| 2026-10-19 | GitHub Actions | CI 首轮执行 | [run 39165315026](https://github.com/shiyi-log/fuwu/actions/runs/39165315026) | 失败；`setup-python` 的 pip 缓存要求仓库存在依赖清单 |
| 2026-10-19 | GitHub Actions | CI 修复后执行 | [run 39165406604](https://github.com/shiyi-log/fuwu/actions/runs/39165406604) | 通过；文档检查与 Python/Django 基线均成功 |
| 待补充 | 本地开发 | Django 配置检查 | `python manage.py check` | 待 Django 工程创建后执行 |
| 待补充 | CI/测试 | 迁移与 API 测试 | `makemigrations --check --dry-run`、测试命令 | 待配置 |

## 维护规则

1. 里程碑状态只有在对应完成标准和验证证据具备后才改为“已完成”。
2. 负责人、环境地址、依赖版本和当前版本发生变化时，同步更新本台账和版本记录。
3. API 路径、鉴权、serializer、数据模型或 migration 发生变化时，同时更新 `docs/API.md` 与 `docs/VERSION_HISTORY.md`。
4. 未验证、待配置或目标技术栈必须使用明确状态，不以规划内容代替运行证据。


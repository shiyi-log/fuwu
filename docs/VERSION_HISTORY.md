# fuwu 版本记录

本文记录数字版本、API/数据影响和验证证据。发布、CI、真实 AWS 验收和 Heyue 独立升级是不同状态，不能互相替代。

## 版本规则

- 内部版本严格是三段数字 `MAJOR.MINOR.PATCH`，每段只能使用 `0` 到 `9`。
- `0.0.9` 的下一版本是 `0.1.0`；`0.0.10` 无效并会被拒绝。
- `version` 只保存数字版本；Git tag 派生为 `v<version>`，例如 `version=0.0.1`、`tag=v0.0.1`。
- `next_version()` 和 `validate_version()` 位于 `fuwu_service/core.py`，对应测试位于 `tests/test_core.py`。

## 版本记录表

| 版本 | 状态 | 日期 | 变更摘要 | API 影响 | 数据/迁移 | 本地验证 | CI | 真实 AWS/独立验收 | Git 标签 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `0.0.1` | 开发基线，未发布 | 2026-10-19 | Django/轻量 HTTP 发行服务、SQLite/Memory repository、S3 SigV4、immutable manifest/latest、发布/回滚/审计/RBAC、Vben 5.7.0 发行管理页、Heyue v1 合同 | 新增 `/healthz`、projects、releases、storage status/publish/rollback/audit；独立 `release.storage.*` 权限 | SQLite 初版；MySQL/Redis 为可替换边界；未声明生产迁移完成 | 18 个 fuwu 测试通过；Django check、migration check 通过；Vben typecheck/build 在 Node 22.18.0/pnpm 10.33.0 环境通过 | workflow 已配置 Python 3.14 + uv；推送后记录最新 run | 未完成真实 bucket 写入、HEAD/匿名 HTTPS 和 Heyue 新主机升级；Docker/Python direct 代码及离线测试已通过 | 预期 `v0.0.1` |

## `0.0.1` 变更明细

### 新增

- 建立 Django 项目入口和可替换的领域服务边界；保留 `uv run fuwu-server` 轻量入口用于离线 smoke test。
- 建立 project、release、artifact、stable/beta channel、latest pointer、发布 job、校验 job、回滚 job 和审计记录。
- 发布只接受受保护 staging/job 引用；发布前校验 manifest、SHA256SUMS、40 位 revision、版本/tag、资产大小和 digest。
- 对象上传到 `releases/{project}/v{version}/`，逐对象 HEAD+digest 回读后才写最小化 channel `latest.json`；同版本不同 digest 返回冲突；requestId 幂等。
- S3 兼容 provider 使用 AWS SigV4。写入凭据只来自 root 0600 文件、环境、container credentials 或 IAM role；匿名读取端不携带凭据。
- 前端基于下载的 Vben Admin 5.7.0 完整 monorepo，在 `apps/web-antd` 中提供项目/频道筛选、版本/revision/digest、HEAD 状态、job 进度、审计、发布和回滚确认。
- Heyue 增加匿名 HTTPS object-storage provider，默认仍保留 GitHub Release 路径；Docker direct updater 已接入 `releaseSource=object-storage`。

### API 与兼容性

- Heyue v1 manifest 使用 `schemaVersion=1`、`repository`、数字 `version`、`tag`、40 位 `revision`、`releaseId`、`publishedAt`、`draft=false`、`prerelease=false`、`assets.docker/python`、`checksums`、`verifiedAt`、`verificationSource=heyue-release-storage`。
- latest 指针只引用已有 immutable manifest，使用 `manifestKey` 和 `manifestSha256`；回滚只替换指针，不删除历史对象。

### 验证证据

- fuwu：`uv run --frozen python -m unittest discover -s tests -p 'test_*.py' -v`（16 passed）。
- fuwu：`uv run --frozen python manage.py check`、`makemigrations --check --dry-run`、`migrate --plan` 通过。
- Heyue：提交 `de68c5b304543c2ddc0e4cea8561949970f94d9c`；对象存储和 Docker updater 定向测试 91 passed；安装工作流定向测试 329 passed。
- Vben：在 Node 22.18.0、pnpm 10.33.0 环境执行 `pnpm --filter @vben/web-antd typecheck` 和 production build 通过。

### 未完成外部条件

- 尚未用真实 AWS bucket `heyue` / region `ap-southeast-1` / prefix `releases` 完成写入、HEAD 摘要、匿名 HTTPS GET 和对象权限验收。
- Heyue Python/uv updater 已支持 opt-in 直接匿名对象存储拉取；真实 AWS、公网和新主机升级仍待验收。
- 尚未完成干净主机上的 Docker Compose、生产 MySQL/Redis adapter 和独立身份系统验收。

## 维护记录模板

正式发布时新增：

```markdown
## X.Y.Z - YYYY-MM-DD

- 状态：已发布
- 数字版本：`X.Y.Z`
- Git 标签：`vX.Y.Z`
- API 影响：新增 / 修改 / 废弃 / 无
- 数据或迁移：
- 本地验证：
- CI：
- 真实 AWS 验收：
- Heyue 独立验收：
- 回滚方式：切换到已有 immutable manifest 的 channel latest 指针
```

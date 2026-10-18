# fuwu API 接口文档

> 状态：已实现的 v1 接口登记
> 更新日期：2026-10-19
> 实现来源：`release_api/views.py`、`release_api/urls.py`、`fuwu_service/core.py`

fuwu 为 Heyue 及未来项目提供独立发行存储管理。Django 路由和保留的轻量 `ThreadingHTTPServer` 入口共用同一领域服务；两者都使用本文档的 JSON 合同。

## 通用约定

- 基础路径：`/`；API 路径为 `/api/...`。
- 请求和响应：`application/json; charset=utf-8`。
- 健康检查 `GET /healthz` 不需要鉴权。
- 除健康检查外，管理 API 使用 `Authorization: Bearer <token>`。token 由服务端身份系统或环境注入，前端不保存 AWS 凭据。
- `X-Request-ID` 用于追踪；写入操作优先使用 `Idempotency-Key` 作为 requestId。重复 requestId 返回第一次已记录的结果。
- 失败响应固定为：

```json
{"code":"error_code","message":"safe message","request_id":"req-..."}
```

错误消息不包含 AWS access key、secret、session token、Authorization 头或本机 staging 路径。

## 权限

| 权限 | 允许的操作 |
| --- | --- |
| `release.storage.read` | 项目、版本、存储状态和审计查询 |
| `release.storage.publish` | 校验并发布受保护 staging/job |
| `release.storage.rollback` | 回滚 channel latest 指针；仍需 `superadmin` 标记和请求体二次确认 |

这些权限独立于 Heyue 的 `system.version.manage`。

## 数据和版本规则

- `project` 使用小写 slug；`channel` 只能是 `stable` 或 `beta`。
- 内部 `version` 严格为 `MAJOR.MINOR.PATCH`，每段只能是单个数字 `0` 到 `9`。因此 `0.0.9` 的下一版本是 `0.1.0`，`0.0.10` 会被拒绝。
- Git tag 由数字版本派生为 `v<version>`，例如 `version=0.0.1`、`tag=v0.0.1`；tag 前缀不会写入数字版本字段。
- revision 必须是 40 位十六进制提交 SHA；manifest 必须是 `schemaVersion=1`、`draft=false`、`prerelease=false` 的最终发行。
- 发布对象写入 `releases/{project}/v{version}/` immutable 前缀，channel 指针写入 `releases/{project}/{channel}/latest.json`。

## 接口登记

| ID | 方法 | 路径 | 权限 | 状态 | 实现 |
| --- | --- | --- | --- | --- | --- |
| API-001 | GET | `/healthz` | 无 | 可用 | `release_api/views.py:healthz` |
| API-002 | GET | `/api/projects` | `release.storage.read` | 可用 | `release_api/views.py:projects` |
| API-003 | GET | `/api/releases` | `release.storage.read` | 可用 | `release_api/views.py:releases` |
| API-004 | GET | `/api/release-storage/status` | `release.storage.read` | 可用 | `release_api/views.py:status` |
| API-005 | POST | `/api/release-storage/publish` | `release.storage.publish` | 可用 | `release_api/views.py:publish` |
| API-006 | POST | `/api/release-storage/rollback` | `release.storage.rollback` + superadmin | 可用 | `release_api/views.py:rollback` |
| API-007 | GET | `/api/release-storage/audit` | `release.storage.read` | 可用 | `release_api/views.py:audit` |

## API-001：健康检查

`GET /healthz` 返回 `200`：

```json
{"status":"ok"}
```

## API-002：项目列表

`GET /api/projects` 返回：

```json
{"items":[{"id":"heyue","name":"heyue","repository":"shiyi-log/heyue","created_at":"2026-10-19T00:00:00Z"}],"request_id":"req-..."}
```

## API-003：发行列表

`GET /api/releases?project=heyue&channel=stable` 的 query 参数均可选。响应包含最新指针标记、对象 HEAD 状态和资产摘要：

```json
{
  "items": [{
    "id": "...",
    "project_id": "heyue",
    "version": "0.0.1",
    "channel": "stable",
    "repository": "shiyi-log/heyue",
    "tag": "v0.0.1",
    "revision": "40位提交SHA",
    "release_id": 100102,
    "published_at": "2026-10-19T00:00:00Z",
    "digest": "64位摘要",
    "manifest_key": "releases/heyue/v0.0.1/manifest.json",
    "status": "published",
    "latest": true,
    "objectHeadStatus": "verified",
    "artifacts": [{"name":"SHA256SUMS","key":"releases/heyue/v0.0.1/SHA256SUMS","size":123,"sha256":"64位摘要"}]
  }],
  "request_id":"req-..."
}
```

`objectHeadStatus` 为 `verified` 或 `unverified`；查询不会修改对象。

## API-004：存储状态和任务进度

`GET /api/release-storage/status` 返回 provider、统计和最近的 validation、publish、rollback job：

```json
{
  "provider":"MemoryObjectStore",
  "prefix":"releases",
  "projects":1,
  "releases":1,
  "jobs": {
    "validation":{"status":"validated","progress":100,"jobId":"...","requestId":"...","updatedAt":"..."},
    "publish":{"status":"published","progress":100,"jobId":"...","requestId":"...","updatedAt":"..."},
    "rollback":{"status":"idle","progress":0,"jobId":null,"requestId":null,"updatedAt":null}
  },
  "request_id":"req-..."
}
```

## API-005：发布

`POST /api/release-storage/publish` 只接受受保护 staging/job 引用，不接受任意路径、S3 URL 或凭据。

请求示例：

```json
{
  "project":"heyue",
  "channel":"stable",
  "version":"0.0.1",
  "staging_ref":"job-20261019-001"
}
```

服务端从 staging 目录读取 `manifest.json`、`SHA256SUMS` 和资产，校验版本/tag、repository、revision、最终发行标记、路径、文件大小和 SHA256。然后依次上传资产、`SHA256SUMS`、manifest；每个对象都执行 HEAD 摘要回读。全部 immutable 对象成功后才写 channel `latest.json`。同一版本不同 digest 返回 `409 release_conflict`；相同 requestId 返回幂等结果。

成功返回 `201`，重复成功返回 `200`：

```json
{
  "status":"published",
  "job":{"id":"req-...","operation":"publish","status":"published","progress":100},
  "release":{"version":"0.0.1","channel":"stable","manifest_key":"releases/heyue/v0.0.1/manifest.json","status":"published"}
}
```

常见错误：`401 unauthorized`、`403 forbidden`、`404 staging_not_found`、`409 release_conflict`、`422 invalid_version` / `invalid_manifest` / `checksum_mismatch`、`502 storage_verification_failed`。

## API-006：回滚

`POST /api/release-storage/rollback` 需要独立 rollback 权限、`superadmin` 标记、`confirm=true` 和非空原因。目标必须是已经存在的 immutable release，可以按 `release_id` 或 `version` 指定。

```json
{
  "project":"heyue",
  "channel":"stable",
  "version":"0.0.1",
  "confirm":true,
  "reason":"回滚到已验证版本"
}
```

回滚只替换 `releases/heyue/stable/latest.json` 指针，不删除或覆盖历史对象。审计记录 before/after、actor、requestId、时间、原因、pointer key 和 digest。

## API-007：审计

`GET /api/release-storage/audit?project=heyue` 返回发布、校验失败、发布失败和回滚事件。事件详情只包含公开 actor、requestId、状态、进度、对象 key、版本和 SHA256；不会保存 secret/token 或私有 staging 路径。

## 本地验证

```bash
uv run --frozen python -m unittest discover -s tests -p 'test_*.py' -v
uv run --frozen python manage.py check
uv run --frozen python manage.py makemigrations --check --dry-run
```

真实 AWS SigV4 上传、匿名 HTTPS GET 和 Heyue updater 端到端 staging 仍须独立验收，不能用本地 MemoryObjectStore 通过替代。

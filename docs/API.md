# API 接口文档

本文描述 fuwu 的通用管理 HTTP 接口。请求和响应使用 UTF-8 JSON；基础路径为 `/`。服务默认由轻量 HTTP 入口提供，也可通过 Django 适配器运行相同的接口与领域逻辑。

## 通用约定

- 除健康检查和登录外，管理接口需要 `Authorization: Bearer <会话>`。
- JSON 请求体必须为对象，最大 65,536 字节。响应设置 `Cache-Control: no-store`。
- `requestId` 依次取 `Idempotency-Key`、`X-Request-ID`、请求体 `requestId`；未提供时由服务生成。对象存储设置接口始终由服务生成 request ID，避免凭据被写入审计或操作日志。
- request ID 长度为 1 至 128 个 ASCII 字母、数字、点、下划线或连字符，且首字符为字母或数字；不符合格式时服务会生成新值。
- 集合接口一般返回 `{"items":[...]}`。登录接口返回 `{"code":0,"data":...}`；其他成功接口返回 JSON 对象。
- 错误响应使用固定错误码和消息，不回显密码、令牌、存储密钥、未知字段值或本机路径。

```json
{"code":"forbidden","message":"permission denied","request_id":"req-example"}
```

## 权限与项目范围

权限由服务端会话决定，不接受客户端声明的角色或权限。项目范围也由服务端账号记录控制；全项目范围仅授予超级管理员。请求越权访问的项目时返回 `403 forbidden`。

| 权限 | 操作 |
| --- | --- |
| `release.storage.read` | 查询发行、存储状态和发行审计 |
| `release.storage.publish` | 发布受保护的 staging/job 作业 |
| `release.storage.rollback` | 回滚渠道指针；同时要求超级管理员和二次确认 |
| `projects.manage` | 登记和更新项目元数据 |
| `users.manage` | 管理账号和密码 |
| `logs.read` | 查询结构化操作日志 |
| `settings.manage` | 更新服务设置；管理对象存储凭据还要求超级管理员 |

渠道固定为 `stable` 与 `beta`。内置角色为 `superadmin`、`release-manager` 与 `viewer`，最终权限由服务端计算。

## 接口目录

未标记为公开的接口均要求有效 Bearer 会话。

| 方法 | 路径 | 访问要求 | 成功状态 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | 公开 | 200 |
| `POST` | `/api/auth/login` | 公开；有效账号密码 | 200 |
| `POST` | `/api/auth/logout` | 已登录 | 200 |
| `GET` | `/api/auth/codes` | 已登录 | 200 |
| `POST` | `/api/auth/password` | 当前账号密码 | 200 |
| `GET` | `/api/user/info` | 已登录 | 200 |
| `GET` | `/api/roles` | 已登录 | 200 |
| `GET`、`POST` | `/api/users` | 查询与变更需 `users.manage` | 200、201 |
| `PATCH` | `/api/users/{id}` | `users.manage` | 200 |
| `POST` | `/api/users/{id}/password` | `users.manage` | 200 |
| `GET`、`POST` | `/api/projects` | 已登录查询；创建需 `projects.manage` 和全项目范围 | 200、201 |
| `GET` | `/api/projects/{id}` | 项目范围 | 200 |
| `PATCH` | `/api/projects/{id}` | `projects.manage` 和项目范围 | 200 |
| `GET` | `/api/releases` | `release.storage.read` 和项目范围 | 200 |
| `GET` | `/api/release-storage/status` | `release.storage.read` 和项目范围 | 200 |
| `POST` | `/api/release-storage/publish` | `release.storage.publish` 和项目范围 | 201；幂等重放为 200 |
| `POST` | `/api/release-storage/rollback` | `release.storage.rollback`、项目范围和超级管理员 | 200 |
| `GET` | `/api/release-storage/audit` | `release.storage.read` 和项目范围 | 200 |
| `GET` | `/api/logs` | `logs.read` 和项目范围 | 200 |
| `GET` | `/api/settings` | 已登录 | 200 |
| `PATCH` | `/api/settings` | `settings.manage` | 200 |
| `PATCH` | `/api/settings/storage` | `settings.manage`、超级管理员及安全连接 | 200 |

## 登录与账号

首次管理员必须在服务所在机器通过交互命令创建，详见[部署说明](DEPLOYMENT.md)。服务不提供默认账号或默认密码。登录成功响应包含 `accessToken`、`expiresAt` 和公开用户资料；会话有效期为一小时，服务仅持久化会话摘要。密码以 PBKDF2 哈希保存。

用户名为小写的 3 至 64 位字母、数字、点、下划线或连字符，首位必须为字母或数字。密码长度为 12 至 256 个字符且不能全为空白。未知账号、错误密码、停用账号和登录限速统一返回 `401 unauthorized`。

更改用户名、角色、直接权限、项目范围、启用状态或密码会撤销该账号现有会话。显示名称变更不会撤销会话。密码修改后需要重新登录。账号对象不包含密码哈希和会话数据；停用账号通过 `PATCH /api/users/{id}` 并设置 `active=false` 完成。

## 项目

`GET /api/projects` 返回当前账号可访问的项目。项目对象包括 `id`、`name`、`repository`、`description`、`public_api`、`build_instructions`、`active` 和 `created_at`。

创建项目示例：

```json
{
  "id":"demo-service",
  "name":"示例服务",
  "repository":"example-org/demo-service",
  "description":"通用项目登记示例"
}
```

`id`、`name` 和 `repository` 必填。`id` 为 1 至 63 位小写 slug，允许点、下划线和连字符，不允许路径遍历。名称去除首尾空白后为 1 至 120 个字符；说明、公共 API 和构建方式文本最长各 12,000 个字符。`repository` 接受 `owner/repo` 或不含凭据、查询参数、片段的 GitHub HTTPS 地址。

`PATCH /api/projects/{id}` 可更新非空的 `name`、`description`、`public_api`、`build_instructions` 和布尔 `active`。仓库绑定创建后不可修改，已有发行记录不会随项目说明变更而改写。

## 版本与发行查询

版本由三段数字组成，每段仅一位，范围为 0 至 9。例如 `0.0.9` 的下一版本为 `0.1.0`；`0.0.10` 无效。Git 标签格式为 `v<版本号>`，`revision` 必须是 40 位十六进制 Git SHA。

`GET /api/releases` 支持可选 `project` 和 `channel` 查询参数，发行记录字段使用实际 JSON 命名：`project_id`、`version`、`channel`、`repository`、`tag`、`revision`、`release_id`、`published_at`、`digest`、`manifest_key`、`status`、`latest`、`objectHeadStatus` 和 `artifacts`。`digest` 是规范化 manifest 字节与 `SHA256SUMS` 文件字节拼接后计算的 SHA256，用于识别完整发行输入；各制品另有独立 `sha256`。

`GET /api/release-storage/status` 支持相同查询参数，返回 provider、前缀、项目和发行计数、对象存储连通性以及最近 validation、publish、rollback 任务摘要。任务进度代表处理阶段，不代表按上传字节计算的实时百分比。

`GET /api/release-storage/audit` 支持 `project` 与 `channel` 查询参数，最多返回最近 100 条发行和指针事件。记录包含事件类型、项目、版本、渠道、操作者、request ID、时间和经筛选的详情。

## 发布

`POST /api/release-storage/publish` 只接受受保护的 staging/job 引用；拒绝任意路径、对象存储 URL、凭据、文件上传和未知字段。

```json
{
  "project":"demo-service",
  "channel":"stable",
  "version":"1.2.3",
  "tag":"v1.2.3",
  "revision":"1111111111111111111111111111111111111111",
  "staging_ref":"job-20261020-001"
}
```

请求允许 `project`、`channel`、`version`、`tag`、`revision`、`staging_ref`、兼容字段 `job_ref` 和 `requestId`。`channel` 默认为 `stable`；`staging_ref` 与 `job_ref` 至少提供一个。

作业须包含 `manifest.json`、`SHA256SUMS` 及 manifest 声明的资产。manifest 必须包含 `schemaVersion=1`、`repository`、`version`、`tag`、40 位 `revision`、`releaseId`、`draft=false`、`prerelease=false` 与 `assets`。服务端会补齐缺省的 `publishedAt`、`verifiedAt`、`verificationSource` 和 `checksums`。资产按 `docker` 或 `python` 分类；项目可只发布其中一种或两种。每个资产声明相对路径、大小和 SHA256。SHA256SUMS 的路径集合必须与资产集合完全一致。

服务按以下顺序操作：校验输入和 manifest、仓库绑定、版本与标签、revision、文件大小及 SHA256；以不可覆盖方式上传至 `releases/{project}/vX.Y.Z/`；逐对象执行 HEAD 并核对长度和摘要；所有不可变对象验证成功后才条件更新相应渠道的 `latest.json`。任何失败都不会将新版本设为 latest。同一项目和版本对应不同发行摘要时返回 `409 release_conflict`。

重复 `requestId` 只有在操作、项目、渠道、操作者和规范化请求内容均相同时才幂等。首次发布返回 `201`；相同请求重放返回 `200`。冲突重用返回 `409 idempotency_conflict`。

对外 v1 manifest 保留 `schemaVersion`、`repository`、`version`、`tag`、`revision`、`releaseId`、`publishedAt`、`draft`、`prerelease`、`assets`、`checksums`、`verifiedAt` 和 `verificationSource` 字段。`latest.json` 指向已存在的不可变 manifest 并携带其 SHA256。客户端通过匿名 HTTPS 获取 latest、manifest 与制品，并在暂存前校验 SHA256；客户端不接收写入凭据。

## 回滚

`POST /api/release-storage/rollback` 只将渠道指针切换至同一项目、同一渠道下已经存在的不可变发行，不删除或覆盖历史对象。

```json
{
  "project":"demo-service",
  "channel":"stable",
  "version":"1.2.2",
  "confirm":true,
  "reason":"恢复到此前验证过的发行"
}
```

请求必须包含 `confirm=true` 和非空 `reason`。目标通过 `version` 或发行记录 `release_id` 指定。审计保存回滚前后指针、操作者、request ID、时间、原因、对象键和摘要。

## 操作日志

`GET /api/logs` 支持 `project`、`channel`、`category`、`level` 和 `limit` 查询参数；`limit` 默认 50，范围为 1 至 500。日志仅记录结构化字段和预定义路由模板，不记录请求头、请求体、原始查询文本或未知路径。存储凭据与会话令牌不进入日志；操作日志保留期可由服务设置调整，发行审计单独保存。

## 服务设置与凭据

`GET /api/settings` 返回通用偏好、只读运行信息和存储配置。`PATCH /api/settings` 只接受 `appName`、`timezone`、`defaultChannel` 和 `logRetentionDays`。日志保留天数范围为 7 至 3650；频道为 `stable` 或 `beta`。

对象存储目标和凭据通过 `PATCH /api/settings/storage` 更新。请求必须通过 HTTPS 或 localhost 页面发送，并由具有 `settings.manage` 权限的超级管理员执行。请求字段为 `bucket`、`region`、`endpoint`、`prefix`、`accessKeyId`、`secretAccessKey` 和可选 `sessionToken`。凭据只保存到 root 所有、权限为 `0600` 的服务端文件；数据库只存储非敏感目标配置。Endpoint 必须是 AWS S3 地址或管理员预先配置的允许地址。已有发行或指针后不允许修改存储目标，但可以轮换凭据。

响应不包含 Secret Access Key 或 Session Token。具有 `settings.manage` 的用户可看到脱敏后的 Access Key ID 提示；其他登录用户不会收到该提示。更新凭据时 Access Key ID 与 Secret Access Key 必须同时提供；留空凭据仅能沿用现存的受保护凭据文件。

## 常见错误

| HTTP 状态 | 错误码 | 含义 |
| --- | --- | --- |
| 400 | `confirmation_required`、`secure_transport_required` | 缺少二次确认或连接不安全 |
| 401 | `unauthorized` | 会话无效、过期或认证失败 |
| 403 | `forbidden` | 权限或项目范围不足 |
| 404 | `not_found`、`project_not_found`、`release_not_found` | 目标不存在 |
| 409 | `release_conflict`、`immutable_object_conflict`、`idempotency_conflict` | 发行、对象或幂等冲突 |
| 422 | `invalid_query`、`invalid_manifest`、`invalid_publish_request`、`invalid_storage_settings` | 请求或发行数据无效 |
| 502 | `storage_verification_failed` | 对象校验未通过 |
| 503 | `pointer_reconciliation_required`、`credential_storage_unavailable` | 指针需人工核对或凭据安全存储不可用 |

其他领域错误仍使用固定错误码和安全消息，不包含存储凭据、私有路径或未知输入值。

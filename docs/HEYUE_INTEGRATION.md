# Heyue 发行存储兼容约定

> 文档状态：fuwu 与 Heyue 的 v1 对接合同
> 最后更新：2026-10-19（Asia/Shanghai）
> 适用项目：`shiyi-log/heyue`、未来接入 fuwu 的其他发行项目

这份文档把 Heyue 当前更新器的真实约束，与 fuwu 新增的匿名对象存储协议分开记录。Heyue 已加入 opt-in 的匿名对象存储 provider：Docker direct 和 Python/uv updater 都可以读取 `latest.json`、immutable manifest、`SHA256SUMS` 和对应资产；默认 GitHub Release 路径保持兼容。真实 AWS、公网匿名 HTTPS 和新主机升级仍需独立验收。

## 1. 已核对的 Heyue 现状

当前 checkout：`/Users/shiyi/Documents/heyue`。

- `scripts/python_update_agent.py:43-113` 的 `manifest_valid()` / `verify_stage()` 要求 manifest 的顶层字段严格为 `repository`、`version`、`tag`、`revision`、`releaseId`、`publishedAt`、`draft`、`prerelease`、`asset`、`checksums`、`verifiedAt`、`verificationSource`。
- `scripts/docker_update_agent.py:459-512` 使用相同的 GitHub Release manifest 结构，但资产名称约束为 `heyue-docker-{version}-linux-amd64.tar.gz`；Python 资产名称约束为 `heyue-python-{version}.tar.gz`。
- 当前校验要求 `verificationSource=github-release-api`、`draft=false`、`prerelease=false`、`tag=v{version}`、`revision` 为完整 40 位十六进制提交、`releaseId` 为正整数、`checksums.name=SHA256SUMS`、`checksums.sha256` 为 64 位十六进制摘要。
- `tests/test_deploy_test_release.py` 已覆盖正式标签、移动分支不可替代固定 tag、SHA256SUMS 与 asset digest 一致性、路径遍历与重复条目拒绝。
- 默认更新发现接口仍通过 GitHub API；当配置 `releaseSource=object-storage` 时，Docker direct 与 Python/uv updater 使用 `scripts/release_storage_client.py` 匿名 HTTPS GET，不读取 GitHub token。
- `scripts/version_update_client.py` 仍只向本机 Unix socket 更新器提交版本与 requestId，不接收浏览器 URL、任意路径或凭据。

上述现状是兼容基线：对象存储代码和离线测试已完成，但不能把本地测试当成真实 AWS 或远端升级验收。

## 2. fuwu 对 Heyue 的 v1 对象存储合同

fuwu 发布一个版本时使用不可变前缀：

```text
releases/{project}/v{version}/
```

对 Heyue，`project=heyue`，因此 v0.0.1 的示例前缀是 `releases/heyue/v0.0.1/`。版本前缀内的对象一旦成功发布不得覆盖或删除；回滚只改变 channel 的 `latest.json` 指针。

### 2.1 不可变 manifest

manifest 对象建议放在：

```text
releases/heyue/v0.0.1/manifest.json
```

必须包含以下字段：

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schemaVersion` | integer | 固定为 `1` |
| `project` | string | 固定为 `heyue`；其他项目使用自己的 project slug |
| `repository` | string | Heyue 为 `shiyi-log/heyue` |
| `version` | string | 三段数字版本，每段为单个十进制数字，例如 `0.0.1`；`0.0.9` 后递增为 `0.1.0`，不使用 `0.0.10` |
| `tag` | string | 必须等于 `v` + `version` |
| `revision` | string | 完整 40 位小写十六进制提交 SHA |
| `releaseId` | integer/string | 发布系统中的稳定 ID；同一 immutable 版本不能变化 |
| `publishedAt` | string | UTC ISO 8601 时间戳，推荐 `...Z` |
| `draft` | boolean | 固定为 `false` |
| `prerelease` | boolean | stable 渠道固定为 `false`；beta 仍必须显式标注，不得混入 stable |
| `channel` | string | `stable` 或 `beta` |
| `assets` | object | 至少包含 `docker`、`python` 两个资产描述；未来可扩展其他键 |
| `checksums` | object | 至少包含 `name=SHA256SUMS` 和清单自身 `sha256` |
| `verifiedAt` | string | fuwu 完成本地校验及对象 HEAD+摘要回读的 UTC 时间戳 |
| `verificationSource` | string | 固定为 `heyue-release-storage` |

每个 `assets.{docker,python}` 描述至少包含 `key`、`name`、`size`、`sha256`：

- `key` 必须落在本版本 immutable 前缀内；不允许 `..`、绝对路径、反斜杠或前缀外对象。
- `name` 只能是 basename；Docker 使用 `heyue-docker-{version}-linux-amd64.tar.gz`，Python 使用 `heyue-python-{version}.tar.gz`。
- `size` 必须是正整数，并等于对象 HEAD 的 `Content-Length`。
- `sha256` 必须是 64 位小写十六进制，并等于对象下载/校验所得摘要。

`checksums` 的兼容形状如下：

```json
{
  "name": "SHA256SUMS",
  "key": "releases/heyue/v0.0.1/SHA256SUMS",
  "sha256": "<64位小写十六进制>",
  "entries": {
    "heyue-docker-0.0.1-linux-amd64.tar.gz": "<64位小写十六进制>",
    "heyue-python-0.0.1.tar.gz": "<64位小写十六进制>"
  }
}
```

`entries` 是便于匿名读取端快速校验的扩展；读取端仍应下载 `SHA256SUMS` 并按文件字节重新计算，不应仅信任 manifest 中的复制值。

### 2.2 latest 指针

stable 和 beta 各自拥有一个指针：

```text
releases/heyue/stable/latest.json
releases/heyue/beta/latest.json
```

fuwu 只有在同一版本所有对象上传成功、每个对象 HEAD+摘要回读通过后，才原子替换对应的 `latest.json`。指针只引用已存在的 immutable manifest，不复制资产、不删除历史版本。

指针最小形状：

```json
{
  "schemaVersion": 1,
  "project": "heyue",
  "channel": "stable",
  "version": "0.0.1",
  "tag": "v0.0.1",
  "revision": "<40位提交SHA>",
  "releaseId": 100102,
  "manifestKey": "releases/heyue/v0.0.1/manifest.json",
  "manifestSha256": "<manifest.json的64位摘要>",
  "publishedAt": "2026-10-19T00:00:00Z",
  "immutable": true,
  "verificationSource": "heyue-release-storage"
}
```

匿名 updater 的读取顺序为：GET `latest.json` → 校验指针字段及 `manifestSha256` → GET `manifest.json` → 校验 `revision`、资产 SHA256 和 `SHA256SUMS` → 将选定平台资产放入受保护 staging 目录。读取端不需要、也不应携带 AWS access key、secret、session token 或 fuwu 管理 API 凭据。

## 3. fuwu 后端发布边界

- 发布请求只能引用受保护的本机 staging/job 标识；请求体不得接收任意文件路径、S3 URL 或云凭据。
- 发布前校验 manifest、`SHA256SUMS`、版本/tag、40 位 revision、资产大小和 SHA256；同版本已有不同 digest 必须返回冲突，完全相同的 requestId/参数必须幂等。
- 每个对象写入后执行 HEAD 并回读摘要；任何一步失败都不能更新 latest。
- rollback 只把 latest 指针切换到已存在的 immutable manifest，不覆盖历史对象；必须超级管理员二次确认，并审计 before/after、actor、requestId、原因和时间。
- 审计记录只能保存对象 key、摘要、版本、阶段和公开 actor 标识，严禁保存 secret/token、Authorization 头、临时凭据或私有 staging 路径。

真实部署目标由项目配置提供，默认值为：bucket `heyue`、region `ap-southeast-1`、prefix `releases`。这些值不构成当前本地或 CI 已完成的 AWS 验收；真实 AWS 发布、匿名 HTTPS 读取和 Heyue updater 端到端升级必须单独记录。

## 4. 与现有 Heyue updater 的差异和迁移步骤

| 项目 | 当前 Heyue | fuwu v1 目标 |
| --- | --- | --- |
| 发现源 | GitHub Release API（认证读取） | S3 兼容对象存储的匿名 HTTPS `latest.json` |
| manifest | 单个 `asset` 对象 | `assets.docker`、`assets.python` 对象 |
| verificationSource | `github-release-api` | `heyue-release-storage` |
| latest | GitHub `releases/latest` | stable/beta 独立 latest 指针 |
| 上传方 | Heyue 发布脚本/CD | fuwu worker + S3 SigV4 provider |
| 凭据 | GitHub CLI/API 侧受保护读取 | 仅 fuwu 服务端受保护文件、环境或 IAM role |
| updater 端 | GitHub 下载后导入受保护 staging | opt-in 匿名 HTTPS provider；Docker/Python 均复用 SHA256、权限和 staging 校验 |

切换时在 updater 配置 `releaseSource=object-storage`、`releaseStorageBaseUrl=https://.../releases/heyue` 和 `releaseStorageChannel=stable|beta`，先做 dry-run/匿名 GET，再执行独立升级验收。默认 GitHub 路径不变。

## 5. 可执行验证命令

在 Heyue checkout 中，以下定向测试覆盖 GitHub 基线和匿名对象存储 provider：

```bash
cd /Users/shiyi/Documents/heyue
uv run pytest tests/test_release_storage_client.py tests/test_object_storage_updater_integration.py tests/test_docker_update_agent.py tests/test_python_update_agent.py -q
```

在 fuwu checkout 中，v1 fixture 契约测试不访问 AWS、不读取本机凭据：

```bash
cd /Users/shiyi/Documents/ChatGPT/fuwu
python -m unittest discover -s tests -p 'test_heyue_contract.py' -v
```

真实验收还需要额外记录：S3 bucket/region 权限、SigV4 上传结果、对象 HEAD 摘要、匿名 HTTPS GET、Heyue updater 实际 staging 与升级回读。CI 的本地单测通过不能替代这些外部条件。

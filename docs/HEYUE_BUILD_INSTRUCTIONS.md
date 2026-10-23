# Heyue 匿名安装与源码归档

Heyue 的全新主机安装器使用 Fuwu 的匿名 HTTPS 发行接口，不需要 GitHub 账号、PAT、Fuwu 管理会话或 AWS 对象存储凭据。

全新主机可以先读取固定的匿名 bootstrap，再由 bootstrap 完成发行链校验并启动已校验包内的安装器：

```sh
set -eu

bootstrap=$(mktemp)
headers=$(mktemp)
trap 'rm -f "$bootstrap" "$headers"' EXIT

curl --fail --silent --show-error --proto '=https' --tlsv1.2 \
  --dump-header "$headers" \
  --output "$bootstrap" \
  'https://fuwu.1213.cc/api/downloads/heyue/bootstrap.py'

expected=$(awk 'BEGIN { IGNORECASE=1 } /^X-Fuwu-SHA256:/ { gsub("\r", "", $2); print tolower($2) }' "$headers")
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$bootstrap" | awk '{print $1}')
else
  actual=$(shasum -a 256 "$bootstrap" | awk '{print $1}')
fi
test -n "$expected" && test "$expected" = "$actual"

exec python3 "$bootstrap"
```

响应头和脚本保存在本地临时文件中，只有 `X-Fuwu-SHA256` 与本机摘要一致才会执行；请求不跟随重定向。Python Bootstrap 会再次读取并校验固定的 `install.sh` 引导，然后由该引导匿名读取 stable latest、manifest、`SHA256SUMS` 和 Python 归档，完成摘要、身份和安全归档检查后，才执行归档内的 `scripts/install_heyue.sh`。调用安装器时会强制使用 Fuwu 的 `object-storage` 来源并复用已经校验的 Python 归档，禁止回退到 GitHub。兼容入口 `https://fuwu.1213.cc/api/downloads/heyue/bootstrap/install.sh` 仍可直接使用，但同样要求先保存并校验响应头摘要。

规范 base URL：

```text
https://fuwu.1213.cc/api/downloads/heyue
```

安装器按下面的顺序读取 stable 发行：

```text
GET {base}/stable/latest.json
GET {base}/{version}/manifest.json
GET {base}/{version}/SHA256SUMS
GET {base}/{version}/heyue-python-{version}.tar.gz
```

`latest.json` 中的 `manifestKey` 使用对象存储形式，例如 `releases/heyue/v1.0.4/manifest.json`。Fuwu API 的版本路径使用数字版本 `1.0.4`；安装器应根据 `manifestKey` 去掉版本段的单个 `v` 后访问对应路径。`/releases/{storage-key}` 兼容入口也可以直接使用 manifest 和资产中的对象 key。

安装器必须在执行任何解包或安装动作前完成以下校验：

- `latest.json` 的 `project=heyue`、`channel=stable`、`tag=v<version>`、40 位 `revision`、`immutable=true` 和 `verificationSource=heyue-release-storage`。
- `manifestKey` 必须属于 `releases/heyue/v<version>/manifest.json`，并且 manifest 原始字节的 SHA256 必须等于 `manifestSha256`。
- manifest 的 `repository=shiyi-log/heyue`、`version`、`tag`、`revision`、`channel`、`draft=false`、`prerelease=false` 必须与 stable 指针一致。
- `checksums.key` 必须属于同一个版本目录并且文件名为 `SHA256SUMS`；文件摘要和条目必须与 manifest 的 `checksums` 一致。
- Python 资产必须来自 manifest 的 `assets.python`，其路径、大小和 SHA256 必须同时匹配 manifest 与 `SHA256SUMS`。
- 归档解包必须拒绝绝对路径、`..`、反斜杠、重复成员、符号链接、硬链接和设备文件；解包后只执行已校验归档中的 `scripts/install_heyue.sh`。

stable 指针和版本化对象的缓存约定如下：`latest.json` 使用 `Cache-Control: no-store, max-age=0`；manifest、`SHA256SUMS` 和版本化 Python 归档使用 `public, max-age=31536000, immutable`，并返回 `X-Fuwu-SHA256`。安装器每次检查更新都必须重新读取 `latest.json`，不能把 stable 指针当作不可变对象缓存。

下载接口只提供匿名读取。安装器不得向这些 URL 添加 `Authorization`，也不得把凭据放入 URL、命令行参数、归档或日志。网络下载完成并通过上述校验后，才可以在本机临时目录中运行归档内的安装器；不要直接执行未经校验的网络 shell 响应。

如果 Fuwu 的 latest、manifest、SHA256SUMS 或 Python 归档读取或校验失败，安装器必须直接失败并保留安全错误信息，不得回退到 GitHub 或其他未登记下载源。

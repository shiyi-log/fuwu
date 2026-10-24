#!/usr/bin/env bash
# Anonymous Heyue bootstrap. It verifies the Fuwu release chain before running
# the installer contained in the verified Python package.
set -euo pipefail
umask 077

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'Heyue bootstrap requires python3.' >&2
  exit 1
fi

bootstrap_python="$(mktemp "${TMPDIR:-/tmp}/heyue-bootstrap.XXXXXX.py")"
trap 'rm -f -- "$bootstrap_python"' EXIT

cat >"$bootstrap_python" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


BASE_DEFAULT = "https://fuwu.1213.cc/api/downloads/heyue"
PROJECT = "heyue"
REPOSITORY = "shiyi-log/heyue"
PREFIX = "releases/heyue"
MAX_METADATA = 2 * 1024 * 1024
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_MEMBERS = 20000
MAX_EXPANDED = 768 * 1024 * 1024
VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
HEX64_RE = re.compile(r"[0-9a-f]{64}\Z")
ASSET_RE = re.compile(r"heyue-python-(?P<version>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\.tar\.gz\Z")


class BootstrapError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise BootstrapError(message)


def base_url() -> str:
    configured = os.environ.get("FUWU_BASE")
    if configured and configured.rstrip("/") != BASE_DEFAULT and os.environ.get("FUWU_BOOTSTRAP_ALLOW_CUSTOM_BASE") != "1":
        fail("custom FUWU_BASE requires explicit test opt-in.")
    value = (configured or BASE_DEFAULT).rstrip("/")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        fail("FUWU_BASE must be an HTTPS Fuwu Heyue download prefix.")
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or port not in {None, 443}
            or parsed.path.rstrip("/") != "/api/downloads/heyue"):
        fail("FUWU_BASE must be an HTTPS Fuwu Heyue download prefix.")
    return value


def opener():
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, file, code, message, headers, newurl):
            return None
    return build_opener(ProxyHandler({}), NoRedirect())


def get_bytes(source: str, *, limit: int) -> bytes:
    try:
        request = Request(source, headers={"Accept": "application/octet-stream", "User-Agent": "heyue-fuwu-bootstrap"})
        with opener().open(request, timeout=45) as response:
            if response.status != 200:
                fail("Fuwu release fetch failed.")
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    if int(content_length) < 0 or int(content_length) > limit:
                        fail("Fuwu release metadata is too large.")
                except ValueError:
                    fail("Fuwu release response is invalid.")
            result = response.read(limit + 1)
    except BootstrapError:
        raise
    except (HTTPError, OSError, URLError, ValueError):
        fail("Fuwu release fetch failed.")
    if len(result) > limit:
        fail("Fuwu release metadata is too large.")
    reported = response.headers.get("X-Fuwu-SHA256", "").lower()
    if not HEX64_RE.fullmatch(reported) or reported != hashlib.sha256(result).hexdigest():
        fail("Fuwu release response digest does not match.")
    if content_length is not None and int(content_length) != len(result):
        fail("Fuwu release response size does not match.")
    return result


def get_json(source: str) -> tuple[dict, bytes]:
    body = get_bytes(source, limit=MAX_METADATA)
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        fail("Fuwu release metadata is invalid.")
    if not isinstance(value, dict):
        fail("Fuwu release metadata is invalid.")
    return value, body


def version(value: object) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        fail("Fuwu release version is invalid.")
    return value


def release_id(value: object) -> None:
    if (isinstance(value, int) and not isinstance(value, bool) and value > 0) or (
        isinstance(value, str) and bool(value.strip())
    ):
        return
    fail("Fuwu release identity is invalid.")


def object_url(base: str, key: object, *, expected: str, version_value: str) -> str:
    if not isinstance(key, str) or key != expected or "\\" in key or any(
        part in {"", ".", ".."} for part in key.split("/")
    ):
        fail("Fuwu release object key is invalid.")
    name = key.rsplit("/", 1)[-1]
    if key != expected or not name:
        fail("Fuwu release object key is invalid.")
    return f"{base}/{quote(version_value, safe='')}/{quote(name, safe='')}"


def validate_latest(pointer: dict) -> str:
    required = {
        "schemaVersion", "project", "channel", "version", "tag", "revision", "releaseId",
        "manifestKey", "manifestSha256", "publishedAt", "immutable", "verificationSource",
    }
    if set(pointer) != required:
        fail("Fuwu latest pointer is invalid.")
    current = version(pointer["version"])
    if (pointer["schemaVersion"] != 1 or pointer["project"] != PROJECT
            or pointer["channel"] != "stable" or pointer["tag"] != f"v{current}"
            or not isinstance(pointer["revision"], str) or not SHA_RE.fullmatch(pointer["revision"])
            or not isinstance(pointer["manifestSha256"], str) or not HEX64_RE.fullmatch(pointer["manifestSha256"])
            or pointer["immutable"] is not True or pointer["verificationSource"] != "heyue-release-storage"):
        fail("Fuwu latest pointer is invalid.")
    release_id(pointer["releaseId"])
    if not isinstance(pointer["publishedAt"], str) or not pointer["publishedAt"].endswith("Z"):
        fail("Fuwu latest pointer is invalid.")
    expected_key = f"{PREFIX}/v{current}/manifest.json"
    if pointer["manifestKey"] != expected_key:
        fail("Fuwu latest manifest key is invalid.")
    return current


def validate_manifest(manifest: dict, *, current: str) -> tuple[dict, str]:
    required = {
        "schemaVersion", "project", "repository", "version", "tag", "revision", "releaseId",
        "publishedAt", "draft", "prerelease", "channel", "assets", "checksums", "verifiedAt",
        "verificationSource",
    }
    if set(manifest) != required:
        fail("Fuwu manifest is invalid.")
    if (manifest["schemaVersion"] != 1 or manifest["project"] != PROJECT
            or manifest["repository"] != REPOSITORY or manifest["version"] != current
            or manifest["tag"] != f"v{current}" or not isinstance(manifest["revision"], str)
            or not SHA_RE.fullmatch(manifest["revision"]) or manifest["draft"] is not False
            or manifest["prerelease"] is not False or manifest["channel"] != "stable"
            or manifest["verificationSource"] != "heyue-release-storage"):
        fail("Fuwu manifest is invalid.")
    release_id(manifest["releaseId"])
    for name in ("publishedAt", "verifiedAt"):
        if not isinstance(manifest[name], str) or not manifest[name].endswith("Z"):
            fail("Fuwu manifest is invalid.")
    assets = manifest["assets"]
    if not isinstance(assets, dict) or not set(assets).issubset({"docker", "python"}) or "python" not in assets:
        fail("Fuwu manifest assets are invalid.")
    python_asset = assets["python"]
    if isinstance(python_asset, list):
        if len(python_asset) != 1:
            fail("Fuwu Python asset is invalid.")
        python_asset = python_asset[0]
    if not isinstance(python_asset, dict) or not {"key", "name", "size", "sha256"}.issubset(python_asset):
        fail("Fuwu Python asset is invalid.")
    name = python_asset["name"]
    if (not isinstance(name, str) or name != f"heyue-python-{current}.tar.gz"
            or not isinstance(python_asset["size"], int) or isinstance(python_asset["size"], bool)
            or not 0 < python_asset["size"] <= MAX_ARCHIVE
            or not isinstance(python_asset["sha256"], str) or not HEX64_RE.fullmatch(python_asset["sha256"])):
        fail("Fuwu Python asset is invalid.")
    expected_key = f"{PREFIX}/v{current}/{name}"
    object_url(base_url(), python_asset["key"], expected=expected_key, version_value=current)
    checksums = manifest["checksums"]
    if (not isinstance(checksums, dict) or checksums.get("name") != "SHA256SUMS"
            or not isinstance(checksums.get("key"), str)
            or not isinstance(checksums.get("sha256"), str)
            or not HEX64_RE.fullmatch(checksums["sha256"])):
        fail("Fuwu checksum record is invalid.")
    expected_checksums_key = f"{PREFIX}/v{current}/SHA256SUMS"
    object_url(base_url(), checksums["key"], expected=expected_checksums_key, version_value=current)
    entries = checksums.get("entries")
    expected_names = {python_asset["name"]}
    docker_asset = assets.get("docker")
    if isinstance(docker_asset, list):
        if len(docker_asset) != 1 or not isinstance(docker_asset[0], dict):
            fail("Fuwu Docker asset is invalid.")
        docker_asset = docker_asset[0]
    if docker_asset is not None:
        if (not isinstance(docker_asset, dict) or not isinstance(docker_asset.get("name"), str)
                or not isinstance(docker_asset.get("size"), int) or isinstance(docker_asset.get("size"), bool)
                or docker_asset["size"] <= 0 or not isinstance(docker_asset.get("sha256"), str)
                or not HEX64_RE.fullmatch(docker_asset["sha256"])):
            fail("Fuwu Docker asset is invalid.")
        docker_name = docker_asset["name"]
        docker_key = f"{PREFIX}/v{current}/{docker_name}"
        object_url(base_url(), docker_asset.get("key"), expected=docker_key, version_value=current)
        expected_names.add(docker_name)
    if not isinstance(entries, dict) or set(entries) != expected_names:
        fail("Fuwu checksum record is invalid.")
    expected_digests = {python_asset["name"]: python_asset["sha256"]}
    if docker_asset is not None:
        expected_digests[docker_asset["name"]] = docker_asset["sha256"]
    if any(not isinstance(value, str) or not HEX64_RE.fullmatch(value) for value in entries.values()) or entries != expected_digests:
        fail("Fuwu checksum record is invalid.")
    return python_asset, checksums


def validate_pointer_binding(pointer: dict, manifest: dict, *, current: str) -> None:
    """Ensure the immutable manifest is the exact release named by latest."""
    for field in ("revision", "releaseId", "publishedAt"):
        if manifest.get(field) != pointer.get(field):
            fail(f"Fuwu latest {field} does not match the manifest.")
    if manifest.get("version") != current or manifest.get("tag") != f"v{current}":
        fail("Fuwu latest release identity does not match the manifest.")


def parse_checksums(body: bytes, expected: str, expected_entries: dict[str, str]) -> None:
    if hashlib.sha256(body).hexdigest() != expected:
        fail("Fuwu checksum file digest does not match.")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        fail("Fuwu checksum file is invalid.")
    entries: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if not match or match[2] in entries:
            fail("Fuwu checksum file is invalid.")
        entries[match[2]] = match[1]
    if entries != expected_entries:
        fail("Fuwu checksum file does not match the manifest.")


def download_asset(url: str, destination: Path, *, expected_size: int, expected_digest: str) -> None:
    digest = hashlib.sha256()
    received = 0
    try:
        request = Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "heyue-fuwu-bootstrap"})
        with opener().open(request, timeout=90) as response:
            if response.status != 200:
                fail("Fuwu Python asset download failed.")
            content_length = response.headers.get("Content-Length")
            if content_length is None:
                fail("Fuwu Python asset response is missing its size.")
            try:
                if int(content_length) != expected_size:
                    fail("Fuwu Python asset response size does not match.")
            except ValueError:
                fail("Fuwu Python asset response is invalid.")
            reported = response.headers.get("X-Fuwu-SHA256", "").lower()
            if reported != expected_digest or not HEX64_RE.fullmatch(reported):
                fail("Fuwu Python asset response digest does not match.")
            with destination.open("xb") as output:
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > expected_size or received > MAX_ARCHIVE:
                        fail("Fuwu Python asset is too large.")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
    except BootstrapError:
        destination.unlink(missing_ok=True)
        raise
    except (HTTPError, OSError, URLError, ValueError):
        destination.unlink(missing_ok=True)
        fail("Fuwu Python asset download failed.")
    if received != expected_size or digest.hexdigest() != expected_digest:
        destination.unlink(missing_ok=True)
        fail("Fuwu Python asset digest does not match.")


def extract_archive(archive: Path, destination: Path, *, current: str, revision: str) -> Path:
    expanded = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as package:
            members = package.getmembers()
            if not members or len(members) > MAX_MEMBERS:
                fail("Fuwu Python archive is invalid.")
            for member in members:
                name = member.name.rstrip("/")
                relative = PurePosixPath(name)
                if (not name or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts)
                        or "\\" in name or any(ord(char) < 32 for char in name)):
                    fail("Fuwu Python archive path is invalid.")
                if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                    fail("Fuwu Python archive contains an unsafe member.")
                if name in seen:
                    fail("Fuwu Python archive contains duplicate members.")
                seen.add(name)
                expanded += max(member.size, 0)
                if member.size < 0 or expanded > MAX_EXPANDED:
                    fail("Fuwu Python archive is too large.")
            destination.mkdir(mode=0o700)
            for member in members:
                name = member.name.rstrip("/")
                target = destination.joinpath(*PurePosixPath(name).parts)
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    source = package.extractfile(member)
                    if source is None:
                        fail("Fuwu Python archive member is unreadable.")
                    with source:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    os.fchmod(output.fileno(), 0o644)
    except BootstrapError:
        raise
    except (OSError, tarfile.TarError):
        fail("Fuwu Python archive extraction failed.")
    installer = destination / "scripts" / "install_heyue.sh"
    helper = destination / "scripts" / "heyue_installer.py"
    if installer.is_symlink() or helper.is_symlink() or not installer.is_file() or not helper.is_file():
        fail("Fuwu Python archive is missing the installer.")
    try:
        if (destination / "VERSION").read_text(encoding="utf-8").strip() != current:
            fail("Fuwu Python archive version does not match.")
        release = json.loads((destination / "python-release.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        fail("Fuwu Python archive metadata is invalid.")
    if (not isinstance(release, dict) or release.get("schemaVersion") != 1
            or release.get("version") != current or release.get("revision") != revision):
        fail("Fuwu Python archive metadata does not match.")
    return installer


def main() -> int:
    try:
        base = base_url()
        pointer, pointer_body = get_json(f"{base}/stable/latest.json")
        current = validate_latest(pointer)
        manifest_url = object_url(base, pointer["manifestKey"], expected=f"{PREFIX}/v{current}/manifest.json", version_value=current)
        manifest, manifest_body = get_json(manifest_url)
        if hashlib.sha256(manifest_body).hexdigest() != pointer["manifestSha256"]:
            fail("Fuwu manifest digest does not match latest.")
        python_asset, checksums = validate_manifest(manifest, current=current)
        validate_pointer_binding(pointer, manifest, current=current)
        sums_url = object_url(base, checksums["key"], expected=f"{PREFIX}/v{current}/SHA256SUMS", version_value=current)
        sums_body = get_bytes(sums_url, limit=MAX_METADATA)
        parse_checksums(sums_body, checksums["sha256"], checksums["entries"])
        with tempfile.TemporaryDirectory(prefix="heyue-fuwu-bootstrap-") as temporary:
            root = Path(temporary)
            archive = root / python_asset["name"]
            asset_url = object_url(base, python_asset["key"], expected=f"{PREFIX}/v{current}/{python_asset['name']}", version_value=current)
            download_asset(asset_url, archive, expected_size=python_asset["size"], expected_digest=python_asset["sha256"])
            extracted = root / "package"
            installer = extract_archive(archive, extracted, current=current, revision=pointer["revision"])
            command = [
                "/bin/bash", str(installer), *sys.argv[1:],
                "--interactive",
                "--tag", f"v{current}",
                "--revision", pointer["revision"],
                "--release-source", "object-storage",
                "--release-storage-base-url", base,
                "--release-storage-channel", "stable",
                "--preverified-python-archive", str(archive),
                "--preverified-python-archive-version", current,
            ]
            return subprocess.run(command, check=False).returncode
    except BootstrapError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
PY

python3 "$bootstrap_python" "$@"

"""Fuwu release discovery and the fixed-contract online updater.

The public API only accepts a validated release tag.  Installation runs in a
detached transient systemd unit so restarting ``fuwu-server.service`` cannot
kill the worker that is responsible for rollback and durable status updates.
"""

from __future__ import annotations

import datetime as dt
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import ReleaseError
from .validation import validate_version
from .version import __version__


SYSTEM_UPDATE_REPOSITORY = "shiyi-log/fuwu"
SYSTEM_UPDATE_LATEST_URL = (
    "https://api.github.com/repos/shiyi-log/fuwu/releases/latest"
)
SYSTEM_UPDATE_TIMEOUT = 10.0
SYSTEM_UPDATE_RESPONSE_MAX_BYTES = 1_000_000
SYSTEM_UPDATE_NOTES_MAX_CHARS = 8_192
SYSTEM_UPDATE_ASSET_MAX_COUNT = 64
SYSTEM_UPDATE_ASSET_NAME_MAX_CHARS = 256
SYSTEM_UPDATE_ASSET_SIZE_MAX_BYTES = 1 << 40
SYSTEM_UPDATE_DIGEST_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
SYSTEM_UPDATE_ASSET_NAME_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$"
)
SYSTEM_UPDATE_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SYSTEM_UPDATE_REVISION_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
SYSTEM_UPDATE_JOB_RE = re.compile(r"^[0-9a-f]{32}$")
SYSTEM_UPDATE_MAX_ARCHIVE_BYTES = 1 << 30
SYSTEM_UPDATE_MAX_EXTRACTED_BYTES = 2 << 30
SYSTEM_UPDATE_HEALTH_TIMEOUT = 60.0
SYSTEM_UPDATE_HEALTH_INTERVAL = 1.0
SYSTEM_UPDATE_REQUIRED_FILES = {
    "fuwu-update.json", ".python-version", "manage.py", "pyproject.toml", "uv.lock",
}
SYSTEM_UPDATE_REQUIRED_DIRS = {"config", "fuwu_service", "release_api", "frontend/apps/web-antd/dist"}


def _install_root() -> Path:
    configured = os.environ.get("FUWU_INSTALL_ROOT")
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            raise ReleaseError("system_update_install_invalid", "install root must be absolute", 503)
        return root.resolve()
    return Path(__file__).resolve().parents[1]


def _state_path() -> Path:
    return _install_root() / "data" / "system-update.json"


def _lock_path() -> Path:
    return _install_root() / "data" / "system-update.lock"


def _job_response(job: Mapping[str, Any]) -> dict[str, Any]:
    fields = ("jobId", "status", "progress", "message", "errorCode", "fromVersion", "toVersion", "startedAt", "updatedAt")
    return {key: job.get(key) for key in fields}


def _open_state_lock() -> Any:
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ReleaseError("system_update_state_unavailable", "update status is unavailable", 503)
    handle = os.fdopen(descriptor, "a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def _read_state_unlocked() -> dict[str, Any]:
    path = _state_path()
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ReleaseError("system_update_state_unavailable", "update status is unavailable", 503)
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"activeJobId": None, "jobs": {}}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("system_update_state_unavailable", "update status is unavailable", 503) from None
    if not isinstance(value, dict) or not isinstance(value.get("jobs", {}), dict):
        raise ReleaseError("system_update_state_unavailable", "update status is unavailable", 503)
    return value


def _write_state_unlocked(value: Mapping[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="system-update-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def validate_system_update_tag(value: Any) -> str:
    if not isinstance(value, str) or not SYSTEM_UPDATE_TAG_RE.fullmatch(value):
        raise ReleaseError("invalid_system_update_request", "update tag is invalid", 422)
    try:
        validate_version(value[1:])
    except ReleaseError:
        raise ReleaseError("invalid_system_update_request", "update tag is invalid", 422) from None
    return value


def get_system_update_job(job_id: str) -> dict[str, Any]:
    if not SYSTEM_UPDATE_JOB_RE.fullmatch(job_id):
        raise ReleaseError("system_update_job_not_found", "update job was not found", 404)
    with _open_state_lock() as lock:
        state = _read_state_unlocked()
        changed = _reconcile_active_job_unlocked(state)
        job = state.get("jobs", {}).get(job_id)
        if changed:
            _write_state_unlocked(state)
    if not isinstance(job, Mapping):
        raise ReleaseError("system_update_job_not_found", "update job was not found", 404)
    return _job_response(job)


def _set_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _open_state_lock() as lock:
        state = _read_state_unlocked()
        jobs = state.setdefault("jobs", {})
        job = jobs.get(job_id)
        if not isinstance(job, dict):
            raise ReleaseError("system_update_job_not_found", "update job was not found", 404)
        job.update(changes)
        job["updatedAt"] = _now()
        _write_state_unlocked(state)
        return _job_response(job)


def _claim_worker(job_id: str) -> bool:
    with _open_state_lock() as lock:
        state = _read_state_unlocked()
        job = state.get("jobs", {}).get(job_id)
        if state.get("activeJobId") != job_id or not isinstance(job, dict) or job.get("status") != "queued":
            return False
        job.update({"status": "running", "progress": 1, "message": "starting update worker",
                    "startedAt": _now(), "updatedAt": _now()})
        _write_state_unlocked(state)
        return True


def queue_system_update(tag: str, *, from_version: str) -> dict[str, Any]:
    tag = validate_system_update_tag(tag)
    current = validate_version(from_version)
    target = tag[1:]
    if _version_tuple(target) <= _version_tuple(current):
        raise ReleaseError("system_update_downgrade", "the requested release is not newer", 409)
    job_id = secrets.token_hex(16)
    now = _now()
    job = {
        "jobId": job_id, "status": "queued", "progress": 0,
        "message": "update queued", "errorCode": None,
        "fromVersion": current, "toVersion": target,
        "startedAt": None, "updatedAt": now,
    }
    try:
        lock = _open_state_lock()
    except OSError:
        raise ReleaseError("system_update_state_unavailable", "update status is unavailable", 503) from None
    with lock:
        state = _read_state_unlocked()
        _reconcile_active_job_unlocked(state)
        active_id = state.get("activeJobId")
        active = state.get("jobs", {}).get(active_id) if active_id else None
        if isinstance(active, Mapping) and active.get("status") in {"queued", "running"}:
            raise ReleaseError("system_update_in_progress", "another update is already running", 409)
        state.setdefault("jobs", {})[job_id] = job
        state["activeJobId"] = job_id
        _write_state_unlocked(state)
        # Launch while holding the state lock so a second request cannot claim
        # the just-created job before its transient unit has been submitted.
        try:
            _launch_worker(job_id, tag)
        except Exception:
            job.update({
                "status": "failed",
                "progress": 0,
                "message": "update worker could not be started",
                "errorCode": "system_update_worker_unavailable",
                "updatedAt": _now(),
            })
            state["activeJobId"] = None
            _write_state_unlocked(state)
            raise ReleaseError("system_update_worker_unavailable", "update worker is unavailable", 503) from None
    return _job_response(job)


def _reconcile_active_job_unlocked(state: dict[str, Any]) -> bool:
    """Fail a queued/running job whose transient worker is explicitly gone."""
    active_id = state.get("activeJobId")
    active = state.get("jobs", {}).get(active_id) if active_id else None
    if not isinstance(active, dict) or active.get("status") not in {"queued", "running"}:
        return False
    unit = f"fuwu-update-{active_id}.service"
    systemctl = os.environ.get("FUWU_SYSTEMCTL_BIN") or shutil.which("systemctl") or "systemctl"
    try:
        check = subprocess.run(
            [systemctl, "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
        unit_state = check.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    if unit_state not in {"inactive", "failed", "dead"}:
        return False
    if active.get("status") == "queued":
        try:
            updated = dt.datetime.fromisoformat(str(active.get("updatedAt", "")).replace("Z", "+00:00"))
        except ValueError:
            updated = dt.datetime.now(dt.timezone.utc)
        if (dt.datetime.now(dt.timezone.utc) - updated).total_seconds() < 30:
            return False
    active.update({
        "status": "failed",
        "message": "update worker stopped unexpectedly",
        "errorCode": "system_update_worker_lost",
        "updatedAt": _now(),
    })
    state["activeJobId"] = None
    return True


def _launch_worker(job_id: str, tag: str) -> None:
    if not SYSTEM_UPDATE_JOB_RE.fullmatch(job_id):
        raise ValueError("invalid job id")
    tag = validate_system_update_tag(tag)
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    uv = shutil.which("uv")
    if not systemd_run or not systemctl or not uv:
        raise OSError("required systemd or uv command is unavailable")
    root = _install_root()
    database = Path(os.environ.get("FUWU_DATABASE", str(root / "fuwu.sqlite3"))).expanduser()
    if not database.is_absolute():
        raise OSError("FUWU_DATABASE must be absolute")
    port = os.environ.get("FUWU_PORT", "8088")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise OSError("FUWU_PORT is invalid")
    unit = f"fuwu-update-{job_id}"
    current_release = root / "current"
    command = [
        systemd_run, f"--unit={unit}", "--collect", "--no-block",
        f"--property=WorkingDirectory={current_release}",
        f"--setenv=FUWU_INSTALL_ROOT={root}", f"--setenv=FUWU_DATABASE={database}",
        f"--setenv=FUWU_PORT={port}", f"--setenv=FUWU_UV_BIN={uv}",
        f"--setenv=FUWU_SYSTEMCTL_BIN={systemctl}", "--service-type=oneshot", "--",
        sys.executable, "-m", "fuwu_service.system_update", "--worker", job_id, tag,
    ]
    subprocess.run(command, cwd=str(current_release), check=True, timeout=10,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class _DownloadRedirectHandler(urllib.request.HTTPRedirectHandler):
    _hosts = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com", "githubusercontent.com"}

    def redirect_request(self, request, file, code, message, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "").lower().rstrip(".") not in self._hosts:
            raise ReleaseError("system_update_redirected", "update asset redirected unexpectedly", 502)
        return super().redirect_request(request, file, code, message, headers, newurl)


def _download(url: str, destination: Path, *, max_bytes: int) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "api.github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}:
        raise ReleaseError("system_update_asset_invalid", "update asset URL is invalid", 502)
    request = urllib.request.Request(url, headers={"User-Agent": "fuwu-system-updater", "Accept": "application/octet-stream"})
    opener = urllib.request.build_opener(_DownloadRedirectHandler())
    total = 0
    try:
        with opener.open(request, timeout=SYSTEM_UPDATE_TIMEOUT) as response, destination.open("wb") as stream:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise ReleaseError("system_update_asset_too_large", "update asset is too large", 502)
                stream.write(block)
    except ReleaseError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError):
        raise ReleaseError("system_update_asset_unavailable", "update asset is unavailable", 502) from None


def _release_metadata(tag: str) -> Mapping[str, Any]:
    tag = validate_system_update_tag(tag)
    url = f"https://api.github.com/repos/{SYSTEM_UPDATE_REPOSITORY}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "fuwu-system-updater", "X-GitHub-Api-Version": "2022-11-28"})
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=SYSTEM_UPDATE_TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                raise ReleaseError("system_update_unavailable", "release metadata is unavailable", 502)
            payload = json.loads(_read_response(response).decode("utf-8"))
    except ReleaseError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("system_update_unavailable", "release metadata is unavailable", 502) from None
    if not isinstance(payload, Mapping) or payload.get("tag_name") != tag or payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ReleaseError("system_update_invalid_release", "release metadata is invalid", 502)
    return payload


def _safe_archive_member(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError("system_update_archive_invalid", "update archive contains an unsafe path", 422)
    return path


def _extract_archive(archive_path: Path, destination: Path, *, expected: Mapping[str, Any]) -> None:
    extracted = 0
    seen: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                relative = _safe_archive_member(member.name)
                name = relative.as_posix()
                if name in seen or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ReleaseError("system_update_archive_invalid", "update archive contains unsafe entries", 422)
                seen.add(name)
                if member.size < 0:
                    raise ReleaseError("system_update_archive_invalid", "update archive contains unsafe entries", 422)
                extracted += member.size
                if extracted > SYSTEM_UPDATE_MAX_EXTRACTED_BYTES:
                    raise ReleaseError("system_update_archive_too_large", "update archive is too large", 422)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                else:
                    source = archive.extractfile(member)
                    if source is None:
                        raise ReleaseError("system_update_archive_invalid", "update archive is invalid", 422)
                    with target.open("wb") as stream:
                        shutil.copyfileobj(source, stream)
        if (not SYSTEM_UPDATE_REQUIRED_FILES.issubset(seen)
                or not {"config", "fuwu_service", "release_api"}.issubset(seen)
                or "frontend/apps/web-antd/dist/index.html" not in seen):
            raise ReleaseError("system_update_archive_incomplete", "update archive is incomplete", 422)
        try:
            metadata = json.loads((destination / "fuwu-update.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("system_update_manifest_invalid", "update manifest is invalid", 422) from None
        if not isinstance(metadata, Mapping) or metadata.get("formatVersion") != 1 or metadata.get("project") != "fuwu" or metadata.get("version") != expected.get("version") or metadata.get("tag") != expected.get("tag") or not isinstance(metadata.get("revision"), str) or not SYSTEM_UPDATE_REVISION_RE.fullmatch(metadata["revision"]):
            raise ReleaseError("system_update_manifest_invalid", "update manifest is invalid", 422)
    except tarfile.TarError:
        raise ReleaseError("system_update_archive_invalid", "update archive is invalid", 422) from None


def _sqlite_backup(install_root: Path, job_id: str) -> Path:
    database = Path(os.environ.get("FUWU_DATABASE", str(install_root / "fuwu.sqlite3"))).expanduser()
    if not database.is_absolute():
        raise ReleaseError("system_update_database_invalid", "database path is invalid", 503)
    backup = install_root / "data" / "backups" / f"fuwu-{job_id}.sqlite3"
    backup_created = False
    try:
        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = backup.parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
            raise OSError("backup directory is not private")
        descriptor = os.open(
            backup,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
        backup_created = True
        os.chmod(backup, 0o600)
        source = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
            result = target.execute("PRAGMA integrity_check").fetchone()
        finally:
            target.close()
            source.close()
    except (OSError, sqlite3.Error):
        if backup_created:
            with contextlib.suppress(FileNotFoundError):
                backup.unlink()
        raise ReleaseError("system_update_database_backup_failed", "database backup failed", 503) from None
    if not result or result[0] != "ok":
        with contextlib.suppress(FileNotFoundError):
            backup.unlink()
        raise ReleaseError("system_update_database_backup_failed", "database integrity check failed", 503)
    return backup


def _restart_and_health(expected_version: str) -> None:
    subprocess.run([_systemctl_bin(), "restart", "fuwu-server.service"], check=True, timeout=30)
    deadline = time.monotonic() + SYSTEM_UPDATE_HEALTH_TIMEOUT
    port = _health_port()
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fuwu-system-updater"})
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.loads(response.read(16_384).decode("utf-8"))
            if (response.status == 200 and isinstance(body, Mapping)
                    and body.get("status") == "ok" and body.get("version") == expected_version):
                return
        except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            pass
        time.sleep(SYSTEM_UPDATE_HEALTH_INTERVAL)
    raise ReleaseError("system_update_health_failed", "updated service health check failed", 503)


def run_system_update_worker(job_id: str, tag: str) -> None:
    tag = validate_system_update_tag(tag)
    if not SYSTEM_UPDATE_JOB_RE.fullmatch(job_id):
        raise ReleaseError("system_update_job_not_found", "update job was not found", 404)
    if not _claim_worker(job_id):
        return
    root = _install_root()
    old_target: str | None = None
    release_path = root / "releases" / tag
    staging_path = root / "releases" / f".staging-{job_id}"
    switched_current = False
    try:
        if os.geteuid() != 0:
            raise ReleaseError("system_update_privilege_required", "update worker is unavailable", 503)
        old_target, old_version = _preflight_install(root)
        if old_version != __version__:
            raise ReleaseError("system_update_install_invalid", "running release does not match the active release", 503)
        active = subprocess.run([_systemctl_bin(), "is-active", "fuwu-server.service"], check=False, capture_output=True, text=True, timeout=5)
        if active.stdout.strip() != "active":
            raise ReleaseError("system_update_install_invalid", "managed service is not active", 503)
        _set_job(job_id, progress=5, message="checking release", errorCode=None)
        latest = check_system_update(__version__)
        if latest.get("tag") != tag:
            raise ReleaseError("system_update_not_latest", "requested release is not the current stable release", 409)
        metadata = _release_metadata(tag)
        version = tag[1:]
        archive_name = f"fuwu-update-{version}.tar.gz"
        sums_name = "SHA256SUMS"
        assets = metadata.get("assets")
        if not isinstance(assets, list):
            raise ReleaseError("system_update_asset_invalid", "update release assets are invalid", 502)
        by_name = {item.get("name"): item for item in assets if isinstance(item, Mapping)}
        if len(by_name) != len([item for item in assets if isinstance(item, Mapping)]) or archive_name not in by_name or sums_name not in by_name:
            raise ReleaseError("system_update_asset_missing", "required update assets are missing", 502)
        for required_name in (archive_name, sums_name):
            asset = by_name[required_name]
            size = asset.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
                raise ReleaseError("system_update_asset_invalid", "update asset metadata is invalid", 502)
            asset["_validated_size"] = size
        asset_url = f"https://github.com/{SYSTEM_UPDATE_REPOSITORY}/releases/download/{urllib.parse.quote(tag, safe='')}/{archive_name}"
        sums_url = f"https://github.com/{SYSTEM_UPDATE_REPOSITORY}/releases/download/{urllib.parse.quote(tag, safe='')}/{sums_name}"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f"fuwu-update-{job_id}-", dir=root) as temporary:
            temporary_path = Path(temporary)
            archive_path = temporary_path / archive_name
            sums_path = temporary_path / sums_name
            _set_job(job_id, progress=15, message="downloading release")
            _download(asset_url, archive_path, max_bytes=SYSTEM_UPDATE_MAX_ARCHIVE_BYTES)
            _download(sums_url, sums_path, max_bytes=1 << 20)
            if archive_path.stat().st_size != by_name[archive_name]["_validated_size"] or sums_path.stat().st_size != by_name[sums_name]["_validated_size"]:
                raise ReleaseError("system_update_asset_invalid", "downloaded asset size did not match release metadata", 422)
            hasher = hashlib.sha256()
            with archive_path.open("rb") as archive_stream:
                for block in iter(lambda: archive_stream.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
            asset_digest = by_name[archive_name].get("digest")
            if asset_digest is not None and asset_digest != f"sha256:{digest}":
                raise ReleaseError("system_update_checksum_failed", "update checksum verification failed", 422)
            lines = sums_path.read_text(encoding="ascii").splitlines()
            matches = [parts for line in lines if len(parts := line.split()) == 2 and parts[1].lstrip("*") == archive_name]
            if len(matches) != 1 or not re.fullmatch(r"[0-9a-fA-F]{64}", matches[0][0]) or matches[0][0].lower() != digest:
                raise ReleaseError("system_update_checksum_failed", "update checksum verification failed", 422)
            _set_job(job_id, progress=30, message="validating release archive")
            staging_path.mkdir(parents=True, exist_ok=False)
            expected = {"version": version, "tag": tag}
            _extract_archive(archive_path, staging_path, expected=expected)
            if release_path.exists() or release_path.is_symlink():
                raise ReleaseError("system_update_release_exists", "release directory already exists", 409)
            _sqlite_backup(root, job_id)
            _set_job(job_id, progress=45, message="installing dependencies")
            uv = _uv_bin()
            python_base = getattr(sys, "_base_executable", None)
            if not uv or not python_base or not Path(python_base).is_absolute() or not Path(python_base).exists():
                raise ReleaseError("system_update_runtime_unavailable", "update runtime is unavailable", 503)
            staging_path.rename(release_path)
            # Run uv against the final path.  Running it in ``.staging-*``
            # would leave editable package metadata pointing at a directory
            # that is renamed immediately afterwards.
            subprocess.run([uv, "sync", "--frozen", "--no-dev", "--project", str(release_path), "--python", python_base], check=True, cwd=release_path, timeout=600)
        current = root / "current"
        if not current.is_symlink():
            raise ReleaseError("system_update_install_invalid", "current release link is not configured", 503)
        temporary_link = root / f".current-{job_id}"
        os.symlink(str(release_path), temporary_link)
        os.replace(temporary_link, current)
        switched_current = True
        _set_job(job_id, progress=75, message="restarting service")
        _restart_and_health(version)
        _set_job(job_id, status="succeeded", progress=100, message="update completed", errorCode=None)
    except Exception as error:
        with contextlib.suppress(FileNotFoundError):
            if staging_path.exists():
                shutil.rmtree(staging_path)
            if not switched_current and release_path.exists():
                shutil.rmtree(release_path)
        rollback_ok = True
        if switched_current and old_target is not None:
            current = root / "current"
            temporary_link = root / f".rollback-{job_id}"
            rollback_ok = False
            try:
                os.symlink(old_target, temporary_link)
                os.replace(temporary_link, current)
                subprocess.run([_systemctl_bin(), "restart", "fuwu-server.service"], check=True, timeout=30)
                _restart_health_only(old_version)
                rollback_ok = True
            except (OSError, subprocess.SubprocessError, ReleaseError):
                with contextlib.suppress(FileNotFoundError):
                    temporary_link.unlink()
            if rollback_ok and release_path.exists():
                shutil.rmtree(release_path, ignore_errors=True)
        code = error.code if isinstance(error, ReleaseError) else "system_update_failed"
        message = error.message if isinstance(error, ReleaseError) else "update failed"
        if switched_current and not rollback_ok:
            code = "system_update_rollback_failed"
            message = "update failed and rollback could not be verified"
        _set_job(job_id, status="failed", progress=0, message=message, errorCode=code)
    finally:
        with _open_state_lock() as lock:
            state = _read_state_unlocked()
            if state.get("activeJobId") == job_id:
                state["activeJobId"] = None
                _write_state_unlocked(state)


def _version_from_target(install_root: Path, target: str) -> str:
    resolved = Path(target)
    if not resolved.is_absolute():
        resolved = install_root / resolved
    metadata_path = resolved / "fuwu-update.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        version = validate_version(metadata.get("version"))
        if metadata.get("project") != "fuwu" or metadata.get("tag") != f"v{version}" or resolved.name != f"v{version}":
            raise ValueError
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, ReleaseError, ValueError):
        raise ReleaseError("system_update_rollback_failed", "previous release metadata is invalid", 503) from None
    return version


def _systemctl_bin() -> str:
    value = os.environ.get("FUWU_SYSTEMCTL_BIN")
    if not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise ReleaseError("system_update_runtime_unavailable", "systemd is unavailable", 503)
    return value


def _uv_bin() -> str:
    value = os.environ.get("FUWU_UV_BIN")
    if not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise ReleaseError("system_update_runtime_unavailable", "update runtime is unavailable", 503)
    return value


def _health_port() -> str:
    port = os.environ.get("FUWU_PORT", "8088")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ReleaseError("system_update_install_invalid", "health check configuration is invalid", 503)
    return port


def _preflight_install(install_root: Path) -> tuple[str, str]:
    releases = install_root / "releases"
    current = install_root / "current"
    static_dist = install_root / "frontend" / "apps" / "web-antd" / "dist"
    try:
        _require_root_owned(install_root)
        releases_info = releases.lstat()
        if (not stat.S_ISDIR(releases_info.st_mode) or not _is_root_owned(releases)
                or releases_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise OSError
        current_info = current.lstat()
        if not stat.S_ISLNK(current_info.st_mode) or not _is_root_owned(current):
            raise OSError
        target = os.readlink(current)
        resolved = current.resolve(strict=True)
        if resolved.parent != releases.resolve(strict=True) or not resolved.is_dir():
            raise OSError
        _require_root_owned(resolved)
        _require_protected_tree(resolved)
        if not SYSTEM_UPDATE_TAG_RE.fullmatch(resolved.name):
            raise OSError
        version = _version_from_target(install_root, target)
        static_info = static_dist.lstat()
        if not stat.S_ISLNK(static_info.st_mode) or not _is_root_owned(static_dist):
            raise OSError
        static_target = os.readlink(static_dist)
        if static_target not in {
            "../../../current/frontend/apps/web-antd/dist",
            str(install_root / "current/frontend/apps/web-antd/dist"),
        }:
            raise OSError
        if static_dist.resolve(strict=True) != (resolved / "frontend/apps/web-antd/dist").resolve(strict=True):
            raise OSError
        return target, version
    except (OSError, ReleaseError):
        raise ReleaseError("system_update_install_invalid", "online update bootstrap is incomplete", 503) from None


def _require_root_owned(path: Path) -> None:
    info = path.lstat()
    if (not _is_root_owned(path) or not stat.S_ISDIR(info.st_mode)
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
        raise OSError("installation path is not a protected root-owned directory")


def _require_protected_tree(root: Path) -> None:
    """Reject writable or non-root-owned paths in an active release tree."""
    for directory, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        _require_protected_path(directory_path)
        for name in (*dirnames, *filenames):
            _require_protected_path(directory_path / name)


def _require_protected_path(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        trusted_owners = {0, os.geteuid()}
        if info.st_uid not in trusted_owners:
            raise OSError("release tree contains an unprotected symlink")
        target = path.resolve(strict=True)
        for resolved_path in (target, *target.parents):
            resolved_info = resolved_path.lstat()
            if (resolved_info.st_uid not in trusted_owners
                    or resolved_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
                raise OSError("release tree symlink target is unprotected")
        return
    if info.st_uid != os.geteuid() or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OSError("release tree contains an unprotected path")


def _is_root_owned(path: Path) -> bool:
    return path.lstat().st_uid == 0


def _restart_health_only(expected_version: str) -> None:
    deadline = time.monotonic() + SYSTEM_UPDATE_HEALTH_TIMEOUT
    port = _health_port()
    url = f"http://127.0.0.1:{port}/healthz"
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "fuwu-system-updater"})
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.loads(response.read(16_384).decode("utf-8"))
            if (response.status == 200 and isinstance(body, Mapping)
                    and body.get("status") == "ok" and body.get("version") == expected_version):
                return
        except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            pass
        time.sleep(SYSTEM_UPDATE_HEALTH_INTERVAL)
    raise ReleaseError("system_update_rollback_failed", "previous service health check failed", 503)


def worker_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != "--worker":
        return 2
    try:
        run_system_update_worker(args[1], args[2])
    except Exception:
        return 1
    return 0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects instead of allowing the fixed request to escape."""

    def redirect_request(self, request, file, code, message, headers, newurl):
        raise ReleaseError(
            "system_update_redirected",
            "system update metadata redirected unexpectedly",
            502,
        )


def _read_response(response: Any) -> bytes:
    """Read at most the metadata limit and reject dishonest length headers."""

    headers = getattr(response, "headers", {})
    declared = headers.get("Content-Length") if headers is not None else None
    if declared is not None:
        try:
            declared_size = int(declared)
        except (TypeError, ValueError):
            raise ReleaseError(
                "system_update_invalid_response",
                "system update metadata response is invalid",
                502,
            ) from None
        if declared_size < 0 or declared_size > SYSTEM_UPDATE_RESPONSE_MAX_BYTES:
            raise ReleaseError(
                "system_update_response_too_large",
                "system update metadata response is too large",
                502,
            )
    body = response.read(SYSTEM_UPDATE_RESPONSE_MAX_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > SYSTEM_UPDATE_RESPONSE_MAX_BYTES:
        raise ReleaseError(
            "system_update_response_too_large",
            "system update metadata response is too large",
            502,
        )
    if declared is not None and len(body) != int(declared):
        raise ReleaseError(
            "system_update_invalid_response",
            "system update metadata response is invalid",
            502,
        )
    return body


def _invalid_metadata() -> ReleaseError:
    return ReleaseError(
        "system_update_invalid_release",
        "latest Fuwu release metadata is invalid",
        502,
    )


def _sanitize_notes(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _invalid_metadata()
    if len(value) > SYSTEM_UPDATE_NOTES_MAX_CHARS:
        raise ReleaseError(
            "system_update_notes_too_large",
            "latest Fuwu release notes are too large",
            502,
        )
    # Keep useful whitespace while removing control characters that could
    # affect logs or a management UI.  Unicode printable characters remain.
    return "".join(
        character
        for character in value
        if character in "\t\n\r" or character.isprintable()
    )


def _sanitize_assets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > SYSTEM_UPDATE_ASSET_MAX_COUNT:
        raise _invalid_metadata()
    assets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            raise _invalid_metadata()
        name = item.get("name")
        if (
            not isinstance(name, str)
            or len(name) > SYSTEM_UPDATE_ASSET_NAME_MAX_CHARS
            or not SYSTEM_UPDATE_ASSET_NAME_RE.fullmatch(name)
            or name in seen
        ):
            raise _invalid_metadata()
        seen.add(name)

        size = item.get("size")
        if size is not None and (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or size > SYSTEM_UPDATE_ASSET_SIZE_MAX_BYTES
        ):
            raise _invalid_metadata()

        digest = item.get("digest")
        normalized_digest: str | None = None
        if digest is not None:
            if not isinstance(digest, str):
                raise _invalid_metadata()
            match = SYSTEM_UPDATE_DIGEST_RE.fullmatch(digest)
            if match is None:
                raise _invalid_metadata()
            normalized_digest = match.group(1).lower()
        assets.append({"name": name, "size": size, "digest": normalized_digest})
    return assets


def _published_at(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise _invalid_metadata()
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _invalid_metadata() from None
    if parsed.tzinfo is None:
        raise _invalid_metadata()
    return value


def _version_tuple(value: str) -> tuple[int, int, int]:
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def check_system_update(current_version: str) -> dict[str, Any]:
    """Return safe metadata for the latest stable Fuwu release.

    The request is always made to the fixed public GitHub endpoint.  The
    result contains no GitHub asset URLs; a future updater must resolve and
    verify assets through its own pinned contract.
    """

    try:
        current = validate_version(current_version)
    except ReleaseError:
        raise

    request = urllib.request.Request(
        SYSTEM_UPDATE_LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "fuwu-system-update-check",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=SYSTEM_UPDATE_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise ReleaseError(
                    "system_update_unavailable",
                    "latest Fuwu release metadata is unavailable",
                    502,
                )
            body = _read_response(response)
        payload = json.loads(body.decode("utf-8"))
    except ReleaseError:
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError,
            UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ReleaseError(
            "system_update_unavailable",
            "latest Fuwu release metadata is unavailable",
            502,
        ) from None

    if not isinstance(payload, Mapping):
        raise _invalid_metadata()
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ReleaseError(
            "system_update_not_stable",
            "latest Fuwu release is not a stable release",
            502,
        )

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise _invalid_metadata()
    try:
        latest = validate_version(tag[1:])
    except ReleaseError:
        raise _invalid_metadata() from None
    published_at = _published_at(payload.get("published_at"))
    notes = _sanitize_notes(payload.get("body"))
    assets = _sanitize_assets(payload.get("assets"))

    return {
        "currentVersion": current,
        "latestVersion": latest,
        "tag": tag,
        "publishedAt": published_at,
        "releaseUrl": f"https://github.com/{SYSTEM_UPDATE_REPOSITORY}/releases/tag/{tag}",
        "updateAvailable": _version_tuple(latest) > _version_tuple(current),
        "notes": notes,
        "assets": assets,
    }


if __name__ == "__main__":
    raise SystemExit(worker_main())

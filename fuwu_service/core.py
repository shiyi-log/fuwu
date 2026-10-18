"""Core release management domain, repositories, and object storage drivers.

The module intentionally uses only the Python standard library.  A Django or
DRF adapter can call the same service objects without duplicating release
validation and storage safety rules.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import re
import sqlite3
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol


UTC = dt.timezone.utc
VERSION_RE = re.compile(r"^(0|[1-9])\.(0|[1-9])\.(0|[1-9])$")
REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
CHANNELS = {"stable", "beta"}


class ReleaseError(Exception):
    """Expected, safe-to-return API error."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def safe_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_version(value: str) -> str:
    if not isinstance(value, str) or value.startswith("v") or not VERSION_RE.fullmatch(value):
        raise ReleaseError("invalid_version", "version format is invalid", 422)
    return value


def next_version(value: str) -> str:
    """Return the next single-digit SemVer release (0.0.9 -> 0.1.0).

    The release registry intentionally uses one decimal digit per component so
    release ordering remains explicit in the small initial deployment.
    """
    value = validate_version(value)
    major, minor, patch = (int(part) for part in value.split("."))
    if patch < 9:
        patch += 1
    elif minor < 9:
        patch, minor = 0, minor + 1
    elif major < 9:
        patch, minor, major = 0, 0, major + 1
    else:
        raise ReleaseError("version_overflow", "version cannot be incremented", 422)
    return f"{major}.{minor}.{patch}"


def validate_tag(value: str, version: str) -> str:
    if not isinstance(value, str) or value != f"v{version}":
        raise ReleaseError("invalid_tag", "tag must match vMAJOR.MINOR.PATCH", 422)
    return value


def validate_revision(value: str) -> str:
    if not isinstance(value, str) or not REVISION_RE.fullmatch(value):
        raise ReleaseError("invalid_revision", "revision must be a 40 character hexadecimal value", 422)
    return value.lower()


def validate_channel(value: str) -> str:
    if value not in CHANNELS:
        raise ReleaseError("invalid_channel", "channel must be stable or beta", 422)
    return value


def safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    # S3 keys and staging paths are deliberately restricted to portable names.
    if any(part.startswith(".") for part in path.parts):
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    return "/".join(path.parts)


def protected_job_path(root: Path, job_ref: str) -> Path:
    if not isinstance(job_ref, str) or not JOB_RE.fullmatch(job_ref):
        raise ReleaseError("invalid_staging_reference", "staging reference is invalid", 422)
    base = root.resolve()
    candidate = (base / job_ref).resolve()
    if candidate.parent != base or candidate == base:
        raise ReleaseError("invalid_staging_reference", "staging reference is invalid", 422)
    return candidate


def _asset_entries(assets: Any) -> list[dict[str, Any]]:
    """Flatten Heyue-style ``assets.docker/python`` into checked entries."""
    entries: list[dict[str, Any]] = []
    if not isinstance(assets, Mapping):
        raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
    for group, values in assets.items():
        if group not in {"docker", "python"}:
            continue
        if isinstance(values, Mapping):
            values = [values]
        if not isinstance(values, list):
            raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
        for value in values:
            if isinstance(value, str):
                entries.append({"group": group, "path": value})
            elif isinstance(value, Mapping):
                item = dict(value)
                item["group"] = group
                item["path"] = item.get("path") or item.get("name") or item.get("file")
                entries.append(item)
            else:
                raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
    if not entries:
        raise ReleaseError("invalid_manifest", "manifest must include docker or python assets", 422)
    for item in entries:
        item["path"] = safe_relative_path(item.get("path"))
    return entries


def validate_manifest(manifest: Mapping[str, Any], requested: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(manifest, Mapping):
        raise ReleaseError("invalid_manifest", "manifest is invalid", 422)
    result = dict(manifest)
    if result.get("schemaVersion") != 1:
        raise ReleaseError("invalid_manifest", "manifest schemaVersion must be 1", 422)
    version = validate_version(result.get("version"))
    revision = validate_revision(result.get("revision"))
    if not result.get("repository") or not isinstance(result.get("repository"), str):
        raise ReleaseError("invalid_manifest", "manifest repository is required", 422)
    validate_tag(result.get("tag"), version)
    release_id = result.get("releaseId")
    valid_release_id = (
        (isinstance(release_id, int) and not isinstance(release_id, bool) and release_id > 0)
        or (isinstance(release_id, str) and bool(release_id.strip()))
    )
    if not valid_release_id:
        raise ReleaseError("invalid_manifest", "manifest releaseId is required", 422)
    if result.get("draft") is not False or result.get("prerelease") is not False:
        raise ReleaseError("invalid_manifest", "manifest must be a final release", 422)
    if requested:
        if requested.get("project") and requested["project"] != result.get("project", requested["project"]):
            # The project is normally supplied by the API and not repeated in v1 manifests.
            raise ReleaseError("manifest_mismatch", "manifest project does not match request", 422)
        if requested.get("version") and validate_version(requested["version"]) != version:
            raise ReleaseError("manifest_mismatch", "manifest version does not match request", 422)
        if requested.get("revision") and validate_revision(requested["revision"]) != revision:
            raise ReleaseError("manifest_mismatch", "manifest revision does not match request", 422)
        if requested.get("tag") and requested["tag"] != result.get("tag"):
            raise ReleaseError("manifest_mismatch", "manifest tag does not match request", 422)
    entries = _asset_entries(result.get("assets"))
    result["version"] = version
    result["revision"] = revision
    result.setdefault("publishedAt", now_iso())
    result.setdefault("verifiedAt", now_iso())
    result.setdefault("verificationSource", "heyue-release-storage")
    result.setdefault("checksums", {})
    return result, entries


class ObjectStore(Protocol):
    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None) -> None: ...
    def head_object(self, key: str) -> dict[str, Any]: ...
    def get_object(self, key: str) -> bytes: ...


class MemoryObjectStore:
    """Deterministic local store used by tests and dry-run development."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None) -> None:
        if key in self.objects:
            existing = self.objects[key][0]
            # latest.json is the sole mutable pointer; all version-prefixed objects
            # remain immutable once written.
            mutable_pointer = bool(re.search(r"/(?:stable|beta)/latest\.json$", key))
            if not mutable_pointer and existing != body:
                raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
            if mutable_pointer:
                self.objects[key] = (bytes(body), sha256 or safe_digest(body))
            return
        self.objects[key] = (bytes(body), sha256 or safe_digest(body))

    def head_object(self, key: str, *, public: bool = False) -> dict[str, Any]:
        try:
            body, digest = self.objects[key]
        except KeyError:
            raise ReleaseError("object_not_found", "storage object was not found", 404)
        return {"key": key, "size": len(body), "sha256": digest}

    def get_object(self, key: str, *, public: bool = False) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError:
            raise ReleaseError("object_not_found", "storage object was not found", 404)


@dataclass(frozen=True)
class AwsCredentials:
    access_key: str
    secret_key: str
    session_token: str | None = None


def load_aws_credentials(env: Mapping[str, str] | None = None) -> AwsCredentials:
    env = env or os.environ
    access = env.get("AWS_ACCESS_KEY_ID") or env.get("AWS_ACCESS_KEY")
    secret = env.get("AWS_SECRET_ACCESS_KEY") or env.get("AWS_SECRET_KEY")
    token = env.get("AWS_SESSION_TOKEN")
    file_name = env.get("AWS_CREDENTIALS_FILE")
    if not (access and secret) and file_name:
        path = Path(file_name).expanduser()
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise ReleaseError("credentials_not_protected", "credential file permissions are too broad", 500)
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            # A missing optional file may be accompanied by an instance or
            # container role; continue through the standard role chain.
            lines = []
        values: dict[str, str] = {}
        for line in lines:
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        access = access or values.get("AWS_ACCESS_KEY_ID") or values.get("aws_access_key_id")
        secret = secret or values.get("AWS_SECRET_ACCESS_KEY") or values.get("aws_secret_access_key")
        token = token or values.get("AWS_SESSION_TOKEN") or values.get("aws_session_token")
    if not (access and secret):
        # Container and instance profiles are the preferred deployment path;
        # only the role endpoint is contacted and no response is logged.
        role_url = env.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        relative = env.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI")
        if not role_url and relative:
            role_url = "http://169.254.170.2" + relative
        if role_url:
            try:
                req = urllib.request.Request(role_url)
                if env.get("AWS_CONTAINER_AUTHORIZATION_TOKEN"):
                    req.add_header("Authorization", env["AWS_CONTAINER_AUTHORIZATION_TOKEN"])
                with urllib.request.urlopen(req, timeout=1.0) as response:
                    payload = json.loads(response.read())
                access = payload.get("AccessKeyId")
                secret = payload.get("SecretAccessKey")
                token = payload.get("Token")
            except Exception:
                access = secret = None
        if not (access and secret) and env.get("AWS_EC2_METADATA_DISABLED", "").lower() not in {"1", "true"}:
            try:
                token_req = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT")
                token_req.add_header("X-aws-ec2-metadata-token-ttl-seconds", "21600")
                with urllib.request.urlopen(token_req, timeout=0.5) as response:
                    metadata_token = response.read().decode()
                role_req = urllib.request.Request("http://169.254.169.254/latest/meta-data/iam/security-credentials/")
                role_req.add_header("X-aws-ec2-metadata-token", metadata_token)
                with urllib.request.urlopen(role_req, timeout=0.5) as response:
                    role = response.read().decode().strip()
                creds_req = urllib.request.Request("http://169.254.169.254/latest/meta-data/iam/security-credentials/" + urllib.parse.quote(role, safe=""))
                creds_req.add_header("X-aws-ec2-metadata-token", metadata_token)
                with urllib.request.urlopen(creds_req, timeout=0.5) as response:
                    payload = json.loads(response.read())
                access = payload.get("AccessKeyId")
                secret = payload.get("SecretAccessKey")
                token = payload.get("Token")
            except Exception:
                access = secret = None
    if not (access and secret):
        raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 500)
    return AwsCredentials(access, secret, token)


class S3ObjectStore:
    """Small dependency-free S3-compatible driver with AWS Signature V4 writes."""

    def __init__(self, bucket: str, region: str = "us-east-1", endpoint: str | None = None, credentials: AwsCredentials | None = None, *, public_reads: bool = True, timeout: float = 20.0):
        if not bucket or not re.fullmatch(r"[A-Za-z0-9.-]{3,63}", bucket):
            raise ValueError("invalid bucket")
        self.bucket = bucket
        self.region = region
        self.endpoint = (endpoint or f"https://s3.{region}.amazonaws.com").rstrip("/")
        self.credentials = credentials
        self.public_reads = public_reads
        self.timeout = timeout

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "S3ObjectStore":
        env = env or os.environ
        credentials = load_aws_credentials(env)
        return cls(env.get("FUWU_S3_BUCKET", "heyue"), env.get("FUWU_S3_REGION", "ap-southeast-1"), env.get("FUWU_S3_ENDPOINT"), credentials, public_reads=env.get("FUWU_S3_PUBLIC_READS", "1") != "0")

    def _url(self, key: str) -> str:
        safe = "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))
        parsed = urllib.parse.urlsplit(self.endpoint)
        if parsed.path.rstrip("/").endswith("/" + self.bucket):
            path = parsed.path.rstrip("/") + "/" + safe
        else:
            path = parsed.path.rstrip("/") + "/" + urllib.parse.quote(self.bucket, safe="") + "/" + safe
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _signed_request(self, method: str, key: str, body: bytes | None = None, *, content_type: str | None = None, sha256: str | None = None, cache_control: str | None = None) -> urllib.request.Request:
        if not self.credentials:
            raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 500)
        body = body or b""
        payload_hash = safe_digest(body)
        url = self._url(key)
        parsed = urllib.parse.urlsplit(url)
        host = parsed.netloc
        amz_date = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
        if self.credentials.session_token:
            headers["x-amz-security-token"] = self.credentials.session_token
        if content_type:
            headers["content-type"] = content_type
        if sha256:
            headers["x-amz-meta-sha256"] = sha256
        if cache_control:
            headers["cache-control"] = cache_control
        signed_names = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{' '.join(v.strip().split())}\n" for k, v in sorted(headers.items()))
        canonical_uri = parsed.path or "/"
        canonical_query = parsed.query
        canonical_request = "\n".join([method, canonical_uri, canonical_query, canonical_headers, signed_names, payload_hash])
        scope = f"{date}/{self.region}/s3/aws4_request"
        string_to_sign = "AWS4-HMAC-SHA256\n" + amz_date + "\n" + scope + "\n" + safe_digest(canonical_request.encode())
        def sign(key_bytes: bytes, value: str) -> bytes:
            return hmac.new(key_bytes, value.encode(), hashlib.sha256).digest()
        k_date = sign(("AWS4" + self.credentials.secret_key).encode(), date)
        k_region = sign(k_date, self.region)
        k_service = sign(k_region, "s3")
        k_signing = sign(k_service, "aws4_request")
        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
        req = urllib.request.Request(url, data=body if method == "PUT" else None, method=method)
        for key_name, value in headers.items():
            req.add_header(key_name, value)
        req.add_header("Authorization", f"AWS4-HMAC-SHA256 Credential={self.credentials.access_key}/{scope}, SignedHeaders={signed_names}, Signature={signature}")
        return req

    def _request(self, method: str, key: str, body: bytes | None = None, *, content_type: str | None = None, sha256: str | None = None, cache_control: str | None = None, public: bool = False) -> tuple[int, Mapping[str, str], bytes]:
        if public and self.public_reads:
            req = urllib.request.Request(self._url(key), method=method)
        else:
            req = self._signed_request(method, key, body, content_type=content_type, sha256=sha256, cache_control=cache_control)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReleaseError("object_not_found", "storage object was not found", 404)
            raise ReleaseError("storage_request_failed", "storage request failed", 502)
        except (urllib.error.URLError, TimeoutError):
            raise ReleaseError("storage_unavailable", "storage service is unavailable", 503)

    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None) -> None:
        self._request("PUT", key, body, content_type=content_type, sha256=sha256, cache_control=cache_control)

    def head_object(self, key: str, *, public: bool = False) -> dict[str, Any]:
        _, headers, _ = self._request("HEAD", key, public=public)
        raw_digest = headers.get("x-amz-meta-sha256") or headers.get("x-fuwu-sha256")
        if not raw_digest:
            raise ReleaseError("storage_digest_missing", "storage object digest is unavailable", 502)
        return {"key": key, "size": int(headers.get("Content-Length", "0")), "sha256": raw_digest}

    def get_object(self, key: str, *, public: bool = False) -> bytes:
        _, _, body = self._request("GET", key, public=public)
        return body

    def head_public(self, key: str) -> dict[str, Any]:
        return self.head_object(key, public=True)

    def get_public(self, key: str) -> bytes:
        return self.get_object(key, public=True)


class SQLiteRepository:
    """Repository boundary. Replace this class with MySQL/Redis adapters later."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version TEXT NOT NULL, channel TEXT NOT NULL, repository TEXT NOT NULL, tag TEXT NOT NULL, revision TEXT NOT NULL, release_id TEXT NOT NULL, published_at TEXT NOT NULL, digest TEXT NOT NULL, manifest_key TEXT NOT NULL, status TEXT NOT NULL, manifest_json TEXT NOT NULL, UNIQUE(project_id, version, channel), FOREIGN KEY(project_id) REFERENCES projects(id));
            CREATE TABLE IF NOT EXISTS artifacts (release_id TEXT NOT NULL, name TEXT NOT NULL, object_key TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(release_id, name), FOREIGN KEY(release_id) REFERENCES releases(id));
            CREATE TABLE IF NOT EXISTS latest (project_id TEXT NOT NULL, channel TEXT NOT NULL, release_pk TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL, pointer_key TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(project_id, channel), FOREIGN KEY(release_pk) REFERENCES releases(id));
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, project_id TEXT, version TEXT, channel TEXT, actor TEXT NOT NULL, request_id TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS idempotency (request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """
        )
        self.conn.commit()

    def ensure_project(self, project_id: str, *, name: str | None = None, repository: str = "") -> dict[str, Any]:
        if not PROJECT_RE.fullmatch(project_id):
            raise ReleaseError("invalid_project", "project identifier is invalid", 422)
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row:
            return dict(row)
        value = {"id": project_id, "name": name or project_id, "repository": repository, "created_at": now_iso()}
        self.conn.execute("INSERT INTO projects VALUES (:id,:name,:repository,:created_at)", value)
        self.conn.commit()
        return value

    def list_projects(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM projects ORDER BY id")]

    def get_release(self, project_id: str, version: str, channel: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM releases WHERE project_id=? AND version=? AND channel=?", (project_id, version, channel)).fetchone()
        return self._release_dict(row) if row else None

    def get_release_by_id(self, release_pk: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM releases WHERE id=?", (release_pk,)).fetchone()
        return self._release_dict(row) if row else None

    def save_release(self, value: Mapping[str, Any], artifacts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        self.conn.execute("INSERT INTO releases VALUES (:id,:project_id,:version,:channel,:repository,:tag,:revision,:release_id,:published_at,:digest,:manifest_key,:status,:manifest_json)", value)
        for artifact in artifacts:
            self.conn.execute("INSERT INTO artifacts VALUES (:release_id,:name,:object_key,:size,:sha256)", artifact)
        self.conn.commit()
        return dict(value)

    def update_release_status(self, release_pk: str, status: str) -> None:
        self.conn.execute("UPDATE releases SET status=? WHERE id=?", (status, release_pk))
        self.conn.commit()

    def set_latest(self, value: Mapping[str, Any]) -> None:
        self.conn.execute("INSERT INTO latest(project_id,channel,release_pk,version,digest,pointer_key,updated_at) VALUES (:project_id,:channel,:release_pk,:version,:digest,:pointer_key,:updated_at) ON CONFLICT(project_id,channel) DO UPDATE SET release_pk=excluded.release_pk,version=excluded.version,digest=excluded.digest,pointer_key=excluded.pointer_key,updated_at=excluded.updated_at", value)
        self.conn.commit()

    def get_latest(self, project_id: str, channel: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM latest WHERE project_id=? AND channel=?", (project_id, channel)).fetchone()
        return dict(row) if row else None

    def list_releases(self, project_id: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM releases"
        args: list[Any] = []
        where = []
        if project_id:
            where.append("project_id=?"); args.append(project_id)
        if channel:
            where.append("channel=?"); args.append(channel)
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY published_at DESC"
        return [self._release_dict(row) for row in self.conn.execute(query, args)]

    def list_artifacts(self, release_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM artifacts WHERE release_id=? ORDER BY name", (release_id,))]

    def list_audit(self, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if project_id:
            rows = self.conn.execute("SELECT * FROM audit WHERE project_id=? ORDER BY id DESC LIMIT ?", (project_id, min(max(limit, 1), 500)))
        else:
            rows = self.conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (min(max(limit, 1), 500),))
        output = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            output.append(item)
        return output

    def audit_event(self, event_type: str, *, project_id: str | None, version: str | None, channel: str | None, actor: str, request_id: str, details: Mapping[str, Any]) -> None:
        # Details are intentionally supplied by the service after sensitive fields have been removed.
        self.conn.execute("INSERT INTO audit(event_type,project_id,version,channel,actor,request_id,details_json,created_at) VALUES (?,?,?,?,?,?,?,?)", (event_type, project_id, version, channel, actor, request_id, json.dumps(details, sort_keys=True), now_iso()))
        self.conn.commit()

    def idempotency_get(self, request_id: str) -> tuple[int, dict[str, Any]] | None:
        row = self.conn.execute("SELECT status_code,response_json FROM idempotency WHERE request_id=?", (request_id,)).fetchone()
        return (int(row[0]), json.loads(row[1])) if row else None

    def idempotency_put(self, request_id: str, operation: str, status_code: int, response: Mapping[str, Any]) -> None:
        self.conn.execute("INSERT OR IGNORE INTO idempotency VALUES (?,?,?,?,?)", (request_id, operation, status_code, json.dumps(response, sort_keys=True), now_iso()))
        self.conn.commit()

    @staticmethod
    def _release_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["manifest"] = json.loads(item.pop("manifest_json"))
        return item


class RBAC:
    PERMISSIONS = {"release.storage.read", "release.storage.publish", "release.storage.rollback"}

    def __init__(self, tokens: Mapping[str, Iterable[str]] | None = None):
        self.tokens = {token: (set(perms) & self.PERMISSIONS) | ({"superadmin"} if "superadmin" in set(perms) else set()) for token, perms in (tokens or {}).items()}

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RBAC":
        env = env or os.environ
        raw = env.get("FUWU_AUTH_TOKENS", "")
        if not raw:
            return cls({})
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, Mapping):
                return cls({str(token): values for token, values in parsed.items() if isinstance(values, list)})
        except json.JSONDecodeError:
            pass
        return cls({})

    def authenticate(self, authorization: str | None) -> tuple[str, set[str]]:
        if not authorization or not authorization.startswith("Bearer "):
            raise ReleaseError("unauthorized", "authentication required", 401)
        token = authorization[7:].strip()
        if not token or token not in self.tokens:
            raise ReleaseError("unauthorized", "authentication required", 401)
        return token[:8], self.tokens[token]

    def require(self, authorization: str | None, permission: str) -> str:
        actor, permissions = self.authenticate(authorization)
        if permission not in permissions:
            raise ReleaseError("forbidden", "permission denied", 403)
        return actor


class ReleaseService:
    def __init__(self, repository: SQLiteRepository, store: ObjectStore, *, staging_dir: str | Path = "staging", prefix: str = "releases"):
        self.repository = repository
        self.store = store
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix.strip("/")

    def _keys(self, project: str, version: str, channel: str) -> tuple[str, str, str]:
        # Immutable object prefix intentionally follows Heyue's releases/{project}/vX.Y.Z convention.
        immutable = f"{self.prefix}/{project}/v{version}"
        manifest_key = f"{immutable}/manifest.json"
        latest_key = f"{self.prefix}/{project}/{channel}/latest.json"
        return immutable, manifest_key, latest_key

    def _put_immutable(self, key: str, body: bytes, *, content_type: str, sha256: str) -> dict[str, Any]:
        """Write an immutable object only when the key is absent or identical."""
        try:
            existing = self.store.head_object(key)
            if existing.get("sha256") != sha256 or int(existing.get("size", -1)) != len(body):
                raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
            return existing
        except ReleaseError as error:
            if error.code != "object_not_found":
                raise
        self.store.put_object(key, body, content_type=content_type, sha256=sha256)
        return self.store.head_object(key)

    @staticmethod
    def _latest_pointer(manifest: Mapping[str, Any], manifest_key: str, manifest_digest: str) -> dict[str, Any]:
        """Build the minimal Heyue v1 pointer without duplicating manifest fields."""
        return {
            "schemaVersion": 1,
            "project": manifest["project"],
            "channel": manifest["channel"],
            "version": manifest["version"],
            "tag": manifest["tag"],
            "revision": manifest["revision"],
            "releaseId": manifest["releaseId"],
            "manifestKey": manifest_key,
            "manifestSha256": manifest_digest,
            "publishedAt": manifest["publishedAt"],
            "immutable": True,
            "verificationSource": manifest["verificationSource"],
        }

    def _read_staging(self, job_ref: str, request: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Path, bytes]:
        job_dir = protected_job_path(self.staging_dir, job_ref)
        manifest_path = job_dir / "manifest.json"
        sums_path = job_dir / "SHA256SUMS"
        if not job_dir.is_dir() or not manifest_path.is_file() or not sums_path.is_file():
            raise ReleaseError("staging_not_found", "staging job was not found", 404)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sums_text = sums_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("invalid_staging", "staging bundle is invalid", 422)
        manifest, entries = validate_manifest(manifest, request)
        checksums: dict[str, str] = {}
        for line in sums_text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                raise ReleaseError("invalid_checksums", "SHA256SUMS is invalid", 422)
            checksums[safe_relative_path(parts[1].lstrip("*"))] = parts[0].lower()
        if not checksums:
            raise ReleaseError("invalid_checksums", "SHA256SUMS is empty", 422)
        asset_names = [Path(entry["path"]).name for entry in entries]
        if len(asset_names) != len(set(asset_names)):
            raise ReleaseError("invalid_manifest", "asset names must be unique", 422)
        for entry in entries:
            path = entry["path"]
            expected = checksums.get(path) or entry.get("sha256")
            if not expected or not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected)):
                raise ReleaseError("invalid_checksums", "asset checksum is missing", 422)
            source = (job_dir / path).resolve()
            if source.parent != job_dir.resolve() and job_dir.resolve() not in source.parents:
                raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
            if not source.is_file():
                raise ReleaseError("staging_asset_missing", "staging asset is missing", 422)
            digest = safe_digest(source.read_bytes())
            if digest != str(expected).lower():
                raise ReleaseError("checksum_mismatch", "asset checksum does not match", 422)
            entry["sha256"] = digest
            entry["size"] = source.stat().st_size
            entry["source"] = source
            entry["checksums"] = checksums
        return manifest, entries, job_dir, sums_path.read_bytes()

    def publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        cached = self.repository.idempotency_get(request_id)
        if cached:
            _, response = cached
            return 200, response
        project = request.get("project")
        if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
            raise ReleaseError("invalid_project", "project identifier is invalid", 422)
        version = validate_version(request.get("version"))
        channel = validate_channel(request.get("channel", "stable"))
        job_ref = request.get("staging_ref") or request.get("job_ref")
        if not isinstance(job_ref, str):
            raise ReleaseError("invalid_staging_reference", "staging reference is required", 422)
        try:
            manifest, entries, job_dir, sums_bytes = self._read_staging(job_ref, {**request, "version": version, "project": project})
        except ReleaseError:
            self.repository.audit_event(
                "validation_failed",
                project_id=project,
                version=version,
                channel=channel,
                actor=actor,
                request_id=request_id,
                details={"jobId": request_id, "status": "failed", "progress": 0},
            )
            raise
        self.repository.audit_event(
            "validation",
            project_id=project,
            version=version,
            channel=channel,
            actor=actor,
            request_id=request_id,
            details={"jobId": request_id, "status": "validated", "progress": 100},
        )
        release_digest = safe_digest(json_bytes(manifest) + sums_bytes)
        existing = self.repository.get_release(project, version, channel)
        if existing:
            if existing["digest"] != release_digest:
                self.repository.audit_event("publish_failed", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "reason": "release_conflict", "digest": release_digest})
                raise ReleaseError("release_conflict", "same version already has a different digest", 409)
            response = {"status": "idempotent", "release": self._public_release(existing)}
            self.repository.idempotency_put(request_id, "publish", 200, response)
            return 200, response
        self.repository.ensure_project(project, repository=manifest["repository"])
        immutable, manifest_key, latest_key = self._keys(project, version, channel)
        sums_key = f"{immutable}/SHA256SUMS"
        artifact_rows: list[dict[str, Any]] = []
        try:
            # Upload each asset under its immutable prefix and immediately verify a HEAD digest.
            for entry in entries:
                object_key = f"{immutable}/{entry['path']}"
                body = entry["source"].read_bytes()
                head = self._put_immutable(object_key, body, content_type=mimetypes.guess_type(entry["path"])[0] or "application/octet-stream", sha256=entry["sha256"])
                if head.get("sha256") != entry["sha256"] or int(head.get("size", -1)) != entry["size"]:
                    raise ReleaseError("storage_verification_failed", "uploaded asset verification failed", 502)
                artifact_rows.append({"release_id": "", "name": entry["path"], "object_key": object_key, "size": entry["size"], "sha256": entry["sha256"]})
            sums_digest = safe_digest(sums_bytes)
            sums_head = self._put_immutable(sums_key, sums_bytes, content_type="text/plain; charset=utf-8", sha256=sums_digest)
            if sums_head.get("sha256") != sums_digest or int(sums_head.get("size", -1)) != len(sums_bytes):
                raise ReleaseError("storage_verification_failed", "uploaded checksum verification failed", 502)
            artifact_rows.append({"release_id": "", "name": "SHA256SUMS", "object_key": sums_key, "size": len(sums_bytes), "sha256": sums_digest})
            published = {
                key: manifest[key]
                for key in (
                    "schemaVersion",
                    "project",
                    "repository",
                    "version",
                    "tag",
                    "revision",
                    "releaseId",
                    "publishedAt",
                    "draft",
                    "prerelease",
                    "verifiedAt",
                    "verificationSource",
                )
                if key in manifest
            }
            published["project"] = project
            published["channel"] = channel
            published["checksums"] = {
                "name": "SHA256SUMS",
                "key": sums_key,
                "sha256": sums_digest,
                "entries": {Path(entry["path"]).name: entry["sha256"] for entry in entries},
            }
            published["assets"] = {}
            for group in ("docker", "python"):
                grouped = [
                    {"name": Path(entry["path"]).name, "key": f"{immutable}/{entry['path']}", "size": entry["size"], "sha256": entry["sha256"]}
                    for entry in entries if entry["group"] == group
                ]
                if grouped:
                    published["assets"][group] = grouped[0] if len(grouped) == 1 else grouped
            manifest_body = json_bytes(published)
            manifest_digest = safe_digest(manifest_body)
            manifest_head = self._put_immutable(manifest_key, manifest_body, content_type="application/json", sha256=manifest_digest)
            if manifest_head.get("sha256") != manifest_digest:
                raise ReleaseError("storage_verification_failed", "uploaded manifest verification failed", 502)
            release_pk = uuid.uuid4().hex
            for row in artifact_rows:
                row["release_id"] = release_pk
            # The latest pointer is only written after all immutable objects are verified.
            pointer_body = json_bytes(self._latest_pointer(published, manifest_key, safe_digest(manifest_body)))
            pointer_digest = safe_digest(pointer_body)
            self.store.put_object(latest_key, pointer_body, content_type="application/json", sha256=pointer_digest, cache_control="no-store, max-age=0")
            pointer_head = self.store.head_object(latest_key)
            if pointer_head.get("sha256") != pointer_digest:
                raise ReleaseError("storage_verification_failed", "uploaded latest pointer verification failed", 502)
            release = {"id": release_pk, "project_id": project, "version": version, "channel": channel, "repository": published["repository"], "tag": published["tag"], "revision": published["revision"], "release_id": published["releaseId"], "published_at": published.get("publishedAt") or now_iso(), "digest": release_digest, "manifest_key": manifest_key, "status": "published", "manifest_json": json.dumps(published, sort_keys=True)}
            self.repository.save_release(release, artifact_rows)
            self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": release_pk, "version": version, "digest": release_digest, "pointer_key": latest_key, "updated_at": now_iso()})
            self.repository.audit_event("publish", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "published", "progress": 100, "immutable_prefix": immutable, "manifest_key": manifest_key, "latest_key": latest_key, "digest": release_digest, "artifacts": [{"key": row["object_key"], "size": row["size"], "sha256": row["sha256"]} for row in artifact_rows]})
            response = {"status": "published", "job": {"id": request_id, "operation": "publish", "status": "published", "progress": 100}, "release": self._public_release({**release, "manifest": published})}
            self.repository.idempotency_put(request_id, "publish", 201, response)
            return 201, response
        except ReleaseError:
            self.repository.audit_event("publish_failed", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "digest": release_digest})
            raise

    def rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        cached = self.repository.idempotency_get(request_id)
        if cached:
            _, response = cached
            return 200, response
        if request.get("confirm") is not True:
            raise ReleaseError("confirmation_required", "rollback confirmation is required", 400)
        reason = str(request.get("reason") or "").strip()
        if not reason:
            raise ReleaseError("invalid_reason", "rollback reason is required", 422)
        project = request.get("project")
        channel = validate_channel(request.get("channel", "stable"))
        if not isinstance(project, str) or not PROJECT_RE.fullmatch(project):
            raise ReleaseError("invalid_project", "project identifier is invalid", 422)
        target = None
        if request.get("release_id"):
            target = self.repository.get_release_by_id(str(request["release_id"]))
        elif request.get("version"):
            target = self.repository.get_release(project, validate_version(request["version"]), channel)
        if not target or target["project_id"] != project or target["channel"] != channel:
            raise ReleaseError("release_not_found", "target release was not found", 404)
        before = self.repository.get_latest(project, channel)
        manifest_key = target["manifest_key"]
        manifest_head = self.store.head_object(manifest_key)
        manifest_body = self.store.get_object(manifest_key)
        if manifest_head.get("sha256") != safe_digest(manifest_body):
            raise ReleaseError("storage_verification_failed", "immutable manifest verification failed", 502)
        manifest = json.loads(manifest_body)
        pointer_key = self._keys(project, target["version"], channel)[2]
        pointer = json_bytes(self._latest_pointer(manifest, manifest_key, safe_digest(manifest_body)))
        pointer_digest = safe_digest(pointer)
        self.store.put_object(pointer_key, pointer, content_type="application/json", sha256=pointer_digest, cache_control="no-store, max-age=0")
        pointer_head = self.store.head_object(pointer_key)
        if pointer_head.get("sha256") != pointer_digest:
            raise ReleaseError("storage_verification_failed", "uploaded latest pointer verification failed", 502)
        self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": target["id"], "version": target["version"], "digest": target["digest"], "pointer_key": pointer_key, "updated_at": now_iso()})
        self.repository.audit_event("rollback", project_id=project, version=target["version"], channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "rolled_back", "progress": 100, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"]}, "reason": reason[:500], "pointer_key": pointer_key})
        response = {"status": "rolled_back", "job": {"id": request_id, "operation": "rollback", "status": "rolled_back", "progress": 100}, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"], "channel": channel}}
        self.repository.idempotency_put(request_id, "rollback", 200, response)
        return 200, response

    def status(self) -> dict[str, Any]:
        return {
            "provider": type(self.store).__name__,
            "prefix": self.prefix,
            "projects": len(self.repository.list_projects()),
            "releases": len(self.repository.list_releases()),
            "jobs": {
                "validation": self._job_summary("validation"),
                "publish": self._job_summary("publish"),
                "rollback": self._job_summary("rollback"),
            },
        }

    def releases(self, project_id: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        result = []
        for release in self.repository.list_releases(project_id, channel):
            latest = self.repository.get_latest(release["project_id"], release["channel"])
            artifacts = self.repository.list_artifacts(release["id"])
            head_status = "verified"
            try:
                self.store.head_object(release["manifest_key"])
                for artifact in artifacts:
                    self.store.head_object(artifact["object_key"])
            except ReleaseError:
                head_status = "unverified"
            result.append({**self._public_release(release), "latest": bool(latest and latest["release_pk"] == release["id"]), "objectHeadStatus": head_status, "artifacts": [{"name": artifact["name"], "key": artifact["object_key"], "size": artifact["size"], "sha256": artifact["sha256"]} for artifact in artifacts]})
        return result

    def _job_summary(self, event_type: str) -> dict[str, Any]:
        events = self.repository.list_audit(limit=500)
        matching = [event for event in events if event["event_type"] == event_type or event["event_type"] == f"{event_type}_failed"]
        latest = matching[0] if matching else None
        return {"status": (latest["details"].get("status") if latest else "idle"), "progress": (latest["details"].get("progress", 0) if latest else 0), "jobId": (latest["details"].get("jobId") if latest else None), "requestId": (latest["request_id"] if latest else None), "updatedAt": (latest["created_at"] if latest else None)}

    @staticmethod
    def _public_release(value: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value[key] for key in ("id", "project_id", "version", "channel", "repository", "tag", "revision", "release_id", "published_at", "digest", "manifest_key", "status") if key in value}

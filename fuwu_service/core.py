"""Core release management domain, repositories, and object storage drivers.

The module intentionally uses only the Python standard library.  A Django or
DRF adapter can call the same service objects without duplicating release
validation and storage safety rules.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .projects import (
    ProjectInputError,
    normalize_repository,
    project_create_values,
    project_update_values,
    validate_project_id,
)


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


def _project_value(validator, *args):
    try:
        return validator(*args)
    except ProjectInputError as error:
        raise ReleaseError(error.code, error.message, error.status) from None


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
    """Flatten the public ``assets.docker/python`` manifest map."""
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
    result.setdefault("verificationSource", "release-storage")
    result.setdefault("checksums", {})
    return result, entries


class ObjectStore(Protocol):
    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None, if_none_match: bool = False, if_match: str | None = None) -> dict[str, Any]: ...
    def head_object(self, key: str) -> dict[str, Any]: ...
    def get_object(self, key: str) -> bytes: ...
    def delete_object(self, key: str, *, if_match: str) -> None: ...


class MemoryObjectStore:
    """Deterministic local store used by tests and dry-run development."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None, if_none_match: bool = False, if_match: str | None = None) -> dict[str, Any]:
        if if_none_match and key in self.objects:
            raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
        if if_match is not None and (key not in self.objects or self.head_object(key)["etag"] != if_match):
            raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
        if key in self.objects:
            existing = self.objects[key][0]
            # latest.json is the sole mutable pointer; all version-prefixed objects
            # remain immutable once written.
            mutable_pointer = bool(re.search(r"/(?:stable|beta)/latest\.json$", key))
            if not mutable_pointer and existing != body:
                raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
            if mutable_pointer:
                self.objects[key] = (bytes(body), sha256 or safe_digest(body))
            return {"etag": '"' + safe_digest(body) + '"'}
        self.objects[key] = (bytes(body), sha256 or safe_digest(body))
        return {"etag": '"' + safe_digest(body) + '"'}

    def head_object(self, key: str, *, public: bool = False) -> dict[str, Any]:
        try:
            body, digest = self.objects[key]
        except KeyError:
            raise ReleaseError("object_not_found", "storage object was not found", 404)
        return {"key": key, "size": len(body), "sha256": digest, "etag": '"' + safe_digest(body) + '"'}

    def get_object(self, key: str, *, public: bool = False) -> bytes:
        try:
            return self.objects[key][0]
        except KeyError:
            raise ReleaseError("object_not_found", "storage object was not found", 404)

    def delete_object(self, key: str, *, if_match: str) -> None:
        if self.head_object(key)["etag"] != if_match:
            raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
        del self.objects[key]


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
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                file_stat = os.fstat(stream.fileno())
                if not stat.S_ISREG(file_stat.st_mode) or stat.S_IMODE(file_stat.st_mode) != 0o600 or file_stat.st_uid != 0:
                    raise ReleaseError("credentials_not_protected", "credential file permissions require root ownership and mode 0600", 500)
                lines = stream.read().splitlines()
        except FileNotFoundError:
            # A missing optional file may be accompanied by an instance or
            # container role; continue through the standard role chain.
            lines = []
        except (OSError, UnicodeDecodeError):
            raise ReleaseError("credentials_not_protected", "credential file permissions require root ownership and mode 0600", 500) from None
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
        try:
            timeout = float(env.get("FUWU_S3_TIMEOUT", "300"))
            if not 0 < timeout <= 3600:
                raise ValueError
        except (TypeError, ValueError):
            raise ReleaseError("invalid_storage_config", "storage timeout is invalid", 500)
        return cls(env.get("FUWU_S3_BUCKET", "your-bucket"), env.get("FUWU_S3_REGION", "your-region-1"), env.get("FUWU_S3_ENDPOINT"), credentials, public_reads=env.get("FUWU_S3_PUBLIC_READS", "1") != "0", timeout=timeout)

    def _url(self, key: str) -> str:
        safe = "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))
        parsed = urllib.parse.urlsplit(self.endpoint)
        if parsed.path.rstrip("/").endswith("/" + self.bucket):
            path = parsed.path.rstrip("/") + "/" + safe
        else:
            path = parsed.path.rstrip("/") + "/" + urllib.parse.quote(self.bucket, safe="") + "/" + safe
        if not key:
            path = path.rstrip("/")
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))

    def _signed_request(self, method: str, key: str, body: bytes | None = None, *, content_type: str | None = None, sha256: str | None = None, cache_control: str | None = None, if_none_match: bool = False, if_match: str | None = None) -> urllib.request.Request:
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
        if if_none_match:
            headers["if-none-match"] = "*"
        if if_match is not None:
            headers["if-match"] = if_match
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

    def _request(self, method: str, key: str, body: bytes | None = None, *, content_type: str | None = None, sha256: str | None = None, cache_control: str | None = None, public: bool = False, if_none_match: bool = False, if_match: str | None = None, timeout: float | None = None) -> tuple[int, Mapping[str, str], bytes]:
        if public and self.public_reads:
            req = urllib.request.Request(self._url(key), method=method)
        else:
            req = self._signed_request(method, key, body, content_type=content_type, sha256=sha256, cache_control=cache_control, if_none_match=if_none_match, if_match=if_match)
        try:
            with _open_s3_url(req, timeout=self.timeout if timeout is None else timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReleaseError("object_not_found", "storage object was not found", 404)
            if exc.code in {409, 412}:
                raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
            raise ReleaseError("storage_request_failed", "storage request failed", 502)
        except (urllib.error.URLError, TimeoutError):
            raise ReleaseError("storage_unavailable", "storage service is unavailable", 503)

    def probe(self, *, timeout: float = 3.0) -> dict[str, Any]:
        """Perform a bounded, read-only authenticated HEAD against the bucket.

        Provider responses and exception messages are deliberately discarded;
        this shape is safe to return from an administrative status endpoint.
        """
        try:
            self._request("HEAD", "", timeout=max(0.1, min(float(timeout), 5.0)))
            reachable = True
        except Exception:
            reachable = False
        return {"reachable": reachable, "connectivityCheckedAt": now_iso()}

    def put_object(self, key: str, body: bytes, *, content_type: str = "application/octet-stream", sha256: str | None = None, cache_control: str | None = None, if_none_match: bool = False, if_match: str | None = None) -> dict[str, Any]:
        _, headers, _ = self._request("PUT", key, body, content_type=content_type, sha256=sha256, cache_control=cache_control, if_none_match=if_none_match, if_match=if_match)
        return {"etag": headers.get("ETag")}

    def head_object(self, key: str, *, public: bool = False) -> dict[str, Any]:
        _, headers, _ = self._request("HEAD", key, public=public)
        raw_digest = headers.get("x-amz-meta-sha256") or headers.get("x-fuwu-sha256")
        if not raw_digest:
            raise ReleaseError("storage_digest_missing", "storage object digest is unavailable", 502)
        return {"key": key, "size": int(headers.get("Content-Length", "0")), "sha256": raw_digest, "etag": headers.get("ETag")}

    def get_object(self, key: str, *, public: bool = False) -> bytes:
        _, _, body = self._request("GET", key, public=public)
        return body

    def open_object(self, key: str):
        """Open a signed streaming response for an internal download proxy."""
        request = self._signed_request("GET", key)
        try:
            return _open_s3_url(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ReleaseError("object_not_found", "storage object was not found", 404) from None
            raise ReleaseError("unauthorized", "authentication required", 401) from None

    def head_public(self, key: str) -> dict[str, Any]:
        return self.head_object(key, public=True)

    def get_public(self, key: str) -> bytes:
        return self.get_object(key, public=True)

    def delete_object(self, key: str, *, if_match: str) -> None:
        # Compensation may remove a newly-created channel pointer only. History
        # is never deleted by publication or rollback.
        if not re.search(r"/(?:stable|beta)/latest\.json$", key):
            raise ReleaseError("invalid_pointer_key", "pointer key is invalid", 422)
        self._request("DELETE", key, if_match=if_match)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


_S3_OPENER = urllib.request.build_opener(_NoRedirectHandler())


def _open_s3_url(request: urllib.request.Request, *, timeout: float):
    """Open an S3 request without forwarding signed headers across redirects."""
    return _S3_OPENER.open(request, timeout=timeout)


def _github_url(value: Any, *, api: bool = False) -> str:
    if not isinstance(value, str):
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422) from None
    allowed_hosts = {"api.github.com"} if api else {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    try:
        port = parsed.port
    except ValueError:
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422) from None
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or port not in (None, 443):
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
    return value


class _GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Release asset API requests may redirect within api.github.com before
        # GitHub sends the response to release-assets.githubusercontent.com.
        # Validate both GitHub API and download hosts, and drop the bearer
        # token whenever the redirect crosses hosts.
        try:
            new_host = urllib.parse.urlsplit(newurl).hostname
        except ValueError:
            new_host = None
        _github_url(newurl, api=new_host == "api.github.com")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def _github_asset_url(repository: str, asset: Mapping[str, Any]) -> str:
    """Return the safest URL for a GitHub release asset download.

    GitHub's release metadata includes a numeric asset id and a browser URL.
    The API URL is preferred because it supports authenticated private-release
    downloads with ``Accept: application/octet-stream``. Older or synthetic
    metadata without an id continues to use the validated browser URL.
    """
    asset_id = asset.get("id")
    if asset_id is not None:
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
        return _github_url(f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}", api=True)
    return _github_url(asset.get("browser_download_url"))


def _read_limited(response: Any, maximum: int) -> bytes:
    """Bound metadata/asset reads and reject truncation instead of staging it."""
    try:
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None and int(declared_length) > maximum:
            raise ReleaseError("remote_asset_too_large", "remote release response is too large", 413)
    except (AttributeError, TypeError, ValueError):
        # A malformed or absent length is handled by the bounded read below;
        # it must never turn into an unbounded allocation.
        pass
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ReleaseError("remote_asset_too_large", "remote release response is too large", 413)
    return body


class SQLiteRepository:
    """Repository boundary. Replace this class with MySQL/Redis adapters later."""

    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self._atomic_depth = 0
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _commit(self) -> None:
        if not self._atomic_depth:
            self.conn.commit()

    @contextmanager
    def atomic(self):
        """Group final release, pointer, audit and idempotency rows locally.

        Object storage is a separate system: callers must compensate its writes
        if this transaction fails. The service serializes mutations per process.
        """
        if self._atomic_depth:
            raise RuntimeError("nested repository transaction")
        self.conn.execute("BEGIN IMMEDIATE")
        self._atomic_depth = 1
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        finally:
            self._atomic_depth = 0

    def _migrate(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT NOT NULL, created_at TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', public_api TEXT NOT NULL DEFAULT '', build_instructions TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, version TEXT NOT NULL, channel TEXT NOT NULL, repository TEXT NOT NULL, tag TEXT NOT NULL, revision TEXT NOT NULL, release_id TEXT NOT NULL, published_at TEXT NOT NULL, digest TEXT NOT NULL, manifest_key TEXT NOT NULL, status TEXT NOT NULL, manifest_json TEXT NOT NULL, UNIQUE(project_id, version, channel), FOREIGN KEY(project_id) REFERENCES projects(id));
            CREATE TABLE IF NOT EXISTS artifacts (release_id TEXT NOT NULL, name TEXT NOT NULL, object_key TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(release_id, name), FOREIGN KEY(release_id) REFERENCES releases(id));
            CREATE TABLE IF NOT EXISTS latest (project_id TEXT NOT NULL, channel TEXT NOT NULL, release_pk TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL, pointer_key TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(project_id, channel), FOREIGN KEY(release_pk) REFERENCES releases(id));
            CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL, project_id TEXT, version TEXT, channel TEXT, actor TEXT NOT NULL, request_id TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS idempotency (request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
            """
        )
        project_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(projects)")}
        for name, definition in (
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("public_api", "TEXT NOT NULL DEFAULT ''"),
            ("build_instructions", "TEXT NOT NULL DEFAULT ''"),
            ("active", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in project_columns:
                self.conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
        idempotency_columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(idempotency)")}
        for name in ("project_id", "channel", "actor", "fingerprint"):
            if name not in idempotency_columns:
                self.conn.execute(f"ALTER TABLE idempotency ADD COLUMN {name} TEXT")
        self._commit()

    def ensure_project(self, project_id: str, *, name: str | None = None, repository: str = "") -> dict[str, Any]:
        project_id = _project_value(validate_project_id, project_id)
        normalized_repository = _project_value(normalize_repository, repository)
        row = self.get_project(project_id)
        if row:
            if not row["active"]:
                raise ReleaseError("project_inactive", "project is inactive", 403)
            if _project_value(normalize_repository, row["repository"]) != normalized_repository:
                raise ReleaseError("project_repository_mismatch", "repository does not match registered project", 409)
            return row
        # Legacy automation can still register on its first validated publish.
        return self.create_project({"id": project_id, "name": name or project_id, "repository": normalized_repository})

    @staticmethod
    def _project_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["active"] = bool(result["active"])
        return result

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        project_id = _project_value(validate_project_id, project_id)
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project_dict(row) if row else None

    def create_project(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _project_value(project_create_values, request)
        if self.get_project(value["id"]):
            raise ReleaseError("project_conflict", "project already exists", 409)
        value.update({"created_at": now_iso(), "active": True})
        try:
            self.conn.execute("INSERT INTO projects(id,name,repository,created_at,description,public_api,build_instructions,active) VALUES (:id,:name,:repository,:created_at,:description,:public_api,:build_instructions,:active)", value)
            self._commit()
        except sqlite3.IntegrityError:
            if not self._atomic_depth:
                self.conn.rollback()
            raise ReleaseError("project_conflict", "project already exists", 409) from None
        return value

    def update_project(self, project_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        project_id = _project_value(validate_project_id, project_id)
        changes = _project_value(project_update_values, request)
        if self.get_project(project_id) is None:
            raise ReleaseError("project_not_found", "project was not found", 404)
        assignments = ",".join(name + "=?" for name in changes)
        self.conn.execute("UPDATE projects SET " + assignments + " WHERE id=?", [*changes.values(), project_id])
        self._commit()
        return self.get_project(project_id)

    def list_projects(self, project_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        if project_ids is None:
            rows = self.conn.execute("SELECT * FROM projects ORDER BY id")
        else:
            ids = sorted({_project_value(validate_project_id, project_id) for project_id in project_ids})
            if not ids:
                return []
            rows = self.conn.execute("SELECT * FROM projects WHERE id IN (" + ",".join("?" for _ in ids) + ") ORDER BY id", ids)
        return [self._project_dict(row) for row in rows]

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
        self._commit()
        return dict(value)

    def update_release_status(self, release_pk: str, status: str) -> None:
        self.conn.execute("UPDATE releases SET status=? WHERE id=?", (status, release_pk))
        self._commit()

    def set_latest(self, value: Mapping[str, Any]) -> None:
        self.conn.execute("INSERT INTO latest(project_id,channel,release_pk,version,digest,pointer_key,updated_at) VALUES (:project_id,:channel,:release_pk,:version,:digest,:pointer_key,:updated_at) ON CONFLICT(project_id,channel) DO UPDATE SET release_pk=excluded.release_pk,version=excluded.version,digest=excluded.digest,pointer_key=excluded.pointer_key,updated_at=excluded.updated_at", value)
        self._commit()

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

    def list_audit(self, project_id: str | None = None, limit: int = 100, channel: str | None = None) -> list[dict[str, Any]]:
        predicates, args = [], []
        if project_id is not None:
            predicates.append("project_id=?")
            args.append(_project_value(validate_project_id, project_id))
        if channel is not None:
            predicates.append("channel=?")
            args.append(validate_channel(channel))
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        rows = self.conn.execute("SELECT * FROM audit" + where + " ORDER BY id DESC LIMIT ?", [*args, min(max(limit, 1), 500)])
        output = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item.pop("details_json"))
            output.append(item)
        return output

    def audit_event(self, event_type: str, *, project_id: str | None, version: str | None, channel: str | None, actor: str, request_id: str, details: Mapping[str, Any]) -> None:
        # Details are intentionally supplied by the service after sensitive fields have been removed.
        self.conn.execute("INSERT INTO audit(event_type,project_id,version,channel,actor,request_id,details_json,created_at) VALUES (?,?,?,?,?,?,?,?)", (event_type, project_id, version, channel, actor, request_id, json.dumps(details, sort_keys=True), now_iso()))
        self._commit()

    def idempotency_get(self, request_id: str, *, operation: str | None = None, project_id: str | None = None, channel: str | None = None, actor: str | None = None, fingerprint: str | None = None) -> tuple[int, dict[str, Any]] | None:
        row = self.conn.execute("SELECT * FROM idempotency WHERE request_id=?", (request_id,)).fetchone()
        if row and operation is not None:
            expected = {"operation": operation, "project_id": project_id, "channel": channel, "actor": actor, "fingerprint": fingerprint}
            if any(row[key] != value for key, value in expected.items()):
                raise ReleaseError("idempotency_conflict", "request identifier is already bound to a different operation", 409)
        return (int(row["status_code"]), json.loads(row["response_json"])) if row else None

    def idempotency_put(self, request_id: str, operation: str, status_code: int, response: Mapping[str, Any], *, project_id: str | None = None, channel: str | None = None, actor: str | None = None, fingerprint: str | None = None) -> None:
        self.conn.execute("INSERT INTO idempotency(request_id,operation,status_code,response_json,created_at,project_id,channel,actor,fingerprint) VALUES (?,?,?,?,?,?,?,?,?)", (request_id, operation, status_code, json.dumps(response, sort_keys=True), now_iso(), project_id, channel, actor, fingerprint))
        self._commit()

    @staticmethod
    def _release_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["manifest"] = json.loads(item.pop("manifest_json"))
        return item


class RBAC:
    PERMISSIONS = {"release.storage.read", "release.storage.publish", "release.storage.rollback", "users.manage", "projects.manage", "logs.read", "settings.manage"}
    SESSION_PREFIX = "fuwu-session-v1."
    SESSION_LIFETIME_SECONDS = 3600

    def __init__(self, tokens: Mapping[str, Iterable[str]] | None = None, *, account_store_factory: Any = None):
        self.tokens = {token: (set(perms) & self.PERMISSIONS) | ({"superadmin"} if "superadmin" in set(perms) else set()) for token, perms in (tokens or {}).items()}
        self._account_store_factory = account_store_factory
        self._account_store = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RBAC":
        env = os.environ if env is None else env
        raw = env.get("FUWU_AUTH_TOKENS", "")
        tokens: dict[str, Any] = {}
        try:
            parsed = json.loads(raw or "{}")
            if isinstance(parsed, Mapping):
                tokens = {str(token): values for token, values in parsed.items() if isinstance(values, list)}
        except json.JSONDecodeError:
            pass
        database = env.get("FUWU_DATABASE", "fuwu.sqlite3")
        def accounts():
            from .accounts import get_account_store
            return get_account_store(database)
        return cls(tokens, account_store_factory=accounts)

    @property
    def account_store(self):
        """Only environment-backed RBAC enables persistent account lookup."""
        if self._account_store_factory is None:
            return None
        if self._account_store is None:
            self._account_store = self._account_store_factory()
        return self._account_store

    def public_user(self, authorization: str | None) -> dict[str, Any] | None:
        if authorization and authorization.startswith("Bearer fuwu-account-v1."):
            store = self.account_store
            if store is not None:
                return store.authenticate(authorization[7:].strip())
            raise ReleaseError("unauthorized", "authentication required", 401)
        self.authenticate(authorization)
        return None

    def project_scope(self, authorization: str | None) -> list[str] | None:
        user = self.public_user(authorization)
        return user["project_ids"] if user is not None else None

    def require_project(self, authorization: str | None, project_id: str) -> None:
        user = self.public_user(authorization)
        if user is not None:
            self.account_store.require_project(user, project_id)

    def authenticate(self, authorization: str | None) -> tuple[str, set[str]]:
        if not authorization or not authorization.startswith("Bearer "):
            raise ReleaseError("unauthorized", "authentication required", 401)
        token = authorization[7:].strip()
        if token in self.tokens:
            return self._actor(token), self.tokens[token].copy()
        if token.startswith("fuwu-account-v1."):
            user = self.public_user(authorization)
            permissions = set(user["permissions"])
            if "superadmin" in user["roles"]:
                permissions.add("superadmin")
            return "user:" + user["id"], permissions
        if token.startswith(self.SESSION_PREFIX):
            return self._authenticate_session(token)
        raise ReleaseError("unauthorized", "authentication required", 401)

    @staticmethod
    def _actor(token: str) -> str:
        # Audit identities must never contain even a prefix of the bearer secret.
        return "token-" + hashlib.sha256(token.encode()).hexdigest()[:16]

    def issue_session(self, token: str) -> tuple[str, int]:
        """Exchange a configured management token for a short-lived UI session.

        Each session is signed by its management token. No management secret or
        permission claim is embedded in the session; permissions are reloaded
        from current server configuration on every authenticated request.
        """
        if not token or token not in self.tokens:
            raise ReleaseError("unauthorized", "authentication required", 401)
        expires_at = int(time.time()) + self.SESSION_LIFETIME_SECONDS
        data = {"id": self._actor(token), "exp": expires_at, "nonce": os.urandom(16).hex()}
        encoded = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()
        signature = hmac.new(token.encode(), (self.SESSION_PREFIX + encoded).encode(), hashlib.sha256).hexdigest()
        return self.SESSION_PREFIX + encoded + "." + signature, expires_at

    def _authenticate_session(self, session: str) -> tuple[str, set[str]]:
        try:
            if len(session) > 1024:
                raise ValueError
            encoded, signature = session[len(self.SESSION_PREFIX):].split(".")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", encoded) or not re.fullmatch(r"[a-f0-9]{64}", signature):
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if not isinstance(data, dict) or type(data.get("exp")) is not int:
                raise ValueError
            now = int(time.time())
            if not now < data["exp"] <= now + self.SESSION_LIFETIME_SECONDS:
                raise ValueError
            for token, permissions in self.tokens.items():
                if self._actor(token) != data.get("id"):
                    continue
                expected = hmac.new(token.encode(), (self.SESSION_PREFIX + encoded).encode(), hashlib.sha256).hexdigest()
                if hmac.compare_digest(expected, signature):
                    return self._actor(token), permissions.copy()
        except (ValueError, TypeError, UnicodeDecodeError):
            pass
        raise ReleaseError("unauthorized", "authentication required", 401)

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
        self._mutation_lock = threading.RLock()

    def create_project(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        """Register metadata; HTTP adapters enforce projects.manage."""
        with self._mutation_lock, self.repository.atomic():
            project = self.repository.create_project(request)
            self.repository.audit_event("project_created", project_id=project["id"], version=None, channel=None, actor=actor, request_id=request_id, details={"status": "created", "repository": project["repository"]})
            return 201, project

    def public_download(self, project: str, version: str, name: str) -> dict[str, Any]:
        """Resolve a published stable asset for the anonymous Fuwu route."""
        project = _project_value(validate_project_id, project)
        version = validate_version(version)
        name = safe_relative_path(name)
        if "/" in name:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        release = self.repository.get_release(project, version, "stable")
        if not release or release.get("status") != "published":
            raise ReleaseError("object_not_found", "download object was not found", 404)
        if name == "manifest.json":
            body = json_bytes(release["manifest"])
            return {"name": name, "key": release["manifest_key"], "size": len(body), "sha256": safe_digest(body), "body": body}
        artifacts = {item["name"]: item for item in self.repository.list_artifacts(release["id"])}
        artifact = artifacts.get(name)
        if not artifact:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        return {"name": name, "key": artifact["object_key"], "size": artifact["size"], "sha256": artifact["sha256"]}

    def public_latest_download(self, project: str, channel: str) -> dict[str, Any]:
        """按数据库发行绑定解析当前 stable 指针，并返回存储中的原始字节。

        客户端只能提供项目和渠道；指针键及不可变 manifest 键均由数据库记录
        推导，并在返回前校验两个存储对象。这样匿名接口不会变成任意对象代理。
        """
        project = _project_value(validate_project_id, project)
        channel = validate_channel(channel)
        if channel != "stable":
            raise ReleaseError("object_not_found", "download object was not found", 404)
        registered = self.repository.get_project(project)
        if not registered or not registered["active"]:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        latest = self.repository.get_latest(project, channel)
        if not latest:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        try:
            version = validate_version(latest["version"])
            _, manifest_key, latest_key = self._keys(project, version, channel)
        except (ReleaseError, KeyError, TypeError):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502) from None
        if latest.get("pointer_key") != latest_key:
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        release = self.repository.get_release_by_id(str(latest.get("release_pk", "")))
        if (
            not release
            or release.get("project_id") != project
            or release.get("channel") != channel
            or release.get("version") != version
            or release.get("status") != "published"
            or release.get("digest") != latest.get("digest")
            or release.get("manifest_key") != manifest_key
        ):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)

        try:
            manifest_head = self.store.head_object(manifest_key)
            manifest_body = self.store.get_object(manifest_key)
            if manifest_head.get("sha256") != safe_digest(manifest_body) or int(manifest_head.get("size", -1)) != len(manifest_body):
                raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
            pointer_head = self.store.head_object(latest_key)
            pointer_body = self.store.get_object(latest_key)
            if pointer_head.get("sha256") != safe_digest(pointer_body) or int(pointer_head.get("size", -1)) != len(pointer_body):
                raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
            pointer = json.loads(pointer_body)
        except ReleaseError as error:
            if error.code == "object_not_found":
                raise ReleaseError("object_not_found", "download object was not found", 404) from None
            raise
        except (TypeError, ValueError, UnicodeDecodeError):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502) from None

        if not isinstance(pointer, Mapping):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        manifest_digest = safe_digest(manifest_body)
        if manifest_body != json_bytes(release["manifest"]):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        manifest = release["manifest"]
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("project") != project
            or manifest.get("repository") != registered["repository"]
            or manifest.get("version") != version
            or manifest.get("tag") != release["tag"]
            or manifest.get("revision") != release["revision"]
            or str(manifest.get("releaseId")) != str(release["release_id"])
        ):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        expected_pointer = {
            "schemaVersion": 1,
            "project": project,
            "channel": channel,
            "version": release["version"],
            "tag": release["tag"],
            "revision": release["revision"],
            "releaseId": manifest["releaseId"],
            "manifestKey": manifest_key,
            "manifestSha256": manifest_digest,
            "publishedAt": release["published_at"],
            "immutable": True,
            "verificationSource": manifest.get("verificationSource"),
        }
        if json_bytes(pointer) != json_bytes(expected_pointer):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        return {
            "name": "latest.json",
            "key": latest_key,
            "size": len(pointer_body),
            "sha256": safe_digest(pointer_body),
            "body": pointer_body,
        }

    def open_public_download(self, descriptor: Mapping[str, Any]):
        """Open a published asset for streaming without exposing storage credentials."""
        body = descriptor.get("body")
        if isinstance(body, bytes):
            from io import BytesIO
            return BytesIO(body)
        if isinstance(self.store, S3ObjectStore):
            return self.store.open_object(str(descriptor["key"]))
        from io import BytesIO
        return BytesIO(self.store.get_object(str(descriptor["key"])))

    def update_project(self, project_id: str, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        """Change display metadata/active only; repository binding is immutable."""
        with self._mutation_lock, self.repository.atomic():
            project = self.repository.update_project(project_id, request)
            self.repository.audit_event("project_updated", project_id=project["id"], version=None, channel=None, actor=actor, request_id=request_id, details={"status": "updated", "changed_fields": sorted(request), "active": project["active"]})
            return 200, project

    @staticmethod
    def _idempotency_scope(operation: str, request: Mapping[str, Any], *, project: str, channel: str, actor: str) -> dict[str, str]:
        normalized = {key: value for key, value in request.items() if key != "requestId"}
        normalized["channel"] = channel
        if operation == "publish":
            normalized["staging_ref"] = request.get("staging_ref") or request.get("job_ref")
            normalized.pop("job_ref", None)
        return {"operation": operation, "project_id": project, "channel": channel, "actor": actor, "fingerprint": safe_digest(json_bytes(normalized))}

    def _keys(self, project: str, version: str, channel: str) -> tuple[str, str, str]:
        # Immutable objects use releases/{project}/vX.Y.Z paths.
        immutable = f"{self.prefix}/{project}/v{version}"
        manifest_key = f"{immutable}/manifest.json"
        latest_key = f"{self.prefix}/{project}/{channel}/latest.json"
        return immutable, manifest_key, latest_key

    def _put_immutable(self, key: str, body: bytes, *, content_type: str, sha256: str) -> dict[str, Any]:
        """Write an immutable object only when the key is absent or identical."""
        if safe_digest(body) != sha256:
            raise ReleaseError("checksum_mismatch", "asset checksum does not match", 422)
        try:
            self.store.put_object(key, body, content_type=content_type, sha256=sha256, if_none_match=True)
        except ReleaseError as error:
            if error.code != "object_precondition_failed":
                raise
            existing = self.store.head_object(key)
            if existing.get("sha256") != sha256 or int(existing.get("size", -1)) != len(body):
                raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
            return existing
        return self.store.head_object(key)

    def _put_release_manifest(self, key: str, candidate: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Reuse the original immutable bytes when only the target channel differs.

        Older manifests retain their source channel. Conditional creation and
        full HEAD/GET verification prevent a promotion from overwriting them.
        """
        candidate_body = json_bytes(candidate)
        try:
            self.store.put_object(key, candidate_body, content_type="application/json", sha256=safe_digest(candidate_body), if_none_match=True)
        except ReleaseError as error:
            if error.code != "object_precondition_failed":
                raise
        head = self.store.head_object(key)
        body = self.store.get_object(key)
        if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
            raise ReleaseError("storage_verification_failed", "immutable manifest verification failed", 502)
        try:
            stored = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409) from None
        expected = {name: value for name, value in candidate.items() if name != "channel"}
        actual = {name: value for name, value in stored.items() if name != "channel"} if isinstance(stored, dict) else None
        if json_bytes(actual) != json_bytes(expected):
            raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
        return stored, body

    @staticmethod
    def _latest_pointer(manifest: Mapping[str, Any], manifest_key: str, manifest_digest: str, *, channel: str) -> dict[str, Any]:
        """Build a v1 latest pointer without duplicating manifest fields."""
        return {
            "schemaVersion": 1,
            "project": manifest["project"],
            "channel": channel,
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

    def _pointer_snapshot(self, key: str) -> dict[str, Any] | None:
        try:
            head = self.store.head_object(key)
        except ReleaseError as error:
            if error.code == "object_not_found":
                return None
            raise
        body = self.store.get_object(key)
        if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        if not head.get("etag"):
            raise ReleaseError("storage_precondition_unavailable", "storage conditional writes are unavailable", 502)
        return {**head, "body": body}

    def _restore_pointer(self, key: str, before: dict[str, Any] | None, body: bytes, written_etag: str | None) -> str:
        """Compensate this write using its ETag; never clobber another writer."""
        if not written_etag:
            current = self._pointer_snapshot(key)
            if (current is None and before is None) or (current and before and current["body"] == before["body"]):
                return "unchanged"
            if current is None or current["body"] != body:
                raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
            written_etag = current["etag"]
        if before is None:
            self.store.delete_object(key, if_match=written_etag)
            try:
                self.store.head_object(key)
            except ReleaseError as error:
                if error.code == "object_not_found":
                    return "removed"
                raise
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        restored = self.store.put_object(key, before["body"], content_type="application/json", sha256=before["sha256"], cache_control="no-store, max-age=0", if_match=written_etag)
        head = self.store.head_object(key)
        if head.get("sha256") != before["sha256"] or head.get("size") != len(before["body"]):
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        if restored.get("etag") and head.get("etag") != restored["etag"]:
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        return "restored"

    def _finalize_pointer(self, key: str, body: bytes, finalize, *, operation: str, project: str, version: str, channel: str, actor: str, request_id: str) -> None:
        """Local DB transaction plus conditional S3 write and compensation.

        S3 and SQLite cannot share a transaction. A process crash or persistent
        storage outage can still require operator reconciliation; never report
        such an outcome as a successful publication.
        """
        before = self._pointer_snapshot(key)
        written_etag = None
        attempted = False
        try:
            with self.repository.atomic():
                attempted = True
                result = self.store.put_object(key, body, content_type="application/json", sha256=safe_digest(body), cache_control="no-store, max-age=0", if_none_match=before is None, if_match=before["etag"] if before else None)
                written_etag = result.get("etag")
                head = self.store.head_object(key)
                if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
                    raise ReleaseError("storage_verification_failed", "uploaded latest pointer verification failed", 502)
                if written_etag and head.get("etag") != written_etag:
                    raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
                finalize()
        except Exception as error:
            if isinstance(error, ReleaseError) and error.code == "object_precondition_failed" and written_etag is None:
                # A rejected conditional PUT did not write any pointer bytes.
                attempted = False
            if attempted:
                recovery = "failed"
                try:
                    recovery = self._restore_pointer(key, before, body, written_etag)
                except Exception:
                    pass
                self.repository.audit_event("pointer_recovery", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": recovery, "operation": operation, "pointer_key": key, "before_digest": before["sha256"] if before else None, "attempted_digest": safe_digest(body)})
                if recovery == "failed":
                    raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503) from None
            if isinstance(error, ReleaseError):
                raise
            raise ReleaseError("publication_finalize_failed", "release finalization failed", 503) from None

    def _read_staging(self, job_ref: str, request: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Path, bytes]:
        job_dir = protected_job_path(self.staging_dir, job_ref)
        manifest_path = job_dir / "manifest.json"
        sums_path = job_dir / "SHA256SUMS"
        if not job_dir.is_dir() or not manifest_path.is_file() or not sums_path.is_file():
            raise ReleaseError("staging_not_found", "staging job was not found", 404)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            sums_bytes = sums_path.read_bytes()
            sums_text = sums_bytes.decode("utf-8")
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
            checksum_path = safe_relative_path(parts[1].lstrip("*"))
            if checksum_path in checksums:
                raise ReleaseError("invalid_checksums", "SHA256SUMS contains duplicate paths", 422)
            checksums[checksum_path] = parts[0].lower()
        if not checksums:
            raise ReleaseError("invalid_checksums", "SHA256SUMS is empty", 422)
        asset_names = [Path(entry["path"]).name for entry in entries]
        if len(asset_names) != len(set(asset_names)):
            raise ReleaseError("invalid_manifest", "asset names must be unique", 422)
        declared_checksums = manifest.get("checksums", {})
        if not isinstance(declared_checksums, Mapping):
            raise ReleaseError("invalid_checksums", "manifest checksums are invalid", 422)
        if declared_checksums.get("sha256") is not None and declared_checksums["sha256"] != safe_digest(sums_bytes):
            raise ReleaseError("checksum_mismatch", "checksum file digest does not match", 422)
        declared_entries = declared_checksums.get("entries")
        if declared_entries is not None:
            if not isinstance(declared_entries, Mapping):
                raise ReleaseError("invalid_checksums", "manifest checksums are invalid", 422)
            normalized_entries = {str(name): str(digest).lower() for name, digest in declared_entries.items()}
            if normalized_entries != checksums:
                raise ReleaseError("checksum_mismatch", "manifest checksums do not match SHA256SUMS", 422)
        declared_asset_paths = {entry["path"] for entry in entries}
        if set(checksums) != declared_asset_paths:
            raise ReleaseError("invalid_checksums", "SHA256SUMS paths do not match manifest assets", 422)
        for entry in entries:
            path = entry["path"]
            expected = checksums.get(path)
            declared = entry.get("sha256")
            if not expected or not isinstance(declared, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", declared):
                raise ReleaseError("invalid_checksums", "asset checksum is missing", 422)
            if declared.lower() != expected:
                raise ReleaseError("checksum_mismatch", "manifest checksum does not match SHA256SUMS", 422)
            if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] < 0:
                raise ReleaseError("invalid_manifest", "asset size is required", 422)
            source = (job_dir / path).resolve()
            if source.parent != job_dir.resolve() and job_dir.resolve() not in source.parents:
                raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
            if not source.is_file():
                raise ReleaseError("staging_asset_missing", "staging asset is missing", 422)
            body = source.read_bytes()
            digest = safe_digest(body)
            if digest != expected:
                raise ReleaseError("checksum_mismatch", "asset checksum does not match", 422)
            if len(body) != entry["size"]:
                raise ReleaseError("size_mismatch", "asset size does not match", 422)
            entry["sha256"] = digest
            entry["source"] = source
            entry["checksums"] = checksums
        return manifest, entries, job_dir, sums_bytes

    def publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        with self._mutation_lock:
            return self._publish(request, actor=actor, request_id=request_id)

    def stage_remote_release(self, request: Mapping[str, Any], *, actor: str, request_id: str,
                             github_token: str | None = None, project_id: str | None = None) -> tuple[int, dict[str, Any]]:
        """Download a GitHub release into a protected staging job.

        Remote bytes are always put through the existing ``publish`` validation
        path.  The optional token is supplied by a server-side authorization
        store; it is intentionally not accepted as part of ``request``.
        """
        with self._mutation_lock:
            return self._stage_remote_release(request, actor=actor, request_id=request_id,
                                              github_token=github_token, project_id=project_id)

    def _stage_remote_release(
        self,
        request: Mapping[str, Any],
        *,
        actor: str,
        request_id: str,
        github_token: str | None = None,
        project_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        allowed = {"repository", "tag", "staging_ref", "assets", "manifest"}
        if not isinstance(request, Mapping) or set(request) - allowed or not isinstance(request.get("repository"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", request["repository"]):
            raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
        tag = request.get("tag")
        job_ref = request.get("staging_ref")
        assets = request.get("assets")
        manifest = request.get("manifest")
        if not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag) or not isinstance(job_ref, str) or not JOB_RE.fullmatch(job_ref):
            raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
        if not isinstance(assets, list) or not assets or len(assets) != len(set(assets)) or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", name) for name in assets):
            raise ReleaseError("invalid_remote_stage_request", "remote asset list is invalid", 422)
        if not isinstance(manifest, Mapping):
            raise ReleaseError("invalid_remote_stage_request", "remote manifest is required", 422)
        manifest, manifest_entries = validate_manifest(manifest, {"tag": tag})
        manifest_repository = _project_value(normalize_repository, manifest.get("repository"))
        if manifest_repository != request["repository"].lower():
            raise ReleaseError("manifest_mismatch", "manifest repository does not match request", 422)
        # Remote staging writes each selected asset at the job root.  Require
        # manifest paths to be the same safe filenames so publish cannot later
        # look for an uncreated nested path or silently collapse two names.
        manifest_names = {entry["path"] for entry in manifest_entries}
        if manifest_names != set(assets):
            raise ReleaseError("manifest_mismatch", "remote assets do not match manifest", 422)
        if github_token is not None and (not isinstance(github_token, str) or not github_token.strip() or len(github_token) > 512):
            raise ReleaseError("invalid_github_authorization", "GitHub authorization is invalid", 422)
        job_dir = protected_job_path(self.staging_dir, job_ref)
        if job_dir.exists():
            raise ReleaseError("staging_conflict", "staging job already exists", 409)
        api = _github_url(f"https://api.github.com/repos/{request['repository']}/releases/tags/{tag}", api=True)
        opener = urllib.request.build_opener(_GitHubRedirectHandler())
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "fuwu-release-storage"}
        if github_token:
            headers["Authorization"] = "Bearer " + github_token.strip()
        req = urllib.request.Request(api, headers=headers)
        try:
            with opener.open(req, timeout=30) as response:
                release = json.loads(_read_limited(response, 2_000_000))
        except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("remote_release_unavailable", "remote release metadata is unavailable", 502) from None
        if not isinstance(release, Mapping) or release.get("tag_name") != tag or release.get("draft") or release.get("prerelease"):
            raise ReleaseError("remote_release_invalid", "remote release is not publishable", 422)
        remote_assets = release.get("assets")
        if not isinstance(remote_assets, list):
            raise ReleaseError("remote_release_invalid", "remote release assets are invalid", 422)
        by_name: dict[str, Mapping[str, Any]] = {}
        for item in remote_assets:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                by_name[item["name"]] = item
        for name in assets:
            if name not in by_name:
                raise ReleaseError("remote_asset_missing", "remote release asset is missing", 422)
            _github_asset_url(request["repository"], by_name[name])
        checksum_name = "SHA256SUMS"
        if checksum_name not in by_name:
            raise ReleaseError("remote_checksums_missing", "remote release SHA256SUMS is missing", 422)
        _github_asset_url(request["repository"], by_name[checksum_name])
        if any(name not in by_name for name in assets):
            raise ReleaseError("remote_asset_missing", "remote release asset is missing", 422)
        job_dir.mkdir(mode=0o700)
        try:
            download_headers = {"Accept": "application/octet-stream", "User-Agent": "fuwu-release-storage"}
            if github_token:
                download_headers["Authorization"] = "Bearer " + github_token.strip()
            for name in assets:
                target = job_dir / name
                download = urllib.request.Request(_github_asset_url(request["repository"], by_name[name]), headers=download_headers)
                with opener.open(download, timeout=120) as response:
                    target.write_bytes(_read_limited(response, 1_500_000_000))
                target.chmod(0o600)
            checksum_req = urllib.request.Request(_github_asset_url(request["repository"], by_name[checksum_name]), headers=download_headers)
            with opener.open(checksum_req, timeout=30) as response:
                try:
                    upstream_sums = _read_limited(response, 2_000_000).decode("utf-8")
                except UnicodeDecodeError:
                    raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS is invalid", 422) from None
            upstream: dict[str, str] = {}
            for line in upstream_sums.splitlines():
                parts = line.strip().split(maxsplit=1)
                if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                    name = Path(parts[1].lstrip("*")).name
                    if name in upstream:
                        raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS contains duplicate assets", 422)
                    upstream[name] = parts[0].lower()
            if any(name not in upstream for name in assets):
                raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS does not cover selected assets", 422)
            checksums = []
            for name in assets:
                body = (job_dir / name).read_bytes()
                digest = hashlib.sha256(body).hexdigest()
                if digest != upstream[name]:
                    raise ReleaseError("remote_checksum_mismatch", "remote asset checksum does not match SHA256SUMS", 422)
                declared = next(entry for entry in manifest_entries if Path(entry["path"]).name == name)
                if declared.get("sha256") and declared["sha256"].lower() != digest:
                    raise ReleaseError("remote_checksum_mismatch", "remote asset checksum does not match manifest", 422)
                if declared.get("size") is not None and declared["size"] != len(body):
                    raise ReleaseError("remote_size_mismatch", "remote asset size does not match manifest", 422)
                checksums.append(f"{digest}  {name}")
            manifest_path = job_dir / "manifest.json"
            manifest_path.write_text(json.dumps(dict(manifest), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            manifest_path.chmod(0o600)
            sums_path = job_dir / "SHA256SUMS"
            sums_path.write_text("\n".join(checksums) + "\n", encoding="utf-8")
            sums_path.chmod(0o600)
        except ReleaseError:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        except (OSError, urllib.error.URLError):
            shutil.rmtree(job_dir, ignore_errors=True)
            raise ReleaseError("remote_asset_unavailable", "remote release asset is unavailable", 502) from None
        self.repository.audit_event("remote_stage", project_id=project_id, version=None, channel=None, actor=actor, request_id=request_id, details={"repository": request["repository"], "tag": tag, "staging_ref": job_ref, "assets": assets})
        return 201, {"status": "staged", "job": {"id": request_id, "status": "staged", "progress": 100}, "staging_ref": job_ref}

    def _publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        allowed = {"project", "channel", "version", "tag", "revision", "staging_ref", "job_ref", "requestId"}
        if not isinstance(request, Mapping) or set(request) - allowed:
            raise ReleaseError("invalid_publish_request", "publish request is invalid", 422)
        project = _project_value(validate_project_id, request.get("project"))
        version = validate_version(request.get("version"))
        channel = validate_channel(request.get("channel", "stable"))
        job_ref = request.get("staging_ref") or request.get("job_ref")
        if not isinstance(job_ref, str):
            raise ReleaseError("invalid_staging_reference", "staging reference is required", 422)
        registered = self.repository.get_project(project)
        if registered and not registered["active"]:
            raise ReleaseError("project_inactive", "project is inactive", 403)
        scope = self._idempotency_scope("publish", request, project=project, channel=channel, actor=actor)
        cached = self.repository.idempotency_get(request_id, **scope)
        if cached:
            _, response = cached
            return 200, response
        try:
            manifest, entries, job_dir, sums_bytes = self._read_staging(job_ref, {**request, "version": version, "project": project})
            self.repository.ensure_project(project, repository=manifest["repository"])
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
            self.repository.idempotency_put(request_id, "publish", 200, response, **{key: value for key, value in scope.items() if key != "operation"})
            return 200, response
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
            published, manifest_body = self._put_release_manifest(manifest_key, published)
            release_pk = uuid.uuid4().hex
            for row in artifact_rows:
                row["release_id"] = release_pk
            # The latest pointer is only written after all immutable objects are verified.
            pointer_body = json_bytes(self._latest_pointer(published, manifest_key, safe_digest(manifest_body), channel=channel))
            release = {"id": release_pk, "project_id": project, "version": version, "channel": channel, "repository": published["repository"], "tag": published["tag"], "revision": published["revision"], "release_id": published["releaseId"], "published_at": published.get("publishedAt") or now_iso(), "digest": release_digest, "manifest_key": manifest_key, "status": "published", "manifest_json": json.dumps(published, sort_keys=True)}
            response = {"status": "published", "job": {"id": request_id, "operation": "publish", "status": "published", "progress": 100}, "release": self._public_release({**release, "manifest": published})}
            def finalize():
                self.repository.save_release(release, artifact_rows)
                self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": release_pk, "version": version, "digest": release_digest, "pointer_key": latest_key, "updated_at": now_iso()})
                self.repository.audit_event("publish", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "published", "progress": 100, "immutable_prefix": immutable, "manifest_key": manifest_key, "latest_key": latest_key, "digest": release_digest, "artifacts": [{"key": row["object_key"], "size": row["size"], "sha256": row["sha256"]} for row in artifact_rows]})
                self.repository.idempotency_put(request_id, "publish", 201, response, **{key: value for key, value in scope.items() if key != "operation"})
            self._finalize_pointer(latest_key, pointer_body, finalize, operation="publish", project=project, version=version, channel=channel, actor=actor, request_id=request_id)
            return 201, response
        except ReleaseError:
            self.repository.audit_event("publish_failed", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "digest": release_digest})
            raise

    def rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        with self._mutation_lock:
            return self._rollback(request, actor=actor, request_id=request_id)

    def _rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        allowed = {"project", "channel", "release_id", "version", "confirm", "reason", "requestId"}
        if not isinstance(request, Mapping) or set(request) - allowed:
            raise ReleaseError("invalid_rollback_request", "rollback request is invalid", 422)
        project = _project_value(validate_project_id, request.get("project"))
        channel = validate_channel(request.get("channel", "stable"))
        scope = self._idempotency_scope("rollback", request, project=project, channel=channel, actor=actor)
        cached = self.repository.idempotency_get(request_id, **scope)
        if cached:
            _, response = cached
            return 200, response
        if request.get("confirm") is not True:
            raise ReleaseError("confirmation_required", "rollback confirmation is required", 400)
        reason = str(request.get("reason") or "").strip()
        if not reason:
            raise ReleaseError("invalid_reason", "rollback reason is required", 422)
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
        pointer = json_bytes(self._latest_pointer(manifest, manifest_key, safe_digest(manifest_body), channel=channel))
        response = {"status": "rolled_back", "job": {"id": request_id, "operation": "rollback", "status": "rolled_back", "progress": 100}, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"], "channel": channel}}
        def finalize():
            self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": target["id"], "version": target["version"], "digest": target["digest"], "pointer_key": pointer_key, "updated_at": now_iso()})
            self.repository.audit_event("rollback", project_id=project, version=target["version"], channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "rolled_back", "progress": 100, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"]}, "reason": reason[:500], "pointer_key": pointer_key})
            self.repository.idempotency_put(request_id, "rollback", 200, response, **{key: value for key, value in scope.items() if key != "operation"})
        try:
            self._finalize_pointer(pointer_key, pointer, finalize, operation="rollback", project=project, version=target["version"], channel=channel, actor=actor, request_id=request_id)
        except ReleaseError:
            self.repository.audit_event("rollback_failed", project_id=project, version=target["version"], channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "before": response["before"], "after": response["after"], "pointer_key": pointer_key})
            raise
        return 200, response

    def status(self, project_id: str | None = None, channel: str | None = None) -> dict[str, Any]:
        if project_id is not None:
            project_id = _project_value(validate_project_id, project_id)
        if channel is not None:
            channel = validate_channel(channel)
        projects = self.repository.list_projects([project_id] if project_id is not None else None)
        releases = self.repository.list_releases(project_id, channel)
        if project_id is None and channel is not None:
            release_project_ids = {release["project_id"] for release in releases}
            projects = [project for project in projects if project["id"] in release_project_ids]
        return {
            "provider": type(self.store).__name__,
            "prefix": self.prefix,
            "project": project_id,
            "channel": channel,
            "projects": len(projects),
            "releases": len(releases),
            "jobs": {
                "validation": self._job_summary("validation", project_id, channel),
                "publish": self._job_summary("publish", project_id, channel),
                "rollback": self._job_summary("rollback", project_id, channel),
            },
        }

    def releases(self, project_id: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        if project_id is not None:
            project_id = _project_value(validate_project_id, project_id)
        if channel is not None:
            channel = validate_channel(channel)
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

    def _job_summary(self, event_type: str, project_id: str | None = None, channel: str | None = None) -> dict[str, Any]:
        events = self.repository.list_audit(project_id, limit=500, channel=channel)
        matching = [event for event in events if event["event_type"] == event_type or event["event_type"] == f"{event_type}_failed"]
        latest = matching[0] if matching else None
        return {"status": (latest["details"].get("status") if latest else "idle"), "progress": (latest["details"].get("progress", 0) if latest else 0), "jobId": (latest["details"].get("jobId") if latest else None), "requestId": (latest["request_id"] if latest else None), "updatedAt": (latest["created_at"] if latest else None)}

    @staticmethod
    def _public_release(value: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value[key] for key in ("id", "project_id", "version", "channel", "repository", "tag", "revision", "release_id", "published_at", "digest", "manifest_key", "status") if key in value}

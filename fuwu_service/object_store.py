"""Object storage drivers: in-memory for tests, S3-compatible with SigV4."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import ReleaseError
from .validation import UTC, now_iso, safe_digest


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

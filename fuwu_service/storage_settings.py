"""Managed object-storage settings and root-only credential file handling.

Non-secret destination values live in the administration store. AWS
credentials are kept only in a root-owned private file and are never included
in an API payload.
"""

from __future__ import annotations

import os
import re
import secrets
import stat
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .core import AwsCredentials, ReleaseError, S3ObjectStore


STORAGE_FIELDS = ("bucket", "region", "endpoint", "prefix")
_BUCKET_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")


@dataclass(frozen=True)
class ManagedStorage:
    settings: dict[str, str]
    credentials: AwsCredentials


def managed_credentials_path(env: Mapping[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("FUWU_MANAGED_AWS_CREDENTIALS_FILE", "/data/private/aws_credentials"))


def _fixed_error() -> ReleaseError:
    return ReleaseError("invalid_storage_settings", "storage settings are invalid", 422)


def _safe_endpoint(value: Any, *, region: str, allowed_endpoints: str | None = None) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise _fixed_error()
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        if parsed.query or parsed.fragment or parsed.port not in (None, 443):
            raise ValueError
        if any(ord(char) < 0x20 for char in value):
            raise ValueError
        # The AWS endpoint is generated from the selected region. Custom
        # S3-compatible endpoints must be pre-authorized by the operator.
        host = parsed.hostname.lower().rstrip(".")
        path = parsed.path.rstrip("/")
        canonical = urllib.parse.urlunsplit(("https", parsed.netloc.lower(), path, "", ""))
        aws_hosts = {f"s3.{region}.amazonaws.com", f"s3.{region}.amazonaws.com.cn"}
        if region == "us-east-1":
            aws_hosts.add("s3.amazonaws.com")
        if host in aws_hosts:
            if parsed.port not in (None, 443) or path:
                raise ValueError
            return f"https://{host}"
        allowed = {entry.strip().rstrip("/") for entry in (allowed_endpoints or "").split(",") if entry.strip()}
        # Compare full origins and path prefixes, not just hostnames. Exact
        # allowlisting keeps the application from becoming an SSRF proxy.
        if canonical not in allowed:
            raise ValueError
        if any(parsed.path.split("/")[i] == ".." for i in range(len(parsed.path.split("/")))):
            raise ValueError
        return canonical
    except (ValueError, UnicodeError):
        raise _fixed_error() from None


def validate_storage_settings(body: Mapping[str, Any], *, allowed_endpoints: str | None = None) -> dict[str, str]:
    if not isinstance(body, Mapping):
        raise _fixed_error()
    permitted = set(STORAGE_FIELDS) | {"accessKeyId", "secretAccessKey", "sessionToken"}
    if set(body) - permitted:
        raise _fixed_error()
    bucket = body.get("bucket")
    region = body.get("region")
    prefix = body.get("prefix")
    if (not isinstance(bucket, str) or not 3 <= len(bucket) <= 63 or bucket.lower() != bucket
            or ".." in bucket or re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", bucket)
            or any(not _BUCKET_LABEL_RE.fullmatch(label) for label in bucket.split("."))):
        raise _fixed_error()
    if not isinstance(region, str) or not _REGION_RE.fullmatch(region):
        raise _fixed_error()
    if not isinstance(prefix, str) or not _PREFIX_RE.fullmatch(prefix) or any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise _fixed_error()
    endpoint_value = body.get("endpoint")
    if endpoint_value in (None, ""):
        endpoint_value = f"https://s3.{region}.amazonaws.com"
    endpoint = _safe_endpoint(endpoint_value, region=region, allowed_endpoints=allowed_endpoints)
    return {"bucket": bucket, "region": region, "endpoint": endpoint, "prefix": prefix}


def _credential_values(body: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    access = body.get("accessKeyId")
    secret = body.get("secretAccessKey")
    token = body.get("sessionToken")
    if access in (None, "") and secret in (None, "") and token in (None, ""):
        return None, None, None
    if not isinstance(access, str) or not isinstance(secret, str) or not access or not secret:
        raise _fixed_error()
    if len(access) > 256 or len(secret) > 4096 or (token is not None and (not isinstance(token, str) or len(token) > 8192)):
        raise _fixed_error()
    values = (access, secret, token or "")
    if any(any(ord(char) < 0x20 or ord(char) == 0x7f for char in value) for value in values):
        raise _fixed_error()
    return access, secret, token or None


def _ensure_private_directory(directory: Path) -> None:
    if os.geteuid() != 0:
        raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
    if not directory.is_absolute():
        raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
    current = Path(directory.anchor)
    parts = directory.parts[1:]
    if not parts:
        raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
    for index, part in enumerate(parts):
        current = current / part
        created = False
        try:
            current.mkdir(mode=0o700)
            created = True
        except FileExistsError:
            pass
        try:
            info = current.lstat()
        except OSError:
            raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503) from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
        if index == len(parts) - 1:
            if info.st_uid != 0:
                raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
            if created:
                os.chmod(current, 0o700)
                info = current.lstat()
            if stat.S_IMODE(info.st_mode) != 0o700:
                raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)


def _validate_private_directory(directory: Path) -> None:
    if os.geteuid() != 0 or not directory.is_absolute():
        raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)
    current = Path(directory.anchor)
    parts = directory.parts[1:]
    if not parts:
        raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except OSError:
            raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503) from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)
        if index == len(parts) - 1 and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700):
            raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)


def _format_credentials(access: str, secret: str, token: str | None) -> bytes:
    lines = [f"AWS_ACCESS_KEY_ID={access}", f"AWS_SECRET_ACCESS_KEY={secret}"]
    if token:
        lines.append(f"AWS_SESSION_TOKEN={token}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_managed_credentials(path: Path, access: str, secret: str, token: str | None) -> None:
    """Atomically replace a root-only credential file without logging values."""
    access, secret, token = _credential_values({"accessKeyId": access, "secretAccessKey": secret, "sessionToken": token})
    if access is None or secret is None:
        raise _fixed_error()
    directory = path.parent
    _ensure_private_directory(directory)
    temp_name = ".aws-credentials-" + secrets.token_hex(12)
    try:
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if current is not None and (not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode) or current.st_uid != 0 or stat.S_IMODE(current.st_mode) != 0o600):
            raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(directory / temp_name, flags, 0o600)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(_format_credentials(access, secret, token))
                stream.flush()
                os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(directory / temp_name, path)
        dir_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
    except ReleaseError:
        raise
    except OSError:
        try:
            (directory / temp_name).unlink(missing_ok=True)
        except OSError:
            pass
        raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503) from None


def read_managed_credentials(path: Path) -> AwsCredentials:
    try:
        _validate_private_directory(path.parent)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 16384:
                raise ValueError
            values: dict[str, str] = {}
            for line in stream.read(16385).splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value
        access, secret = values.get("AWS_ACCESS_KEY_ID"), values.get("AWS_SECRET_ACCESS_KEY")
        token = values.get("AWS_SESSION_TOKEN")
        if not access or not secret:
            raise ValueError
        return AwsCredentials(access, secret, token)
    except (OSError, UnicodeError, ValueError):
        raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503) from None


def masked_access_key(access_key: str | None) -> str | None:
    if not access_key:
        return None
    return "****" + access_key[-4:]


def _service_defaults(service: Any) -> dict[str, str]:
    store = service.store
    region = getattr(store, "region", os.environ.get("FUWU_S3_REGION", "your-region-1"))
    return {
        "bucket": getattr(store, "bucket", os.environ.get("FUWU_S3_BUCKET", "")),
        "region": region,
        "endpoint": getattr(store, "endpoint", os.environ.get("FUWU_S3_ENDPOINT", f"https://s3.{region}.amazonaws.com")),
        "prefix": service.prefix or os.environ.get("FUWU_S3_PREFIX", "releases"),
    }


def _has_releases_or_pointers(service: Any) -> bool:
    repository = service.repository
    if repository.list_releases():
        return True
    try:
        return repository.conn.execute("SELECT 1 FROM latest LIMIT 1").fetchone() is not None
    except Exception:
        return False


def _has_managed_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)
    _validate_private_directory(path.parent)
    return True


def _remove_managed_credentials(path: Path) -> None:
    _validate_private_directory(path.parent)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503)
        path.unlink()
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except FileNotFoundError:
        return
    except ReleaseError:
        raise
    except OSError:
        raise ReleaseError("credential_storage_unavailable", "secure credential storage is unavailable", 503) from None


def load_managed_storage(admin_store: Any, *, credentials_file: Path | None = None) -> ManagedStorage | None:
    """Load an explicitly configured destination and its complete credential set.

    Once a managed destination exists it is a complete configuration: startup
    will not fall back to environment fields or mix another credential source.
    """
    settings = admin_store.storage_configuration()
    if not settings.get("configured"):
        return None
    if any(not isinstance(settings.get(field), str) or not settings[field] for field in STORAGE_FIELDS):
        raise ReleaseError("invalid_storage_config", "storage configuration is invalid", 500)
    path = credentials_file or managed_credentials_path()
    credentials = read_managed_credentials(path)
    return ManagedStorage({field: str(settings[field]) for field in STORAGE_FIELDS}, credentials)


def build_managed_store(admin_store: Any, *, credentials_file: Path | None = None, env: Mapping[str, str] | None = None) -> tuple[S3ObjectStore, str] | None:
    managed = load_managed_storage(admin_store, credentials_file=credentials_file)
    if managed is None:
        return None
    env = os.environ if env is None else env
    try:
        timeout = float(env.get("FUWU_S3_TIMEOUT", "30"))
        if not 0 < timeout <= 3600:
            raise ValueError
    except (TypeError, ValueError):
        raise ReleaseError("invalid_storage_config", "storage timeout is invalid", 500) from None
    store = S3ObjectStore(managed.settings["bucket"], managed.settings["region"], managed.settings["endpoint"],
                          managed.credentials, public_reads=env.get("FUWU_S3_PUBLIC_READS", "1") != "0", timeout=timeout)
    return store, managed.settings["prefix"]


def storage_settings_payload(
    admin_store: Any,
    service: Any,
    *,
    credentials_file: Path | None = None,
    include_access_key_hint: bool = True,
) -> dict[str, Any]:
    path = credentials_file or managed_credentials_path()
    settings = admin_store.storage_configuration(_service_defaults(service))
    credentials: AwsCredentials | None = getattr(service.store, "credentials", None) if not settings.get("configured") else None
    managed = False
    if settings.get("configured"):
        try:
            if _has_managed_file(path):
                credentials = read_managed_credentials(path)
                managed = True
        except ReleaseError:
            credentials = None
    return {
        **{field: settings.get(field, "") for field in STORAGE_FIELDS},
        "credentialsConfigured": credentials is not None,
        "credentialSource": "managed" if managed else ("bootstrap" if credentials is not None else "unavailable"),
        "accessKeyHint": masked_access_key(credentials.access_key) if include_access_key_hint and credentials is not None else None,
        # These are deliberately boolean status fields.  The credential
        # values themselves never cross the API boundary, while the UI can
        # distinguish an intentionally empty input from an unconfigured
        # deployment.
        "secretAccessKeyConfigured": credentials is not None,
        "sessionTokenConfigured": bool(credentials and credentials.session_token),
    }


def update_storage_settings(admin_store: Any, service: Any, body: Mapping[str, Any], *, credentials_file: Path | None = None,
                            request_secure: bool = True, allowed_endpoints: str | None = None) -> dict[str, Any]:
    """Validate and apply storage settings while never persisting credentials in SQLite."""
    if not request_secure:
        raise ReleaseError("secure_transport_required", "secure connection is required", 400)
    settings = validate_storage_settings(body, allowed_endpoints=allowed_endpoints if allowed_endpoints is not None else os.environ.get("FUWU_S3_ALLOWED_ENDPOINTS", ""))
    access, secret, token = _credential_values(body)
    path = credentials_file or managed_credentials_path()
    current = admin_store.storage_configuration(_service_defaults(service))
    destination = {field: current.get(field, "") for field in STORAGE_FIELDS}
    timeout_env = os.environ.copy()
    try:
        timeout = float(timeout_env.get("FUWU_S3_TIMEOUT", "30"))
        if not 0 < timeout <= 3600:
            raise ValueError
    except (TypeError, ValueError):
        raise ReleaseError("invalid_storage_config", "storage timeout is invalid", 500) from None
    with service._mutation_lock:
        if _has_releases_or_pointers(service) and any(destination[field] != settings[field] for field in STORAGE_FIELDS):
            raise ReleaseError("storage_destination_locked", "storage destination cannot change after publication", 409)
        previous_credentials: AwsCredentials | None = None
        if access is not None:
            if _has_managed_file(path):
                previous_credentials = read_managed_credentials(path)
        if access is not None and secret is not None:
            write_managed_credentials(path, access, secret, token)
        elif not _has_managed_file(path):
            raise ReleaseError("credentials_unavailable", "storage credentials are unavailable", 503)
        credentials = read_managed_credentials(path)
        store = S3ObjectStore(settings["bucket"], settings["region"], settings["endpoint"], credentials,
                              public_reads=timeout_env.get("FUWU_S3_PUBLIC_READS", "1") != "0", timeout=timeout)
        # Only the complete destination is committed. Secrets are written to
        # the protected file before the database transaction and never enter SQL data.
        try:
            admin_store.save_storage_configuration(settings)
        except BaseException:
            if access is not None:
                try:
                    if previous_credentials is None:
                        _remove_managed_credentials(path)
                    else:
                        write_managed_credentials(path, previous_credentials.access_key,
                                                  previous_credentials.secret_key,
                                                  previous_credentials.session_token)
                except BaseException:
                    raise ReleaseError("storage_settings_save_failed", "storage settings could not be saved", 500) from None
            raise
        service.store = store
        service.prefix = settings["prefix"]
    return storage_settings_payload(admin_store, service, credentials_file=path)

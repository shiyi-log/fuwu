"""Global GitHub credentials and identity verification.

GitHub access tokens are deployment secrets.  They are kept outside SQLite,
operation logs, and API responses in a root-only (or service-user-only)
0600 JSON file.  Only the metadata returned by GitHub's ``/user`` endpoint is
stored alongside the token.  The release service can read the token through
``get_token`` when it needs to download a private release, while HTTP handlers
must use ``public``/``public_record`` and never include the token in a response.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import stat
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Protocol

from .core import ReleaseError
UTC = dt.timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUIRED_REPOSITORY = "shiyi-log/heyue"
_TOKEN_MAX_LENGTH = 512
_GITHUB_README_RESPONSE_MAX_BYTES = 2_000_000
_GITHUB_README_MAX_BYTES = 512_000
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_+/=-]{1,128}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _timestamp() -> str:
    return dt.datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def github_credentials_path(env: Mapping[str, str] | None = None) -> Path:
    """Return the deployment path for project GitHub tokens.

    Deployments should set ``FUWU_GITHUB_CREDENTIALS_FILE`` to a mounted
    secrets path.  The container default follows the existing AWS secret
    layout.  No token is read from a project row or a public configuration
    file.
    """

    env = os.environ if env is None else env
    value = env.get("FUWU_GITHUB_CREDENTIALS_FILE")
    if value:
        path = Path(value).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path
    try:
        from .runtime_config import github_credentials_file
        return github_credentials_file()
    except ReleaseError:
        raise
    except Exception:
        raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None


def _validate_token(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > _TOKEN_MAX_LENGTH:
        raise ReleaseError("invalid_github_token", "GitHub authorization is invalid", 422)
    if value != value.strip() or any(ord(char) < 0x21 or ord(char) == 0x7F for char in value):
        raise ReleaseError("invalid_github_token", "GitHub authorization is invalid", 422)
    return value


def _validate_login(value: Any) -> str:
    if not isinstance(value, str) or not _LOGIN_RE.fullmatch(value):
        raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
    return value


def _validate_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
    result = sorted({scope.strip() for scope in value if isinstance(scope, str) and scope.strip()})
    # GitHub can add scopes over time; unknown values are safe metadata and are
    # retained rather than making an otherwise valid authorization unusable.
    if any(len(scope) > 100 or any(ord(char) < 0x21 for char in scope) for scope in result):
        raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
    return result


def github_required_repository(env: Mapping[str, str] | None = None) -> str:
    """Return the repository whose read access is checked on save."""

    env = os.environ if env is None else env
    value = env.get("FUWU_GITHUB_REQUIRED_REPOSITORY", DEFAULT_REQUIRED_REPOSITORY)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value.strip()):
        raise ReleaseError("github_repository_config_invalid", "GitHub repository configuration is invalid", 503)
    return value.strip()


class _Opener(Protocol):
    def open(self, request: urllib.request.Request, timeout: float = ...) -> Any: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a PAT to a URL GitHub did not explicitly verify."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def verify_github_token(token: str, *, opener: _Opener | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Resolve a PAT through GitHub and return safe identity metadata.

    The token is sent only as an HTTPS Authorization header.  GitHub errors
    are mapped to fixed messages, and response bodies are never included in an
    exception or audit record.
    """

    token = _validate_token(token)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ReleaseError("github_unavailable", "GitHub authorization service is unavailable", 502)
    request = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "fuwu-release-storage",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = opener or urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            payload = json.loads(response.read(1_000_000))
            headers = getattr(response, "headers", {})
    except urllib.error.HTTPError as error:
        # 401 and 403 are both intentionally represented by one safe error;
        # exposing GitHub's body could disclose account or policy details.
        if error.code in {401, 403}:
            raise ReleaseError("github_token_invalid", "GitHub authorization was rejected", 422) from None
        raise ReleaseError("github_unavailable", "GitHub authorization service is unavailable", 502) from None
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise ReleaseError("github_unavailable", "GitHub authorization service is unavailable", 502) from None
    if not isinstance(payload, Mapping):
        raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
    login = _validate_login(payload.get("login"))
    node_id = payload.get("node_id")
    if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
        raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
    raw_scopes = ""
    try:
        raw_scopes = headers.get("X-OAuth-Scopes", "")
    except AttributeError:
        pass
    scopes = _validate_scopes(raw_scopes.split(",") if isinstance(raw_scopes, str) else [])
    return {"login": login, "node_id": node_id, "scopes": scopes}


def verify_github_repository_access(token: str, repository: str, *, opener: _Opener | None = None, timeout: float = 10.0) -> dict[str, Any]:
    """Require repository read access and return safe check metadata.

    GitHub exposes repository permissions for an authenticated caller.  A
    caller with ``pull=true`` can read releases, while the same account may
    legitimately have ``push`` or ``admin`` access on the repository.  Those
    account permissions do not describe the token's scope and must not make a
    valid download authorization fail.  Metadata is always read-only for this
    API; the ``readOnly`` marker reports the account's write capability without
    exposing GitHub's response or the token.
    """

    token = _validate_token(token)
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("invalid_repository", "repository is invalid", 422)
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "User-Agent": "fuwu-release-storage",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    opener = opener or urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as error:
        if error.code in {401, 403, 404}:
            raise ReleaseError("github_repository_access_denied", "GitHub account cannot access the project repository", 422) from None
        raise ReleaseError("github_unavailable", "GitHub authorization service is unavailable", 502) from None
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise ReleaseError("github_unavailable", "GitHub authorization service is unavailable", 502) from None
    if not isinstance(payload, Mapping) or not isinstance(payload.get("full_name"), str) or payload["full_name"].casefold() != repository.casefold():
        raise ReleaseError("github_repository_access_denied", "GitHub account cannot access the project repository", 422)
    permissions = payload.get("permissions")
    if not isinstance(permissions, Mapping):
        raise ReleaseError("github_repository_permissions_insufficient", "GitHub token must have repository read access", 422)
    if permissions.get("pull") is not True:
        raise ReleaseError("github_repository_permissions_insufficient", "GitHub token must have repository read access", 422)
    has_write_access = any(permissions.get(name) is True for name in ("push", "admin", "maintain", "triage"))
    return {
        "repository": repository,
        "contents": "read",
        "metadata": "read",
        "readOnly": not has_write_access,
    }


def read_github_repository_readme(
    repository: str,
    *,
    token: str | None = None,
    opener: _Opener | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Read a repository's default README through GitHub Contents API.

    The service keeps the source repository as the single copy of the project
    description.  GitHub's JSON Contents response is decoded server-side and
    only bounded UTF-8 text plus non-sensitive source metadata is returned to
    callers.  The authorization token is never part of the result or an error
    message.
    """

    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("invalid_repository", "repository is invalid", 422)
    repository = repository.strip()
    if token is not None:
        token = _validate_token(token)
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
        raise ReleaseError("github_unavailable", "GitHub project description is unavailable", 502)

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "fuwu-release-storage",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + "/readme",
        headers=headers,
    )
    opener = opener or urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=float(timeout)) as response:
            headers = getattr(response, "headers", {})
            try:
                declared_length = headers.get("Content-Length")
                if declared_length is not None and int(declared_length) > _GITHUB_README_RESPONSE_MAX_BYTES:
                    raise ReleaseError("github_documentation_too_large", "GitHub project description is too large", 502)
            except (AttributeError, TypeError, ValueError):
                pass
            encoded_response = response.read(_GITHUB_README_RESPONSE_MAX_BYTES + 1)
            if len(encoded_response) > _GITHUB_README_RESPONSE_MAX_BYTES:
                raise ReleaseError("github_documentation_too_large", "GitHub project description is too large", 502)
            payload = json.loads(encoded_response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise ReleaseError("github_documentation_not_found", "GitHub project description was not found", 404) from None
        if error.code in {401, 403}:
            raise ReleaseError("github_documentation_unavailable", "GitHub project description is unavailable", 502) from None
        raise ReleaseError("github_unavailable", "GitHub project description is unavailable", 502) from None
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise ReleaseError("github_unavailable", "GitHub project description is unavailable", 502) from None

    if not isinstance(payload, Mapping):
        raise ReleaseError("github_documentation_invalid", "GitHub project description is invalid", 502)
    declared_size = payload.get("size")
    if isinstance(declared_size, int) and not isinstance(declared_size, bool) and declared_size > _GITHUB_README_MAX_BYTES:
        raise ReleaseError("github_documentation_too_large", "GitHub project description is too large", 502)
    encoded = payload.get("content")
    if not isinstance(encoded, str) or payload.get("encoding") != "base64":
        raise ReleaseError("github_documentation_invalid", "GitHub project description is invalid", 502)
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
    except (ValueError, TypeError):
        raise ReleaseError("github_documentation_invalid", "GitHub project description is invalid", 502) from None
    if len(raw) > _GITHUB_README_MAX_BYTES:
        raise ReleaseError("github_documentation_too_large", "GitHub project description is too large", 502)
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ReleaseError("github_documentation_invalid", "GitHub project description is invalid", 502) from None

    path = payload.get("path", "README.md")
    if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}", path) or ".." in path.split("/"):
        path = "README.md"
    sha = payload.get("sha")
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        sha = None
    source_url = payload.get("html_url")
    try:
        parsed_source = urllib.parse.urlsplit(source_url) if isinstance(source_url, str) else None
    except ValueError:
        parsed_source = None
    source_parts = parsed_source.path.split("/") if parsed_source else []
    if not (
        parsed_source
        and parsed_source.scheme == "https"
        and parsed_source.netloc.lower() == "github.com"
        and not parsed_source.username
        and not parsed_source.password
        and not parsed_source.query
        and not parsed_source.fragment
        and len(source_parts) >= 4
        and source_parts[1].casefold() == repository.split("/", 1)[0].casefold()
        and source_parts[2].casefold() == repository.split("/", 1)[1].casefold()
        and source_parts[3] == "blob"
    ):
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in path.split("/"))
        source_url = "https://github.com/" + repository + "/blob/HEAD/" + encoded_path
    return {
        "repository": repository,
        "path": path,
        "sha": sha,
        "content": content,
        "sourceUrl": source_url,
    }


class GitHubAuthorizationStore:
    """Atomic, global GitHub token store.

    The file format is deliberately private and versioned.  A missing file is
    treated as an empty store, while malformed or weakly protected files fail
    closed so a deployment cannot silently lose its authorization boundary.
    """

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else github_credentials_path()
        self._lock = threading.RLock()

    def _ensure_parent(self) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None

    def _check_file(self, *, create: bool = False) -> None:
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            if create:
                return
            return
        except OSError:
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)

    def _read(self) -> dict[str, Any]:
        self._check_file()
        if not self.path.exists():
            return {"version": 1, "account": None}
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None
        # Accept only the global v1 shape.  In particular, do not silently
        # treat a former project-scoped ``projects`` file as an empty global
        # store while leaving its secret on disk.
        if not isinstance(data, Mapping) or set(data) != {"version", "account"} or data.get("version") != 1:
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
        return {"version": 1, "account": data.get("account")}

    def _write(self, data: Mapping[str, Any]) -> None:
        self._ensure_parent()
        self._check_file(create=True)
        payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        temp_path: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=".github-credentials-", dir=str(self.path.parent), text=True)
            temp_path = Path(temp_name)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            if temp_path:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None

    @staticmethod
    def _record(value: Any) -> dict[str, Any]:
        """Validate a record loaded from the private file before using it."""

        if not isinstance(value, Mapping):
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
        try:
            token = _validate_token(value.get("token"))
            login = _validate_login(value.get("login"))
            node_id = value.get("node_id")
            if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
                raise ValueError
            scopes = _validate_scopes(value.get("scopes", []))
        except (ReleaseError, ValueError, TypeError):
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
        authorized_at = value.get("authorizedAt")
        if not isinstance(authorized_at, str) or len(authorized_at) > 64:
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
        result = {"token": token, "login": login, "node_id": node_id, "scopes": scopes, "authorizedAt": authorized_at}
        if "permissionCheck" in value:
            check = value.get("permissionCheck")
            if not isinstance(check, Mapping) or set(check) != {"repository", "contents", "metadata", "readOnly"}:
                raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
            repository = check.get("repository")
            if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
                raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
            if check.get("contents") != "read" or check.get("metadata") != "read" or not isinstance(check.get("readOnly"), bool):
                raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
            result["permissionCheck"] = {
                "repository": repository,
                "contents": "read",
                "metadata": "read",
                "readOnly": check["readOnly"],
            }
        return result

    def get_token(self) -> str | None:
        """Return a token only for an internal, already-authorized download."""

        with self._lock:
            record = self._read()["account"]
            if not isinstance(record, Mapping):
                return None
            return self._record(record)["token"]

    def get_public(self) -> dict[str, Any]:
        with self._lock:
            record = self._read()["account"]
            if not isinstance(record, Mapping):
                return {"authorized": False, "login": None, "node_id": None, "scopes": [], "authorizedAt": None}
            record = self._record(record)
            public = {
                "authorized": True,
                "login": record["login"],
                "node_id": record["node_id"],
                "scopes": list(record["scopes"]),
                "authorizedAt": record["authorizedAt"],
            }
            if "permissionCheck" in record:
                public["permissionCheck"] = dict(record["permissionCheck"])
            return public

    def authorize(self, token: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        token = _validate_token(token)
        login = _validate_login(identity.get("login"))
        node_id = identity.get("node_id")
        if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
            raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
        scopes = _validate_scopes(identity.get("scopes", []))
        record = {"token": token, "login": login, "node_id": node_id, "scopes": scopes, "authorizedAt": _timestamp()}
        if "permissionCheck" in identity:
            record["permissionCheck"] = self._record({
                "token": token, "login": login, "node_id": node_id, "scopes": scopes,
                "authorizedAt": record["authorizedAt"], "permissionCheck": identity.get("permissionCheck"),
            })["permissionCheck"]
        with self._lock:
            data = self._read()
            self._write({"version": 1, "account": record})
        return self.get_public()

    def revoke(self) -> dict[str, Any]:
        with self._lock:
            self._write({"version": 1, "account": None})
        return self.get_public()


def get_github_authorization_store(env: Mapping[str, str] | None = None) -> GitHubAuthorizationStore:
    """Construct a store from deployment configuration.

    The API layer may cache this object for a process lifetime.  Keeping this
    factory side-effect free also makes tests and rotated secret mounts safe.
    """

    if env is not None and "FUWU_GITHUB_CREDENTIALS_FILE" in env:
        path = github_credentials_path(env)
    else:
        from .runtime_config import github_credentials_file
        path = github_credentials_file()
    return GitHubAuthorizationStore(path)

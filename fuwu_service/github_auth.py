"""Project-scoped GitHub credentials and identity verification.

GitHub access tokens are deployment secrets.  They are kept outside SQLite,
operation logs, and API responses in a root-only (or service-user-only)
0600 JSON file.  Only the metadata returned by GitHub's ``/user`` endpoint is
stored alongside the token.  The release service can read the token through
``get_token`` when it needs to download a private release, while HTTP handlers
must use ``public``/``public_record`` and never include the token in a response.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Protocol

from .core import ReleaseError
from .projects import validate_project_id


UTC = dt.timezone.utc
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TOKEN_MAX_LENGTH = 512
_LOGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_NODE_ID_RE = re.compile(r"[A-Za-z0-9_+/=-]{1,128}")


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


def verify_github_repository_access(token: str, repository: str, *, opener: _Opener | None = None, timeout: float = 10.0) -> None:
    """Require the verified token to read the project's bound repository."""

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


class GitHubAuthorizationStore:
    """Atomic, project-scoped GitHub token store.

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
            return {"version": 1, "projects": {}}
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503) from None
        if not isinstance(data, Mapping) or data.get("version") != 1 or not isinstance(data.get("projects"), Mapping):
            raise ReleaseError("github_credentials_unavailable", "GitHub credential storage is unavailable", 503)
        return {"version": 1, "projects": dict(data["projects"])}

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
    def _project(project_id: Any) -> str:
        try:
            return validate_project_id(project_id)
        except Exception:
            raise ReleaseError("invalid_project", "project identifier is invalid", 422) from None

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
        return {"token": token, "login": login, "node_id": node_id, "scopes": scopes, "authorizedAt": authorized_at}

    def get_token(self, project_id: str) -> str | None:
        """Return a token only for an internal, already-authorized download."""

        project_id = self._project(project_id)
        with self._lock:
            record = self._read()["projects"].get(project_id)
            if not isinstance(record, Mapping):
                return None
            return self._record(record)["token"]

    def get_public(self, project_id: str) -> dict[str, Any]:
        project_id = self._project(project_id)
        with self._lock:
            record = self._read()["projects"].get(project_id)
            if not isinstance(record, Mapping):
                return {"authorized": False, "login": None, "node_id": None, "scopes": [], "authorizedAt": None}
            record = self._record(record)
            return {
                "authorized": True,
                "login": record["login"],
                "node_id": record["node_id"],
                "scopes": list(record["scopes"]),
                "authorizedAt": record["authorizedAt"],
            }

    def authorize(self, project_id: str, token: str, identity: Mapping[str, Any]) -> dict[str, Any]:
        project_id = self._project(project_id)
        token = _validate_token(token)
        login = _validate_login(identity.get("login"))
        node_id = identity.get("node_id")
        if not isinstance(node_id, str) or not _NODE_ID_RE.fullmatch(node_id):
            raise ReleaseError("github_identity_invalid", "GitHub identity is invalid", 502)
        scopes = _validate_scopes(identity.get("scopes", []))
        record = {"token": token, "login": login, "node_id": node_id, "scopes": scopes, "authorizedAt": _timestamp()}
        with self._lock:
            data = self._read()
            projects = dict(data["projects"])
            projects[project_id] = record
            self._write({"version": 1, "projects": projects})
        return self.get_public(project_id)

    def revoke(self, project_id: str) -> dict[str, Any]:
        project_id = self._project(project_id)
        with self._lock:
            data = self._read()
            projects = dict(data["projects"])
            projects.pop(project_id, None)
            self._write({"version": 1, "projects": projects})
        return self.get_public(project_id)


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

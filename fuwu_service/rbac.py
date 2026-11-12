"""Bearer token, account and signed UI session authorization."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Iterable, Mapping

from .errors import ReleaseError


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

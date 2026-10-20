"""Persistent accounts, password hashes and revocable opaque management sessions.

Accounts use an independent SQLite connection; no account operation participates
in a release publication transaction. Only session SHA256 digests are stored.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from django.contrib.auth.hashers import PBKDF2PasswordHasher

from .core import ReleaseError
from .projects import ProjectInputError, validate_project_id

KNOWN_PERMISSIONS = frozenset({
    "release.storage.read", "release.storage.publish", "release.storage.rollback",
    "users.manage", "projects.manage", "logs.read", "settings.manage",
})
ROLE_CATALOG = {
    "superadmin": {"name": "超级管理员", "permissions": sorted(KNOWN_PERMISSIONS)},
    "release-manager": {"name": "发行管理员", "permissions": ["release.storage.read", "release.storage.publish"]},
    "viewer": {"name": "只读用户", "permissions": ["release.storage.read"]},
}
SESSION_PREFIX = "fuwu-account-v1."
SESSION_LIFETIME_SECONDS = 3600
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
LOGIN_WINDOW_SECONDS = 300
LOGIN_LOCK_SECONDS = 300
LOGIN_MAX_FAILURES = 5
CLIENT_MAX_FAILURES = 20
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_LOCKS: dict[str, Any] = {}
_LOCKS_GUARD = threading.Lock()
_DUMMY_HASH: str | None = None
_DUMMY_HASH_GUARD = threading.Lock()
_STORES: dict[str, Any] = {}
_STORES_GUARD = threading.Lock()


def _unauthorized() -> ReleaseError:
    return ReleaseError("unauthorized", "authentication required", 401)


def _invalid() -> ReleaseError:
    return ReleaseError("invalid_account", "account data is invalid", 400)


def _timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dummy_hash() -> str:
    global _DUMMY_HASH
    with _DUMMY_HASH_GUARD:
        if _DUMMY_HASH is None:
            hasher = PBKDF2PasswordHasher()
            _DUMMY_HASH = hasher.encode(secrets.token_urlsafe(32), hasher.salt())
    return _DUMMY_HASH


def role_catalog() -> list[dict[str, Any]]:
    return [{"id": key, "name": value["name"], "permissions": list(value["permissions"])} for key, value in ROLE_CATALOG.items()]


def get_account_store(database: str | Path | None = None) -> "AccountStore":
    """Reuse one independently locked connection per configured database."""
    key = str(database if database is not None else os.environ.get("FUWU_DATABASE", "fuwu.sqlite3"))
    with _STORES_GUARD:
        if key not in _STORES:
            _STORES[key] = AccountStore(key)
        return _STORES[key]


def account_store() -> "AccountStore":
    """HTTP-adapter entry point using the current FUWU_DATABASE setting."""
    return get_account_store()


class AccountStore:
    def __init__(self, database: str | Path | None = None):
        self.database = str(database if database is not None else os.environ.get("FUWU_DATABASE", "fuwu.sqlite3"))
        lock_key = str(Path(self.database).resolve()) if self.database != ":memory:" else uuid.uuid4().hex
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(lock_key, threading.RLock())
        self.conn = sqlite3.connect(self.database, check_same_thread=False, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> "AccountStore":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def _migrate(self) -> None:
        with self._transaction():
            self.conn.execute("CREATE TABLE IF NOT EXISTS account_schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)),
                roles_json TEXT NOT NULL, permissions_json TEXT NOT NULL, project_ids_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER)""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS account_sessions_user ON account_sessions(user_id)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_login_attempts (
                scope_hash TEXT PRIMARY KEY, window_start INTEGER NOT NULL,
                failures INTEGER NOT NULL, blocked_until INTEGER NOT NULL)""")
            self.conn.execute("INSERT OR IGNORE INTO account_schema_migrations VALUES (1,?)", (_timestamp(),))

    @staticmethod
    def _id(user_id: str) -> str:
        return user_id[5:] if isinstance(user_id, str) and user_id.startswith("user:") else user_id

    def _row(self, user_id: str) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM account_users WHERE id=?", (self._id(user_id),)).fetchone()
        if row is None:
            raise ReleaseError("not_found", "user was not found", 404)
        return row

    @staticmethod
    def _public(row: Mapping[str, Any]) -> dict[str, Any]:
        roles = json.loads(row["roles_json"])
        extra_permissions = json.loads(row["permissions_json"])
        permissions = set(extra_permissions)
        for role in roles:
            permissions.update(ROLE_CATALOG[role]["permissions"])
        return {
            "id": row["id"], "username": row["username"], "displayName": row["display_name"],
            "active": bool(row["active"]), "roles": roles, "permissions": sorted(permissions),
            "directPermissions": extra_permissions, "project_ids": json.loads(row["project_ids_json"]),
            "homePath": "/projects", "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _password(password: Any) -> str:
        if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH or not password.strip():
            raise ReleaseError("invalid_password", "password must contain 12 to 256 characters", 400)
        hasher = PBKDF2PasswordHasher()
        return hasher.encode(password, hasher.salt())

    @staticmethod
    def _fields(payload: Mapping[str, Any], current: Mapping[str, Any] | None = None, *, creation: bool = False) -> dict[str, Any]:
        permitted = {"username", "displayName", "active", "roles", "permissions", "project_ids"} | ({"password"} if creation else set())
        if not isinstance(payload, Mapping) or set(payload) - permitted:
            raise _invalid()
        current = current or {}
        username = payload.get("username", current.get("username"))
        if not isinstance(username, str) or not USERNAME_RE.fullmatch(username.strip().casefold()):
            raise _invalid()
        username = username.strip().casefold()
        display_name = payload.get("displayName", current.get("displayName", username))
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 100 or any(ord(ch) < 32 for ch in display_name):
            raise _invalid()
        active = payload.get("active", current.get("active", True))
        if type(active) is not bool:
            raise _invalid()
        roles = payload.get("roles", current.get("roles", ["viewer"]))
        direct = payload.get("permissions", current.get("directPermissions", []))
        if not isinstance(roles, list) or any(not isinstance(role, str) or role not in ROLE_CATALOG for role in roles):
            raise _invalid()
        if not isinstance(direct, list) or any(not isinstance(permission, str) or permission not in KNOWN_PERMISSIONS for permission in direct):
            raise _invalid()
        projects = payload.get("project_ids", current.get("project_ids", []))
        if projects is None:
            if "superadmin" not in roles:
                raise _invalid()
        elif not isinstance(projects, list):
            raise _invalid()
        else:
            try:
                projects = [validate_project_id(project) for project in projects]
            except ProjectInputError:
                raise _invalid() from None
        return {"username": username, "displayName": display_name.strip(), "active": active,
                "roles": sorted(set(roles)), "directPermissions": sorted(set(direct)),
                "project_ids": None if projects is None else sorted(set(projects))}

    def _manager(self, actor_id: str) -> dict[str, Any]:
        try:
            actor = self._public(self._row(actor_id))
        except ReleaseError:
            raise ReleaseError("forbidden", "permission denied", 403) from None
        if not actor["active"] or "users.manage" not in actor["permissions"]:
            raise ReleaseError("forbidden", "permission denied", 403)
        return actor

    @staticmethod
    def _effective(fields: Mapping[str, Any]) -> set[str]:
        permissions = set(fields["directPermissions"])
        for role in fields["roles"]:
            permissions.update(ROLE_CATALOG[role]["permissions"])
        return permissions

    def _may_manage(self, actor: Mapping[str, Any], desired: Mapping[str, Any], current: Mapping[str, Any] | None = None) -> None:
        if "superadmin" in actor["roles"]:
            return
        if "superadmin" in desired["roles"] or (current and "superadmin" in current["roles"]):
            raise ReleaseError("forbidden", "permission denied", 403)
        if not self._effective(desired) <= set(actor["permissions"]):
            raise ReleaseError("forbidden", "permission denied", 403)
        actor_projects = actor["project_ids"]
        if desired["project_ids"] is None or not set(desired["project_ids"]) <= set(actor_projects or []):
            raise ReleaseError("forbidden", "permission denied", 403)
        if current and (current["project_ids"] is None or not set(current["project_ids"]) <= set(actor_projects or [])):
            raise ReleaseError("forbidden", "permission denied", 403)

    def _retain_superadmin(self, current: Mapping[str, Any], desired: Mapping[str, Any] | None) -> None:
        if not current["active"] or "superadmin" not in current["roles"]:
            return
        if desired and desired["active"] and "superadmin" in desired["roles"]:
            return
        others = self.conn.execute("SELECT * FROM account_users WHERE active=1 AND id<>?", (current["id"],)).fetchall()
        if not any("superadmin" in json.loads(row["roles_json"]) for row in others):
            raise ReleaseError("last_superadmin", "last active superadmin must be retained", 409)

    def _insert(self, fields: Mapping[str, Any], password_hash: str) -> dict[str, Any]:
        user_id, timestamp = uuid.uuid4().hex, _timestamp()
        try:
            self.conn.execute("INSERT INTO account_users VALUES (?,?,?,?,?,?,?,?,?,?)", (
                user_id, fields["username"], fields["displayName"], password_hash, int(fields["active"]),
                json.dumps(fields["roles"]), json.dumps(fields["directPermissions"]), json.dumps(fields["project_ids"]), timestamp, timestamp))
        except sqlite3.IntegrityError:
            raise ReleaseError("username_exists", "username already exists", 409) from None
        return self._public(self._row(user_id))

    def bootstrap_superadmin(self, username: str, password: str, display_name: str = "") -> dict[str, Any]:
        fields = self._fields({"username": username, "displayName": display_name or username,
                               "roles": ["superadmin"], "permissions": [], "project_ids": None})
        password_hash = self._password(password)
        with self._transaction():
            if self.conn.execute("SELECT 1 FROM account_users LIMIT 1").fetchone():
                raise ReleaseError("bootstrap_not_allowed", "initial administrator already exists", 409)
            return self._insert(fields, password_hash)

    def list_users(self, actor_id: str) -> list[dict[str, Any]]:
        with self._lock:
            self._manager(actor_id)
            return [self._public(row) for row in self.conn.execute("SELECT * FROM account_users ORDER BY username").fetchall()]

    def get_user(self, actor_id: str, user_id: str) -> dict[str, Any]:
        with self._lock:
            self._manager(actor_id)
            return self._public(self._row(user_id))

    def create_user(self, actor_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        fields = self._fields(payload, creation=True)
        password_hash = self._password(payload.get("password"))
        with self._transaction():
            actor = self._manager(actor_id)
            self._may_manage(actor, fields)
            return self._insert(fields, password_hash)

    def _revoke(self, user_id: str) -> None:
        self.conn.execute("UPDATE account_sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (int(time.time()), self._id(user_id)))

    def update_user(self, actor_id: str, user_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self._transaction():
            actor = self._manager(actor_id)
            current = self._public(self._row(user_id))
            fields = self._fields(payload, current)
            self._may_manage(actor, fields, current)
            self._retain_superadmin(current, fields)
            if actor["id"] == current["id"] and not fields["active"]:
                raise ReleaseError("self_deactivation", "current user cannot be disabled or deleted", 409)
            try:
                self.conn.execute("""UPDATE account_users SET username=?,display_name=?,active=?,roles_json=?,
                    permissions_json=?,project_ids_json=?,updated_at=? WHERE id=?""", (
                    fields["username"], fields["displayName"], int(fields["active"]), json.dumps(fields["roles"]),
                    json.dumps(fields["directPermissions"]), json.dumps(fields["project_ids"]), _timestamp(), current["id"]))
            except sqlite3.IntegrityError:
                raise ReleaseError("username_exists", "username already exists", 409) from None
            if any(fields[key] != current[key] for key in ("username", "active", "roles", "directPermissions", "project_ids")):
                self._revoke(current["id"])
            return self._public(self._row(current["id"]))

    def delete_user(self, actor_id: str, user_id: str) -> None:
        with self._transaction():
            actor = self._manager(actor_id)
            current = self._public(self._row(user_id))
            self._may_manage(actor, current, current)
            self._retain_superadmin(current, None)
            if actor["id"] == current["id"]:
                raise ReleaseError("self_deactivation", "current user cannot be disabled or deleted", 409)
            self.conn.execute("DELETE FROM account_users WHERE id=?", (current["id"],))

    def reset_password(self, actor_id: str, user_id: str, password: str) -> None:
        password_hash = self._password(password)
        with self._transaction():
            actor = self._manager(actor_id)
            current = self._public(self._row(user_id))
            self._may_manage(actor, current, current)
            self.conn.execute("UPDATE account_users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, _timestamp(), current["id"]))
            self._revoke(current["id"])

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        password_hash = self._password(new_password)
        with self._transaction():
            row = self._row(user_id)
            if not row["active"] or not isinstance(current_password, str) or len(current_password) > PASSWORD_MAX_LENGTH or not PBKDF2PasswordHasher().verify(current_password, row["password_hash"]):
                raise _unauthorized()
            self.conn.execute("UPDATE account_users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, _timestamp(), row["id"]))
            self._revoke(row["id"])

    @staticmethod
    def _login_scopes(username: str, client_id: str) -> tuple[str, str]:
        client = str(client_id)[:256]
        account_scope = hashlib.sha256(("account:" + username).encode()).hexdigest()
        client_scope = hashlib.sha256(("client:" + client).encode()).hexdigest()
        return account_scope, client_scope

    def _blocked(self, scopes: tuple[str, str], now: int) -> bool:
        return any((row is not None and row["blocked_until"] > now) for row in
                   (self.conn.execute("SELECT blocked_until FROM account_login_attempts WHERE scope_hash=?", (scope,)).fetchone() for scope in scopes))

    def _failed(self, scopes: tuple[str, str], now: int) -> None:
        for scope, limit in zip(scopes, (LOGIN_MAX_FAILURES, CLIENT_MAX_FAILURES)):
            row = self.conn.execute("SELECT * FROM account_login_attempts WHERE scope_hash=?", (scope,)).fetchone()
            window_start = row["window_start"] if row and now - row["window_start"] < LOGIN_WINDOW_SECONDS else now
            failures = row["failures"] + 1 if row and window_start == row["window_start"] else 1
            blocked_until = now + LOGIN_LOCK_SECONDS if failures >= limit else 0
            self.conn.execute("INSERT OR REPLACE INTO account_login_attempts VALUES (?,?,?,?)", (scope, window_start, failures, blocked_until))

    def login(self, username: str, password: str, *, client_id: str = "local") -> dict[str, Any]:
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > PASSWORD_MAX_LENGTH:
            raise _unauthorized()
        username = username.strip().casefold()
        now, scopes = int(time.time()), self._login_scopes(username, client_id)
        with self._lock:
            blocked = self._blocked(scopes, now)
            row = self.conn.execute("SELECT * FROM account_users WHERE username=?", (username,)).fetchone()
        if blocked:
            raise _unauthorized()
        encoded = row["password_hash"] if row else _dummy_hash()
        valid = PBKDF2PasswordHasher().verify(password, encoded)
        with self._transaction():
            # Recheck after the expensive hash: a concurrent disable, reset, or
            # login throttle must take effect before a session can be issued.
            current = self.conn.execute("SELECT * FROM account_users WHERE username=?", (username,)).fetchone()
            if self._blocked(scopes, now) or not valid or not current or not current["active"] or current["password_hash"] != encoded:
                self._failed(scopes, now)
                failed = True
            else:
                self.conn.execute("DELETE FROM account_login_attempts WHERE scope_hash=?", (scopes[0],))
                token = SESSION_PREFIX + secrets.token_urlsafe(32)
                expires_at = now + SESSION_LIFETIME_SECONDS
                self.conn.execute("INSERT INTO account_sessions VALUES (?,?,?,?,NULL)", (hashlib.sha256(token.encode()).hexdigest(), current["id"], now, expires_at))
                result = {"accessToken": token, "expiresAt": expires_at, "user": self._public(current)}
                failed = False
        if failed:
            raise _unauthorized()
        return result

    def authenticate(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or not token.startswith(SESSION_PREFIX) or len(token) > 128:
            raise _unauthorized()
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            row = self.conn.execute("""SELECT u.* FROM account_sessions s JOIN account_users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.active=1""", (digest, int(time.time()))).fetchone()
            if row is None:
                raise _unauthorized()
            return self._public(row)

    def logout(self, token: str) -> None:
        if not isinstance(token, str) or len(token) > 128:
            return
        with self._transaction():
            self.conn.execute("UPDATE account_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL", (int(time.time()), hashlib.sha256(token.encode()).hexdigest()))

    def require_project(self, user_or_token: Mapping[str, Any] | str, project_id: str) -> None:
        try:
            project_id = validate_project_id(project_id)
        except ProjectInputError:
            raise ReleaseError("forbidden", "permission denied", 403) from None
        if isinstance(user_or_token, Mapping):
            with self._lock:
                try:
                    user = self._public(self._row(str(user_or_token.get("id", ""))))
                except ReleaseError:
                    raise _unauthorized() from None
                if not user["active"]:
                    raise _unauthorized()
        else:
            user = self.authenticate(user_or_token)
        projects = user["project_ids"]
        if projects is None and "superadmin" in user["roles"]:
            return
        if isinstance(projects, list) and project_id in projects:
            return
        raise ReleaseError("forbidden", "permission denied", 403)

    @staticmethod
    def role_catalog() -> list[dict[str, Any]]:
        return role_catalog()


def main(argv: list[str] | None = None) -> int:
    class SafeArgumentParser(argparse.ArgumentParser):
        def error(self, _message: str) -> None:
            # argparse's default error may echo an unsupported password value.
            super().error("unsupported command or arguments")

    parser = SafeArgumentParser(description="fuwu 账号初始化工具（不接收明文密码参数）")
    parser.add_argument("command", choices=["bootstrap", "bootstrap-config"])
    parser.add_argument("--database", default=None, help="账号与发行共用的 SQLite 文件")
    parser.add_argument("--config", default=None, help="项目根目录配置文件（bootstrap-config 使用）")
    args = parser.parse_args(argv)
    try:
        if args.command == "bootstrap-config":
            from .runtime_config import bootstrap_from_config
            bootstrap_from_config(args.database, config=args.config)
        else:
            username = input("首个超级管理员用户名：").strip()
            display_name = input("显示名称（留空使用用户名）：").strip()
            password = getpass.getpass("管理员密码（12 至 256 字符）：")
            confirmation = getpass.getpass("再次输入密码：")
            if not hmac.compare_digest(password.encode(), confirmation.encode()):
                print("初始化失败：两次密码不一致")
                return 1
            with AccountStore(args.database) as store:
                store.bootstrap_superadmin(username, password, display_name)
        print("首个超级管理员已创建；请通过账号密码登录。")
        return 0
    except (ReleaseError, EOFError, KeyboardInterrupt) as error:
        print("初始化失败：" + (error.message if isinstance(error, ReleaseError) else "交互已取消"))
        return 1
    except (sqlite3.Error, OSError):
        print("初始化失败：账号存储不可用")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

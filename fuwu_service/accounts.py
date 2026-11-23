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
import sys
import threading
import time
import uuid
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping

from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.conf import settings

from .errors import ReleaseError
from .projects import ProjectInputError, validate_project_id

KNOWN_PERMISSIONS = frozenset({
    "release.storage.read", "release.storage.publish", "release.storage.rollback",
    "users.manage", "projects.manage", "logs.read", "settings.manage", "fuwu.update.manage",
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
RESET_CODE_LIFETIME_SECONDS = 600
RESET_CODE_COOLDOWN_SECONDS = 60
RESET_CODE_MAX_PER_HOUR = 3
RESET_CODE_MAX_ATTEMPTS = 5
RESET_LIMIT_ROW_CAPACITY = 10000
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
    def __init__(self, database: str | Path | None = None, *, existing_only: bool = False):
        self.database = str(database if database is not None else os.environ.get("FUWU_DATABASE", "fuwu.sqlite3"))
        lock_key = str(Path(self.database).resolve()) if self.database != ":memory:" else uuid.uuid4().hex
        with _LOCKS_GUARD:
            self._lock = _LOCKS.setdefault(lock_key, threading.RLock())
        if existing_only:
            # Recovery must never create a database at a mistyped path, or
            # initialize account tables in an unrelated SQLite database.
            uri = Path(self.database).resolve().as_uri() + "?mode=rw"
            self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30, isolation_level=None)
            if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_users'").fetchone():
                self.conn.close()
                raise ReleaseError("account_database_required", "an initialized account database is required", 400)
        else:
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
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_password_reset_codes (
                email_hash TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                consumed_at INTEGER)""")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_password_reset_limits (
                scope_hash TEXT PRIMARY KEY, window_start INTEGER NOT NULL,
                sent_count INTEGER NOT NULL, last_sent_at INTEGER NOT NULL)""")
            self.conn.execute("CREATE INDEX IF NOT EXISTS account_password_reset_limits_window ON account_password_reset_limits(window_start)")
            self.conn.execute("""CREATE TABLE IF NOT EXISTS account_security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                action TEXT NOT NULL, actor TEXT NOT NULL, user_id TEXT,
                outcome TEXT NOT NULL)""")
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
        if not isinstance(username, str):
            raise _invalid()
        username = username.strip().casefold()
        if not USERNAME_RE.fullmatch(username) and not AccountStore._valid_email(username):
            raise _invalid()
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

    @staticmethod
    def _valid_email(value: str) -> bool:
        if not isinstance(value, str) or len(value) > 254:
            return False
        try:
            validate_email(value)
        except ValidationError:
            return False
        return True

    @staticmethod
    def _reset_secret() -> bytes:
        secret = settings.SECRET_KEY
        if not isinstance(secret, str) or len(secret) < 16 or secret in {
            "fuwu-local-development-key", "change-this-for-local-use",
        }:
            raise ReleaseError("email_reset_unavailable", "password reset is unavailable", 503)
        return secret.encode("utf-8")

    @staticmethod
    def _reset_digest(value: str, secret: bytes) -> str:
        return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def set_superadmin_email(self, current_username: str, email: str) -> dict[str, Any]:
        """Move the sole active superadmin login name to a verified-capable email.

        This host-only recovery operation intentionally does not need the lost
        account password. Access to the production account database is its
        authorization boundary; the action is recorded without either address.
        """
        if not isinstance(current_username, str) or not isinstance(email, str):
            raise _invalid()
        current_username = current_username.strip().casefold()
        email = email.strip().casefold()
        if not current_username or not self._valid_email(email):
            raise _invalid()
        with self._transaction():
            row = self.conn.execute("SELECT * FROM account_users WHERE username=?", (current_username,)).fetchone()
            if row is None or not row["active"] or "superadmin" not in json.loads(row["roles_json"]):
                raise ReleaseError("superadmin_not_found", "active superadmin was not found", 404)
            if self.conn.execute("SELECT 1 FROM account_users WHERE username=? AND id<>?", (email, row["id"])).fetchone():
                raise ReleaseError("username_exists", "username already exists", 409)
            try:
                self.conn.execute("UPDATE account_users SET username=?,updated_at=? WHERE id=?", (email, _timestamp(), row["id"]))
            except sqlite3.IntegrityError:
                raise ReleaseError("username_exists", "username already exists", 409) from None
            self._revoke(row["id"])
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (row["id"],))
            self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                (_timestamp(), "superadmin_email_set", "host-cli", row["id"], "success"))
            return self._public(self._row(row["id"]))

    def create_password_reset_code(self, email: str) -> tuple[str, str, str] | None:
        """Create a short-lived code and apply per-email throttles.

        The caller sends the returned code only when a matching active account
        exists. Unknown addresses and throttled requests return the same value.
        """
        if not isinstance(email, str):
            return None
        email = email.strip().casefold()
        if not self._valid_email(email):
            return None
        secret = self._reset_secret()
        email_hash = self._reset_digest("email:" + email, secret)
        now = int(time.time())
        with self._transaction():
            self.conn.execute("DELETE FROM account_password_reset_limits WHERE window_start<?", (now - 3600,))
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE expires_at<=?", (now,))
            user = self.conn.execute("SELECT id FROM account_users WHERE username=? AND active=1", (email,)).fetchone()
            if user is None:
                return None
            scope_hash = self._reset_digest("email:" + email_hash, secret)
            row = self.conn.execute("SELECT * FROM account_password_reset_limits WHERE scope_hash=?", (scope_hash,)).fetchone()
            if row and now - row["window_start"] < 3600:
                if now - row["last_sent_at"] < RESET_CODE_COOLDOWN_SECONDS or row["sent_count"] >= RESET_CODE_MAX_PER_HOUR:
                    return None
            elif self.conn.execute("SELECT COUNT(*) FROM account_password_reset_limits").fetchone()[0] >= RESET_LIMIT_ROW_CAPACITY:
                return None
            if row and now - row["window_start"] < 3600:
                self.conn.execute("UPDATE account_password_reset_limits SET sent_count=sent_count+1,last_sent_at=? WHERE scope_hash=?", (now, scope_hash))
            else:
                self.conn.execute("INSERT OR REPLACE INTO account_password_reset_limits VALUES (?,?,?,?)", (scope_hash, now, 1, now))
            code = f"{secrets.randbelow(1_000_000):06d}"
            challenge_id = secrets.token_urlsafe(24)
            code_hash = self._reset_digest("code:" + email_hash + ":" + code, secret)
            self.conn.execute("INSERT OR REPLACE INTO account_password_reset_codes VALUES (?,?,?,?,?,?,0,NULL)",
                (email_hash, challenge_id, user["id"], code_hash, now, now + RESET_CODE_LIFETIME_SECONDS))
            self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                (_timestamp(), "password_reset_code_requested", "anonymous", user["id"], "issued"))
            return email, code, challenge_id

    def discard_password_reset_code(self, email: str, challenge_id: str) -> None:
        if not isinstance(email, str) or not isinstance(challenge_id, str):
            return
        secret = self._reset_secret()
        email_hash = self._reset_digest("email:" + email.strip().casefold(), secret)
        with self._transaction():
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE email_hash=? AND challenge_id=?", (email_hash, challenge_id))

    def reset_password_with_code(self, email: str, code: str, password: str) -> None:
        if not isinstance(email, str) or not self._valid_email(email.strip().casefold()) or not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
            raise ReleaseError("invalid_reset_code", "verification code is invalid or expired", 400)
        email = email.strip().casefold()
        secret = self._reset_secret()
        email_hash = self._reset_digest("email:" + email, secret)
        supplied_hash = self._reset_digest("code:" + email_hash + ":" + code, secret)
        now = int(time.time())
        claim_time = now
        invalid = False
        user_id = None
        challenge_id = None
        with self._transaction():
            challenge = self.conn.execute("SELECT * FROM account_password_reset_codes WHERE email_hash=?", (email_hash,)).fetchone()
            if challenge is None or challenge["consumed_at"] is not None or challenge["expires_at"] <= now or challenge["attempts"] >= RESET_CODE_MAX_ATTEMPTS:
                invalid = True
            elif not hmac.compare_digest(challenge["code_hash"], supplied_hash):
                attempts = challenge["attempts"] + 1
                if attempts >= RESET_CODE_MAX_ATTEMPTS:
                    self.conn.execute("DELETE FROM account_password_reset_codes WHERE email_hash=?", (email_hash,))
                else:
                    self.conn.execute("UPDATE account_password_reset_codes SET attempts=? WHERE email_hash=?", (attempts, email_hash))
                self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                    (_timestamp(), "password_reset_attempt", "anonymous", challenge["user_id"], "rejected"))
                invalid = True
            else:
                user = self.conn.execute("SELECT * FROM account_users WHERE id=? AND username=? AND active=1", (challenge["user_id"], email)).fetchone()
                if user is None:
                    self.conn.execute("DELETE FROM account_password_reset_codes WHERE email_hash=?", (email_hash,))
                    invalid = True
                else:
                    claimed = self.conn.execute("UPDATE account_password_reset_codes SET consumed_at=? WHERE email_hash=? AND consumed_at IS NULL", (claim_time, email_hash))
                    if claimed.rowcount != 1:
                        invalid = True
                    else:
                        user_id = user["id"]
                        challenge_id = challenge["challenge_id"]
        if invalid:
            raise ReleaseError("invalid_reset_code", "verification code is invalid or expired", 400)

        try:
            password_hash = self._password(password)
        except BaseException:
            with self._transaction():
                self.conn.execute("UPDATE account_password_reset_codes SET consumed_at=NULL WHERE email_hash=? AND challenge_id=? AND consumed_at=?",
                    (email_hash, challenge_id, claim_time))
            raise
        now = int(time.time())
        invalid = False
        with self._transaction():
            challenge = self.conn.execute("SELECT * FROM account_password_reset_codes WHERE email_hash=?", (email_hash,)).fetchone()
            if challenge is None or challenge["challenge_id"] != challenge_id or challenge["user_id"] != user_id or challenge["consumed_at"] != claim_time or challenge["expires_at"] <= now or challenge["attempts"] >= RESET_CODE_MAX_ATTEMPTS or not hmac.compare_digest(challenge["code_hash"], supplied_hash):
                invalid = True
            else:
                user = self.conn.execute("SELECT * FROM account_users WHERE id=? AND username=? AND active=1", (user_id, email)).fetchone()
                if user is None:
                    self.conn.execute("DELETE FROM account_password_reset_codes WHERE email_hash=?", (email_hash,))
                    invalid = True
                else:
                    self.conn.execute("UPDATE account_users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, _timestamp(), user["id"]))
                    self._revoke(user["id"])
                    self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (user["id"],))
                    self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                        (_timestamp(), "password_reset_completed", "anonymous", user["id"], "success"))
        if invalid:
            raise ReleaseError("invalid_reset_code", "verification code is invalid or expired", 400)

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
            if fields["username"] != current["username"] or not fields["active"]:
                self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (current["id"],))
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
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (current["id"],))
            self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                (_timestamp(), "password_reset_completed", actor_id, current["id"], "admin_reset"))

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        password_hash = self._password(new_password)
        with self._transaction():
            row = self._row(user_id)
            if not row["active"] or not isinstance(current_password, str) or len(current_password) > PASSWORD_MAX_LENGTH or not PBKDF2PasswordHasher().verify(current_password, row["password_hash"]):
                raise _unauthorized()
            self.conn.execute("UPDATE account_users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, _timestamp(), row["id"]))
            self._revoke(row["id"])
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (row["id"],))
            self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                (_timestamp(), "password_changed", row["id"], row["id"], "success"))

    def reset_password_from_host(self, username: str, password: str, *, expected_user_id: str) -> None:
        """Recover an active account using host access, never an HTTP route.

        Authorization is the operator's write access to the account database.
        This does not grant roles or reactivate disabled accounts.
        """
        if not isinstance(username, str) or not username.strip() or len(username) > 254:
            raise _invalid()
        username = username.strip().casefold()
        password_hash = self._password(password)
        with self._transaction():
            row = self.conn.execute("SELECT id FROM account_users WHERE username=? AND active=1", (username,)).fetchone()
            if row is None or row["id"] != expected_user_id:
                raise ReleaseError("active_account_not_found", "active account was not found", 404)
            self.conn.execute("UPDATE account_users SET password_hash=?,updated_at=? WHERE id=?", (password_hash, _timestamp(), row["id"]))
            self._revoke(row["id"])
            self.conn.execute("DELETE FROM account_password_reset_codes WHERE user_id=?", (row["id"],))
            account_scope, _ = self._login_scopes(username, "host-cli")
            self.conn.execute("DELETE FROM account_login_attempts WHERE scope_hash=?", (account_scope,))
            self.conn.execute("INSERT INTO account_security_events(created_at,action,actor,user_id,outcome) VALUES (?,?,?,?,?)",
                (_timestamp(), "password_reset_completed", "host-cli", row["id"], "host_reset"))

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
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 254 or len(password) > PASSWORD_MAX_LENGTH:
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

    parser = SafeArgumentParser(description="fuwu 账号维护工具（不接收明文密码参数）")
    parser.add_argument("command", choices=["bootstrap", "bootstrap-config", "set-superadmin-email", "reset-password"])
    parser.add_argument("--database", default=None, help="账号与发行共用的 SQLite 文件")
    parser.add_argument("--config", default=None, help="项目根目录配置文件（bootstrap-config 使用）")
    parser.add_argument("--username", default=None, help="要重置密码的邮箱或用户名（reset-password 使用，省略则交互输入）")
    args = parser.parse_args(argv)
    if args.username is not None and args.command != "reset-password":
        parser.error("unsupported arguments")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        if args.command == "reset-password":
            database = args.database or os.environ.get("FUWU_DATABASE")
            if not database or database == ":memory:":
                print("操作失败：请用 --database 或 FUWU_DATABASE 指定现有账号数据库")
                return 1
            if not sys.stdin.isatty():
                print("操作失败：请在交互终端运行，以隐藏输入新密码")
                return 1
            with AccountStore(database, existing_only=True) as store:
                username = (args.username if args.username is not None else input("要重置的邮箱或用户名：")).strip().casefold()
                with store._lock:
                    row = store.conn.execute("SELECT id FROM account_users WHERE username=? AND active=1", (username,)).fetchone()
                if row is None:
                    raise ReleaseError("active_account_not_found", "active account was not found", 404)
                print("即将重置账号：" + username)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    password = getpass.getpass("新密码（12 至 256 字符，不显示）：")
                    confirmation = getpass.getpass("再次输入新密码（不显示）：")
                if not hmac.compare_digest(password.encode(), confirmation.encode()):
                    print("操作失败：两次密码不一致，密码未修改")
                    return 1
                store.reset_password_from_host(username, password, expected_user_id=row["id"])
            print("密码已修改；该账号的现有会话和找回验证码已撤销，请使用新密码登录。")
        elif args.command == "bootstrap-config":
            from .runtime_config import bootstrap_from_config
            bootstrap_from_config(args.database, config=args.config)
        elif args.command == "set-superadmin-email":
            username = input("当前超级管理员用户名：").strip()
            email = input("新的超级管理员邮箱：").strip()
            confirmation = input("再次输入邮箱：").strip()
            if not hmac.compare_digest(email.casefold().encode(), confirmation.casefold().encode()):
                print("操作失败：两次邮箱不一致")
                return 1
            with AccountStore(args.database) as store:
                store.set_superadmin_email(username, email)
            print("超级管理员登录名已设为邮箱；下一步可在登录页请求密码重置验证码。")
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
        if args.command in {"bootstrap", "bootstrap-config"}:
            print("首个超级管理员已创建；请通过账号密码登录。")
        return 0
    except (ReleaseError, EOFError, KeyboardInterrupt) as error:
        print("操作失败：" + (error.message if isinstance(error, ReleaseError) else "交互已取消"))
        return 1
    except getpass.GetPassWarning:
        print("操作失败：终端无法隐藏密码输入，请在交互终端重试")
        return 1
    except (sqlite3.Error, OSError):
        print("操作失败：账号存储不可用，请核对现有数据库路径与权限")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

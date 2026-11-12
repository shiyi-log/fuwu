"""SQLite persistence for projects, releases, latest pointers and audit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ReleaseError
from .projects import normalize_repository, project_create_values, project_update_values, validate_project_id
from .validation import now_iso, project_value, validate_channel


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
        project_id = project_value(validate_project_id, project_id)
        normalized_repository = project_value(normalize_repository, repository)
        row = self.get_project(project_id)
        if row:
            if not row["active"]:
                raise ReleaseError("project_inactive", "project is inactive", 403)
            if project_value(normalize_repository, row["repository"]) != normalized_repository:
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
        project_id = project_value(validate_project_id, project_id)
        row = self.conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        return self._project_dict(row) if row else None

    def create_project(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = project_value(project_create_values, request)
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
        project_id = project_value(validate_project_id, project_id)
        changes = project_value(project_update_values, request)
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
            ids = sorted({project_value(validate_project_id, project_id) for project_id in project_ids})
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
            args.append(project_value(validate_project_id, project_id))
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

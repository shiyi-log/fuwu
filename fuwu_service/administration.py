"""Non-secret service preferences and bounded, structured operational logs."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ReleaseError, now_iso


class AdministrationStore:
    DEFAULTS = {
        "appName": "fuwu",
        "timezone": "Asia/Shanghai",
        "defaultChannel": "stable",
        "logRetentionDays": 90,
    }
    STORAGE_SETTINGS = {
        "bucket": "FUWU_S3_BUCKET",
        "region": "FUWU_S3_REGION",
        "endpoint": "FUWU_S3_ENDPOINT",
        "prefix": "FUWU_S3_PREFIX",
    }

    def __init__(self, database: str | Path):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(database), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS service_settings (
                key TEXT PRIMARY KEY, value_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                level TEXT NOT NULL, category TEXT NOT NULL, action TEXT NOT NULL,
                actor TEXT NOT NULL, request_id TEXT NOT NULL,
                project_id TEXT, channel TEXT, status_code INTEGER NOT NULL,
                method TEXT NOT NULL, route TEXT NOT NULL, duration_ms INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS operation_logs_project_time
                ON operation_logs(project_id, id DESC);
        """)
        self.conn.commit()

    def settings(self, service: Any) -> dict[str, Any]:
        with self._lock:
            result = dict(self.DEFAULTS)
            for row in self.conn.execute("SELECT key,value_json FROM service_settings"):
                if row["key"] in result:
                    result[row["key"]] = json.loads(row["value_json"])
        try:
            version = importlib.metadata.version("fuwu")
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        result["readOnly"] = {
            "version": version,
            "provider": type(service.store).__name__,
            "bucket": getattr(service.store, "bucket", None),
            "region": getattr(service.store, "region", None),
            "prefix": service.prefix,
            "publicReadMode": "anonymous-https",
            "repository": "SQLite",
            "singleInstance": True,
        }
        return result

    def update_settings(self, body: Mapping[str, Any], service: Any) -> dict[str, Any]:
        if not body or set(body) - set(self.DEFAULTS):
            raise ReleaseError("invalid_settings", "settings are invalid", 422)
        for key, value in body.items():
            if key == "appName" and (not isinstance(value, str) or not 1 <= len(value.strip()) <= 64):
                raise ReleaseError("invalid_settings", "settings are invalid", 422)
            if key == "timezone":
                try:
                    if not isinstance(value, str):
                        raise ValueError
                    ZoneInfo(value)
                except (ZoneInfoNotFoundError, ValueError, TypeError):
                    raise ReleaseError("invalid_settings", "settings are invalid", 422) from None
            if key == "defaultChannel" and (not isinstance(value, str) or value not in {"stable", "beta"}):
                raise ReleaseError("invalid_settings", "settings are invalid", 422)
            if key == "logRetentionDays" and (type(value) is not int or not 7 <= value <= 3650):
                raise ReleaseError("invalid_settings", "settings are invalid", 422)
        with self._lock:
            for key, value in body.items():
                if isinstance(value, str):
                    value = value.strip()
                self.conn.execute("INSERT INTO service_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (key, json.dumps(value)))
            self.conn.commit()
        return self.settings(service)

    def storage_configuration(self, fallback: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Return only non-secret object-storage settings.

        The `configured` marker is derived from the presence of a persisted
        bucket row, so no credential or credential hint is stored in SQLite.
        """
        result = dict(fallback or {})
        configured = False
        with self._lock:
            for field, key in self.STORAGE_SETTINGS.items():
                row = self.conn.execute("SELECT value_json FROM service_settings WHERE key=?", ("storage." + field,)).fetchone()
                if row is not None:
                    result[field] = json.loads(row[0])
                    if field == "bucket":
                        configured = True
        result["configured"] = configured
        return result

    def save_storage_configuration(self, settings: Mapping[str, Any]) -> None:
        """Persist the validated, non-secret storage destination atomically."""
        if not settings or set(settings) != set(self.STORAGE_SETTINGS):
            raise ReleaseError("invalid_storage_settings", "storage settings are invalid", 422)
        with self._lock:
            for field in self.STORAGE_SETTINGS:
                value = settings[field]
                if not isinstance(value, str):
                    raise ReleaseError("invalid_storage_settings", "storage settings are invalid", 422)
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for field, value in settings.items():
                    self.conn.execute(
                        "INSERT INTO service_settings(key,value_json) VALUES (?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                        ("storage." + field, json.dumps(value)),
                    )
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                raise

    def record(self, *, category: str, action: str, actor: str, request_id: str,
               project_id: str | None, channel: str | None, status_code: int,
               method: str, route: str, duration_ms: int) -> None:
        """Only the dispatcher supplies these fields; bodies and headers never enter logs."""
        level = "error" if status_code >= 500 else "warning" if status_code >= 400 else "info"
        with self._lock:
            self.conn.execute(
                "INSERT INTO operation_logs(created_at,level,category,action,actor,request_id,project_id,channel,status_code,method,route,duration_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_iso(), level, category, action, actor, request_id, project_id, channel,
                 status_code, method, route, max(0, int(duration_ms))),
            )
            setting = self.conn.execute("SELECT value_json FROM service_settings WHERE key='logRetentionDays'").fetchone()
            days = json.loads(setting[0]) if setting else self.DEFAULTS["logRetentionDays"]
            cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat(timespec="seconds")
            self.conn.execute("DELETE FROM operation_logs WHERE created_at < ?", (cutoff,))
            self.conn.commit()

    def logs(self, *, project_id: str | None = None, channel: str | None = None,
             category: str | None = None, level: str | None = None, limit: int = 50,
             project_scope: Iterable[str] | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        for column, value in (("project_id", project_id), ("channel", channel), ("category", category), ("level", level)):
            if value:
                clauses.append(column + "=?")
                values.append(value)
        if project_scope is not None:
            scope = list(project_scope)
            if not scope:
                return []
            clauses.append("project_id IN (" + ",".join("?" for _ in scope) + ")")
            values.extend(scope)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        values.append(min(max(limit, 1), 500))
        with self._lock:
            return [dict(row) for row in self.conn.execute("SELECT * FROM operation_logs" + where + " ORDER BY id DESC LIMIT ?", values)]


_stores: dict[str, AdministrationStore] = {}
_stores_lock = threading.Lock()


def administration_store() -> AdministrationStore:
    database = os.environ.get("FUWU_DATABASE", "fuwu.sqlite3")
    with _stores_lock:
        if database not in _stores:
            _stores[database] = AdministrationStore(database)
        return _stores[database]

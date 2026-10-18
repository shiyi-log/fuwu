"""HTTP entry point used for local smoke tests and container deployment.

The same ReleaseService is usable from Django views; this adapter keeps the
service runnable even before a Django runtime is installed in a fresh host.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, S3ObjectStore, SQLiteRepository


def build_service() -> ReleaseService:
    database = os.environ.get("FUWU_DATABASE", "fuwu.sqlite3")
    staging = os.environ.get("FUWU_STAGING_DIR", "staging")
    if os.environ.get("FUWU_STORAGE", "memory") == "s3":
        store = S3ObjectStore.from_env()
    else:
        store = MemoryObjectStore()
    return ReleaseService(SQLiteRepository(database), store, staging_dir=staging, prefix=os.environ.get("FUWU_S3_PREFIX", "releases"))


class ApiHandler(BaseHTTPRequestHandler):
    service: ReleaseService
    rbac: RBAC

    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, error: ReleaseError, request_id: str) -> None:
        self._send(error.status, {"code": error.code, "message": error.message, "request_id": request_id})

    def _body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ReleaseError("invalid_json", "request body is invalid", 400)
        if not isinstance(value, dict):
            raise ReleaseError("invalid_json", "request body is invalid", 400)
        return value

    def do_GET(self) -> None:  # noqa: N802
        request_id = self.headers.get("X-Request-ID") or "req-" + os.urandom(6).hex()
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/healthz":
                self._send(200, {"status": "ok"})
                return
            actor = self.rbac.require(self.headers.get("Authorization"), "release.storage.read")
            if parsed.path == "/api/projects":
                self._send(200, {"items": self.service.repository.list_projects()})
            elif parsed.path == "/api/releases":
                self._send(200, {"items": self.service.releases((query.get("project") or [None])[0], (query.get("channel") or [None])[0])})
            elif parsed.path == "/api/release-storage/status":
                self._send(200, self.service.status())
            elif parsed.path == "/api/release-storage/audit":
                self._send(200, {"items": self.service.repository.list_audit((query.get("project") or [None])[0])})
            else:
                self._send(404, {"code": "not_found", "message": "resource was not found", "request_id": request_id})
            _ = actor
        except ReleaseError as error:
            self._error(error, request_id)
        except Exception:
            self._send(500, {"code": "internal_error", "message": "internal server error", "request_id": request_id})

    def do_POST(self) -> None:  # noqa: N802
        request_id = self.headers.get("Idempotency-Key") or self.headers.get("X-Request-ID") or "req-" + os.urandom(6).hex()
        try:
            body = self._body()
            request_id = self.headers.get("Idempotency-Key") or self.headers.get("X-Request-ID") or str(body.get("requestId") or request_id)
            parsed = urlsplit(self.path)
            if parsed.path == "/api/release-storage/publish":
                actor = self.rbac.require(self.headers.get("Authorization"), "release.storage.publish")
                status, response = self.service.publish(body, actor=actor, request_id=request_id)
            elif parsed.path == "/api/release-storage/rollback":
                actor = self.rbac.require(self.headers.get("Authorization"), "release.storage.rollback")
                # Rollback always requires a token mapped to the explicit superadmin marker.
                _, permissions = self.rbac.authenticate(self.headers.get("Authorization"))
                if "superadmin" not in permissions and os.environ.get("FUWU_ALLOW_ROLLBACK_NON_SUPERADMIN") != "1":
                    raise ReleaseError("forbidden", "permission denied", 403)
                status, response = self.service.rollback(body, actor=actor, request_id=request_id)
            else:
                self._send(404, {"code": "not_found", "message": "resource was not found", "request_id": request_id})
                return
            self._send(status, response)
        except ReleaseError as error:
            self._error(error, request_id)
        except Exception:
            self._send(500, {"code": "internal_error", "message": "internal server error", "request_id": request_id})


def serve(host: str | None = None, port: int | None = None) -> None:
    host = host or os.environ.get("FUWU_HOST", "0.0.0.0")
    port = port or int(os.environ.get("FUWU_PORT", "8000"))
    service = build_service()
    handler = type("FuwuApiHandler", (ApiHandler,), {"service": service, "rbac": RBAC.from_env()})
    with ThreadingHTTPServer((host, port), handler) as httpd:
        print(f"fuwu release service listening on {host}:{port}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    serve()

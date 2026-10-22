"""Lightweight HTTP adapter using the same API dispatcher as Django."""
from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import ssl
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .administration import administration_store
from .api import handle_api, requires_generated_request_id
from .core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, S3ObjectStore, SQLiteRepository
from .storage_settings import build_managed_store


# 配置日志格式
def setup_logging():
    """配置带时间戳的日志"""
    log_format = "%(asctime)s [%(levelname)s] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    # 配置根日志记录器
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt=date_format,
        stream=sys.stdout,
        force=True,
    )

    return logging.getLogger(__name__)


def build_storage() -> tuple[object, str]:
    managed = build_managed_store(administration_store(), env=os.environ)
    if managed is not None:
        return managed
    store = S3ObjectStore.from_env() if os.environ.get("FUWU_STORAGE", "memory") == "s3" else MemoryObjectStore()
    return store, os.environ.get("FUWU_S3_PREFIX", "releases")


def build_service() -> ReleaseService:
    database = os.environ.get("FUWU_DATABASE", "fuwu.sqlite3")
    staging = os.environ.get("FUWU_STAGING_DIR", "staging")
    store, prefix = build_storage()
    return ReleaseService(SQLiteRepository(database), store, staging_dir=staging, prefix=prefix)


def request_identifier(value: str | None) -> str:
    if value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) and not re.match(r"(?:AKIA|ASIA)[A-Z0-9]{16}$", value):
        return value
    return "req-" + os.urandom(12).hex()


class ApiHandler(BaseHTTPRequestHandler):
    service: ReleaseService
    rbac: RBAC

    def log_message(self, format: str, *args: object) -> None:
        """记录请求日志，带时间戳"""
        logger = logging.getLogger(__name__)
        # 只记录非健康检查的请求
        if "/health" not in str(args):
            logger.info(f"{self.client_address[0]} - {format % args}")

    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 65536:
                raise ReleaseError("request_too_large", "request body is too large", 413)
            value = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            raise ReleaseError("invalid_json", "request body is invalid", 400) from None
        if not isinstance(value, dict):
            raise ReleaseError("invalid_json", "request body is invalid", 400)
        return value

    def _request_is_secure(self) -> bool:
        try:
            if ipaddress.ip_address(self.client_address[0]).is_loopback:
                return True
        except (ValueError, IndexError):
            pass
        return isinstance(self.connection, ssl.SSLSocket)

    def _dispatch(self) -> None:
        header_request_id = self.headers.get("Idempotency-Key") or self.headers.get("X-Request-ID")
        request_id = request_identifier(header_request_id)
        try:
            parsed = urlsplit(self.path)
            # Generate before reading a sensitive body.  Keep URL parsing in
            # this guarded block so malformed request targets still receive a
            # structured error instead of terminating the handler thread.
            if requires_generated_request_id(parsed.path):
                request_id = "req-" + os.urandom(12).hex()
            # DELETE requests are parsed too so the shared dispatcher can
            # enforce the endpoint's empty-body contract.  Previously the
            # lightweight adapter discarded a DELETE body before dispatch,
            # which made a malformed revoke request look valid.
            body = self._body() if self.command in {"POST", "PATCH", "DELETE"} else {}
            body_request_id = None if parsed.path == "/api/settings/storage" else body.get("requestId")
            if not requires_generated_request_id(parsed.path):
                request_id = request_identifier(header_request_id or str(body_request_id or request_id))
            status, response = handle_api(self.service, self.rbac, method=self.command, path=parsed.path,
                query={key: values[0] for key, values in parse_qs(parsed.query).items()}, body=body,
                authorization=self.headers.get("Authorization"), request_id=request_id,
                client_id=self.client_address[0], request_secure=self._request_is_secure())
        except ReleaseError as error:
            status, response = error.status, {"code": error.code, "message": error.message, "request_id": request_id}
        except Exception:
            status, response = 500, {"code": "internal_error", "message": "internal server error", "request_id": request_id}
        self._send(status, response)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch


def serve(host: str | None = None, port: int | None = None) -> None:
    logger = setup_logging()
    host = host or os.environ.get("FUWU_HOST", "0.0.0.0")
    port = port or int(os.environ.get("FUWU_PORT", "8088"))
    handler = type("FuwuApiHandler", (ApiHandler,), {"service": build_service(), "rbac": RBAC.from_env()})

    try:
        with ThreadingHTTPServer((host, port), handler) as httpd:
            logger.info(f"fuwu release service listening on {host}:{port}")
            logger.info(f"API endpoints available at http://{host}:{port}/api/")
            httpd.serve_forever()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            logger.error(f"Port {port} is already in use. Please:")
            logger.error(f"  1. Check for other processes: lsof -ti:{port}")
            logger.error(f"  2. Kill them: kill -9 $(lsof -ti:{port})")
            logger.error(f"  3. Or set a different port: FUWU_PORT=8089 fuwu-server")
            sys.exit(1)
        else:
            raise


if __name__ == "__main__":
    serve()

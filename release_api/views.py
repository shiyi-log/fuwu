"""Django adapter for the shared lightweight service API."""
from __future__ import annotations

import json
import ipaddress
import os
from functools import lru_cache

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from fuwu_service.api import handle_api
from fuwu_service.core import RBAC, ReleaseError, ReleaseService, SQLiteRepository
from fuwu_service.server import build_storage, request_identifier


@lru_cache(maxsize=1)
def get_service() -> ReleaseService:
    database = os.environ.get("FUWU_DATABASE", "fuwu.sqlite3")
    store, prefix = build_storage()
    return ReleaseService(SQLiteRepository(database), store, staging_dir=os.environ.get("FUWU_STAGING_DIR", "staging"), prefix=prefix)


@csrf_exempt
def api_view(request, **_kwargs):
    header_request_id = request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID")
    request_id = request_identifier(header_request_id)
    try:
        body = {}
        if request.method in {"POST", "PATCH"}:
            if len(request.body) > 65536:
                raise ReleaseError("request_too_large", "request body is too large", 413)
            try:
                body = json.loads(request.body or b"{}")
            except (ValueError, json.JSONDecodeError):
                raise ReleaseError("invalid_json", "request body is invalid", 400) from None
            if not isinstance(body, dict):
                raise ReleaseError("invalid_json", "request body is invalid", 400)
        body_request_id = None if request.path == "/api/settings/storage" else body.get("requestId")
        request_id = request_identifier(header_request_id or str(body_request_id or request_id))
        if request.path == "/healthz":
            status, payload = 200, {"status": "ok"}
        else:
            remote_address = request.META.get("REMOTE_ADDR", "")
            try:
                loopback = ipaddress.ip_address(remote_address).is_loopback
            except ValueError:
                loopback = False
            status, payload = handle_api(get_service(), RBAC.from_env(), method=request.method, path=request.path,
                query={key: request.GET[key] for key in request.GET}, body=body,
                authorization=request.headers.get("Authorization"), request_id=request_id,
                client_id=remote_address or "local", request_secure=request.is_secure() or loopback)
    except ReleaseError as error:
        status, payload = error.status, {"code": error.code, "message": error.message, "request_id": request_id}
    except Exception:
        status, payload = 500, {"code": "internal_error", "message": "internal server error", "request_id": request_id}
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    return response


healthz = auth_login = auth_codes = auth_logout = user_info = api_view
projects = releases = status = publish = rollback = audit = api_view

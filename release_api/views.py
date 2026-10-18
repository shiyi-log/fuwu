from __future__ import annotations

import json
import os
from functools import lru_cache

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, S3ObjectStore, SQLiteRepository


@lru_cache(maxsize=1)
def get_service() -> ReleaseService:
    database = os.environ.get("FUWU_DATABASE", "fuwu.sqlite3")
    store = S3ObjectStore.from_env() if os.environ.get("FUWU_STORAGE", "memory") == "s3" else MemoryObjectStore()
    return ReleaseService(SQLiteRepository(database), store, staging_dir=os.environ.get("FUWU_STAGING_DIR", "staging"), prefix=os.environ.get("FUWU_S3_PREFIX", "releases"))


def _request_id(request, body: dict | None = None) -> str:
    return request.headers.get("Idempotency-Key") or request.headers.get("X-Request-ID") or str((body or {}).get("requestId") or "req-" + os.urandom(6).hex())


def _json(request) -> dict:
    try:
        value = json.loads(request.body or b"{}")
    except (ValueError, json.JSONDecodeError):
        raise ReleaseError("invalid_json", "request body is invalid", 400)
    if not isinstance(value, dict):
        raise ReleaseError("invalid_json", "request body is invalid", 400)
    return value


def _error(error: ReleaseError, request_id: str) -> JsonResponse:
    return JsonResponse({"code": error.code, "message": error.message, "request_id": request_id}, status=error.status)


def _actor(request, permission: str) -> str:
    return RBAC.from_env().require(request.headers.get("Authorization"), permission)


@require_http_methods(["GET"])
def healthz(request):
    return JsonResponse({"status": "ok"})


@require_http_methods(["GET"])
def projects(request):
    request_id = _request_id(request)
    try:
        _actor(request, "release.storage.read")
        return JsonResponse({"items": get_service().repository.list_projects(), "request_id": request_id})
    except ReleaseError as error:
        return _error(error, request_id)
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), request_id)


@require_http_methods(["GET"])
def releases(request):
    request_id = _request_id(request)
    try:
        _actor(request, "release.storage.read")
        return JsonResponse({"items": get_service().releases(request.GET.get("project"), request.GET.get("channel")), "request_id": request_id})
    except ReleaseError as error:
        return _error(error, request_id)
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), request_id)


@require_http_methods(["GET"])
def status(request):
    request_id = _request_id(request)
    try:
        _actor(request, "release.storage.read")
        return JsonResponse({**get_service().status(), "request_id": request_id})
    except ReleaseError as error:
        return _error(error, request_id)
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), request_id)


@csrf_exempt
@require_http_methods(["POST"])
def publish(request):
    try:
        body = _json(request)
        request_id = _request_id(request, body)
        actor = _actor(request, "release.storage.publish")
        status_code, response = get_service().publish(body, actor=actor, request_id=request_id)
        return JsonResponse(response, status=status_code)
    except ReleaseError as error:
        return _error(error, locals().get("request_id", _request_id(request)))
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), locals().get("request_id", _request_id(request)))


@csrf_exempt
@require_http_methods(["POST"])
def rollback(request):
    try:
        body = _json(request)
        request_id = _request_id(request, body)
        actor = _actor(request, "release.storage.rollback")
        _, permissions = RBAC.from_env().authenticate(request.headers.get("Authorization"))
        if "superadmin" not in permissions and os.environ.get("FUWU_ALLOW_ROLLBACK_NON_SUPERADMIN") != "1":
            raise ReleaseError("forbidden", "permission denied", 403)
        status_code, response = get_service().rollback(body, actor=actor, request_id=request_id)
        return JsonResponse(response, status=status_code)
    except ReleaseError as error:
        return _error(error, locals().get("request_id", _request_id(request)))
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), locals().get("request_id", _request_id(request)))


@require_http_methods(["GET"])
def audit(request):
    request_id = _request_id(request)
    try:
        _actor(request, "release.storage.read")
        return JsonResponse({"items": get_service().repository.list_audit(request.GET.get("project")), "request_id": request_id})
    except ReleaseError as error:
        return _error(error, request_id)
    except Exception:
        return _error(ReleaseError("internal_error", "internal server error", 500), request_id)

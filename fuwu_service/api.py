"""Shared API dispatcher for Django and ThreadingHTTPServer."""

from __future__ import annotations

import re
import secrets
import time
from typing import Any, Mapping

from . import auth
from .administration import administration_store
from .core import PROJECT_RE, RBAC, ReleaseError, ReleaseService, validate_channel
from .storage_settings import storage_settings_payload, update_storage_settings


def _user(rbac: RBAC, authorization: str | None) -> dict[str, Any]:
    user = rbac.public_user(authorization)
    if user is None:
        raise ReleaseError("account_required", "a user account is required", 403)
    return user


def _accounts():
    from .accounts import account_store
    return account_store()


def _allowed_project(rbac: RBAC, authorization: str | None, value: Any) -> str:
    if not isinstance(value, str) or not PROJECT_RE.fullmatch(value):
        raise ReleaseError("invalid_project", "project identifier is invalid", 422)
    rbac.require_project(authorization, value)
    return value


def _scoped_status(service: ReleaseService, scope: list[str] | None, project: str | None, channel: str | None) -> dict[str, Any]:
    if scope is None or project:
        result = service.status(project, channel)
    else:
        records = [service.status(item, channel) for item in scope]
        result = service.status(None, channel)
        result["projects"] = len(service.repository.list_projects(scope))
        result["releases"] = sum(item.get("releases", 0) for item in records)
        result["jobs"] = {operation: {"status": "idle", "progress": 0, "jobId": None, "requestId": None, "updatedAt": None}
                          for operation in ("validation", "publish", "rollback")}
        for operation in ("validation", "publish", "rollback"):
            jobs = [item.get("jobs", {}).get(operation, {}) for item in records]
            jobs = [job for job in jobs if job.get("updatedAt")]
            if jobs:
                result["jobs"][operation] = max(jobs, key=lambda job: job["updatedAt"])
    result["bucket"] = getattr(service.store, "bucket", None)
    result["region"] = getattr(service.store, "region", None)
    probe = getattr(service.store, "probe", None)
    if callable(probe):
        result.update(probe(timeout=3))
    else:
        result.update({"reachable": None, "connectivityCheckedAt": None})
    return result


def handle_api(service: ReleaseService, rbac: RBAC, *, method: str, path: str,
               query: Mapping[str, str], body: Mapping[str, Any], authorization: str | None,
               request_id: str, client_id: str = "local", request_secure: bool = False) -> tuple[int, dict[str, Any]]:
    started = time.monotonic()
    if path == "/api/settings/storage":
        request_id = "req-" + secrets.token_hex(12)
    code, response = 500, {}
    actor, category, action, route = "anonymous", "system", "unknown", "/api/unknown"
    project_id: str | None = None
    channel: str | None = None
    try:
        if method == "GET" and path == "/healthz":
            return 200, {"status": "ok"}
        if path.startswith("/api/auth/"):
            category, action, route = "auth", path.rsplit("/", 1)[-1], path
            if path == "/api/auth/login" and method == "POST":
                # A connection identity is used only for throttling, never persisted in logs.
                response = auth.login(rbac, body, client_id=client_id)
                actor, _ = rbac.authenticate("Bearer " + response["data"]["accessToken"])
                code = 200
                return code, response
            if path == "/api/auth/logout" and method == "POST":
                actor, _ = rbac.authenticate(authorization)
                code, response = 200, auth.logout(rbac, authorization)
                return code, response
            if path == "/api/auth/codes" and method == "GET":
                actor, _ = rbac.authenticate(authorization)
                code, response = 200, auth.access_codes(rbac, authorization)
                return code, response
            if path == "/api/auth/password" and method == "POST":
                actor, _ = rbac.authenticate(authorization)
                user = _user(rbac, authorization)
                if set(body) != {"currentPassword", "newPassword"}:
                    raise ReleaseError("invalid_password_request", "password request is invalid", 422)
                _accounts().change_password(user["id"], body["currentPassword"], body["newPassword"])
                code, response = 200, {"status": "password_changed"}
                return code, response
        actor, permissions = rbac.authenticate(authorization)
        if path == "/api/user/info" and method == "GET":
            category, action, route = "account", "profile", path
            code, response = 200, auth.user_info(rbac, authorization)
        elif path == "/api/roles" and method == "GET":
            category, action, route = "account", "roles", path
            code, response = 200, {"items": _accounts().role_catalog()}
        elif path == "/api/users":
            category, action, route = "account", "users", path
            rbac.require(authorization, "users.manage")
            user = _user(rbac, authorization)
            if method == "GET":
                code, response = 200, {"items": _accounts().list_users(user["id"])}
            elif method == "POST":
                code, response = 201, _accounts().create_user(user["id"], body)
            else:
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
        elif match := re.fullmatch(r"/api/users/([A-Za-z0-9_-]+)(/password)?", path):
            category, action, route = "account", "reset_password" if match[2] else "update_user", "/api/users/{id}/password" if match[2] else "/api/users/{id}"
            rbac.require(authorization, "users.manage")
            user = _user(rbac, authorization)
            if match[2] and method == "POST":
                if set(body) != {"password"}:
                    raise ReleaseError("invalid_password_request", "password request is invalid", 422)
                _accounts().reset_password(user["id"], match[1], body["password"])
                code, response = 200, {"status": "password_reset"}
            elif not match[2] and method == "PATCH":
                code, response = 200, _accounts().update_user(user["id"], match[1], body)
            else:
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
        elif path == "/api/projects":
            category, action, route = "project", "projects", path
            if method == "GET":
                code, response = 200, {"items": service.repository.list_projects(rbac.project_scope(authorization))}
            elif method == "POST":
                rbac.require(authorization, "projects.manage")
                if rbac.project_scope(authorization) is not None:
                    raise ReleaseError("forbidden", "permission denied", 403)
                code, response = service.create_project(body, actor=actor, request_id=request_id)
                project_id = response.get("id")
            else:
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
        elif match := re.fullmatch(r"/api/projects/([a-z0-9][a-z0-9._-]*)", path):
            category, action, route = "project", "project", "/api/projects/{id}"
            project_id = _allowed_project(rbac, authorization, match[1])
            if method == "GET":
                record = service.repository.get_project(project_id)
                if not record:
                    raise ReleaseError("project_not_found", "project was not found", 404)
                code, response = 200, record
            elif method == "PATCH":
                rbac.require(authorization, "projects.manage")
                code, response = service.update_project(project_id, body, actor=actor, request_id=request_id)
            else:
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
        elif path in {"/api/releases", "/api/release-storage/status", "/api/release-storage/audit"} and method == "GET":
            category, action, route = "release", path.rsplit("/", 1)[-1], path
            rbac.require(authorization, "release.storage.read")
            project_id = _allowed_project(rbac, authorization, query["project"]) if query.get("project") else None
            channel = validate_channel(query["channel"]) if query.get("channel") else None
            scope = rbac.project_scope(authorization)
            if path == "/api/releases":
                if project_id or scope is None:
                    items = service.releases(project_id, channel)
                else:
                    items = [item for project in scope for item in service.releases(project, channel)]
                response = {"items": items}
            elif path.endswith("/status"):
                response = _scoped_status(service, scope, project_id, channel)
            else:
                if project_id or scope is None:
                    items = service.repository.list_audit(project_id, channel=channel)
                else:
                    items = [item for project in scope for item in service.repository.list_audit(project, channel=channel)]
                    items = sorted(items, key=lambda item: item["id"], reverse=True)[:100]
                response = {"items": items}
            code = 200
        elif path in {"/api/release-storage/publish", "/api/release-storage/rollback"} and method == "POST":
            category, action, route = "release", path.rsplit("/", 1)[-1], path
            rbac.require(authorization, "release.storage." + action)
            project_id = _allowed_project(rbac, authorization, body.get("project"))
            channel = validate_channel(body.get("channel", "stable"))
            if action == "rollback":
                _, permissions = rbac.authenticate(authorization)
                if "superadmin" not in permissions:
                    raise ReleaseError("forbidden", "permission denied", 403)
                code, response = service.rollback(body, actor=actor, request_id=request_id)
            else:
                code, response = service.publish(body, actor=actor, request_id=request_id)
        elif path == "/api/logs" and method == "GET":
            category, action, route = "system", "logs", path
            rbac.require(authorization, "logs.read")
            project_id = _allowed_project(rbac, authorization, query["project"]) if query.get("project") else None
            channel = validate_channel(query["channel"]) if query.get("channel") else None
            try:
                limit = int(query.get("limit", "50"))
            except ValueError:
                raise ReleaseError("invalid_query", "query is invalid", 422) from None
            code, response = 200, {"items": administration_store().logs(project_id=project_id, channel=channel,
                category=query.get("category"), level=query.get("level"), limit=limit,
                project_scope=rbac.project_scope(authorization))}
        elif path == "/api/settings":
            category, action, route = "settings", "settings", path
            if method == "GET":
                store = administration_store()
                code, response = 200, store.settings(service)
                response["storage"] = storage_settings_payload(
                    store,
                    service,
                    include_access_key_hint="settings.manage" in permissions,
                )
            elif method == "PATCH":
                rbac.require(authorization, "settings.manage")
                code, response = 200, administration_store().update_settings(body, service)
            else:
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
        elif path == "/api/settings/storage":
            category, action, route = "settings", "storage", path
            if method != "PATCH":
                code, response = 405, {"code": "method_not_allowed", "message": "method is not allowed"}
            else:
                rbac.require(authorization, "settings.manage")
                _, permissions = rbac.authenticate(authorization)
                if "superadmin" not in permissions:
                    raise ReleaseError("forbidden", "permission denied", 403)
                store = administration_store()
                response = update_storage_settings(store, service, body, request_secure=request_secure)
                code = 200
        else:
            code, response = 404, {"code": "not_found", "message": "resource was not found"}
    except ReleaseError as error:
        code, response = error.status, {"code": error.code, "message": error.message, "request_id": request_id}
    except Exception:
        code, response = 500, {"code": "internal_error", "message": "internal server error", "request_id": request_id}
    finally:
        if path != "/healthz":
            # Log only predefined route templates; unknown request paths and
            # query strings may contain secrets and must not be persisted.
            if route.startswith("/api/auth/") and route not in {"/api/auth/login", "/api/auth/logout", "/api/auth/codes", "/api/auth/password"}:
                route, action = "/api/unknown", "unknown"
            try:
                administration_store().record(category=category, action=action, actor=actor,
                    request_id=request_id, project_id=project_id, channel=channel,
                    status_code=code, method=method, route=route,
                    duration_ms=round((time.monotonic() - started) * 1000))
            except Exception:
                # A logging failure cannot turn an already committed publish
                # into a misleading HTTP failure. Domain audit remains durable.
                pass
    return code, response

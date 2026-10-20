"""Vben authentication contract shared by Django and the lightweight server."""

from __future__ import annotations

from typing import Any, Mapping

from .core import RBAC, ReleaseError


def login(rbac: RBAC, body: Mapping[str, Any], *, client_id: str = "local") -> dict[str, Any]:
    if set(body) == {"username", "password"}:
        store = rbac.account_store
        if store is None:
            raise ReleaseError("unauthorized", "authentication required", 401)
        result = store.login(body.get("username"), body.get("password"), client_id=client_id)
        return {"code": 0, "data": result}
    # Legacy management-token exchange is retained for automation only. The
    # product login screen uses username/password and does not display tokens.
    token = body.get("token")
    if set(body) != {"token"} or not isinstance(token, str) or len(token) > 4096:
        raise ReleaseError("unauthorized", "authentication required", 401)
    session, expires_at = rbac.issue_session(token.strip())
    return {"code": 0, "data": {"accessToken": session, "expiresAt": expires_at}}


def access_codes(rbac: RBAC, authorization: str | None) -> dict[str, Any]:
    _, permissions = rbac.authenticate(authorization)
    return {"code": 0, "data": sorted(permissions & RBAC.PERMISSIONS)}


def user_info(rbac: RBAC, authorization: str | None) -> dict[str, Any]:
    user = rbac.public_user(authorization)
    if user is not None:
        return {"code": 0, "data": {
            **user,
            "userId": user["id"],
            "realName": user["displayName"],
            "roles": [*user["roles"], *(["super"] if "superadmin" in user["roles"] else [])],
            "homePath": "/projects",
            "desc": "项目范围与权限由服务端账号配置授予",
        }}
    actor, permissions = rbac.authenticate(authorization)
    return {"code": 0, "data": {
        "userId": actor,
        "username": actor,
        "realName": "发行管理员" if "superadmin" in permissions else "发行操作员",
        "roles": ["super"] if "superadmin" in permissions else ["release-operator"],
        "permissions": sorted(permissions & RBAC.PERMISSIONS),
        "project_ids": None,
        "homePath": "/release-management",
        "desc": "权限由服务端管理令牌配置授予",
    }}


def logout(rbac: RBAC, authorization: str | None) -> dict[str, Any]:
    rbac.authenticate(authorization)
    if authorization and authorization.startswith("Bearer fuwu-account-v1."):
        rbac.account_store.logout(authorization[7:].strip())
    return {"code": 0, "data": None}

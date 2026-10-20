"""Project registry input contracts, independent of HTTP and authentication."""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Mapping


class ProjectInputError(ValueError):
    """A fixed, safe registry error; submitted values are never echoed."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def validate_project_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", value) or ".." in value or value.endswith((".", "-", "_")):
        raise ProjectInputError("invalid_project", "project identifier is invalid")
    return value


def normalize_repository(value: Any) -> str:
    """Accept owner/repo or an equivalent credential-free GitHub HTTPS URL."""
    if not isinstance(value, str) or value != value.strip() or len(value) > 300:
        raise ProjectInputError("invalid_repository", "repository is invalid")
    if value.startswith("https://"):
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            raise ProjectInputError("invalid_repository", "repository is invalid") from None
        if parsed.netloc.lower() != "github.com" or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ProjectInputError("invalid_repository", "repository is invalid")
        value = parsed.path.removeprefix("/")
    if value.endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", part) or ".." in part or part.endswith(".") for part in parts):
        raise ProjectInputError("invalid_repository", "repository is invalid")
    return value.lower()


def _text(value: Any, *, name: str, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ProjectInputError("invalid_project_request", "project request is invalid")
    value = value.strip()
    if (not value and not allow_empty) or len(value) > max_length or any(ord(char) < 32 and char not in ("\n", "\t") for char in value) or "\x7f" in value:
        raise ProjectInputError("invalid_project_request", "project request is invalid")
    if name == "name" and any(char in value for char in ("\n", "\t")):
        raise ProjectInputError("invalid_project_request", "project request is invalid")
    return value


def project_create_values(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping) or set(request) - {"id", "name", "repository", "description", "public_api", "build_instructions"}:
        raise ProjectInputError("invalid_project_request", "project request is invalid")
    return {
        "id": validate_project_id(request.get("id")),
        "name": _text(request.get("name"), name="name", max_length=120),
        "repository": normalize_repository(request.get("repository")),
        "description": _text(request.get("description", ""), name="description", max_length=2000, allow_empty=True),
        "public_api": _text(request.get("public_api", ""), name="public_api", max_length=12000, allow_empty=True),
        "build_instructions": _text(request.get("build_instructions", ""), name="build_instructions", max_length=12000, allow_empty=True),
    }


def project_update_values(request: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(request, Mapping) or not request or set(request) - {"name", "description", "public_api", "build_instructions", "active"}:
        raise ProjectInputError("invalid_project_request", "project request is invalid")
    result = {}
    if "name" in request:
        result["name"] = _text(request["name"], name="name", max_length=120)
    if "description" in request:
        result["description"] = _text(request["description"], name="description", max_length=2000, allow_empty=True)
    for field in ("public_api", "build_instructions"):
        if field in request:
            result[field] = _text(request[field], name=field, max_length=12000, allow_empty=True)
    if "active" in request:
        if type(request["active"]) is not bool:
            raise ProjectInputError("invalid_project_request", "project request is invalid")
        result["active"] = request["active"]
    return result

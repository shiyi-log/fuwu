"""Release identifiers, manifest contract and small shared helpers."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .errors import ReleaseError
from .projects import ProjectInputError


UTC = dt.timezone.utc
VERSION_RE = re.compile(r"^(0|[1-9])\.(0|[1-9])\.(0|[1-9])$")
REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
CHANNELS = {"stable", "beta"}


def project_value(validator, *args):
    try:
        return validator(*args)
    except ProjectInputError as error:
        raise ReleaseError(error.code, error.message, error.status) from None


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def safe_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_version(value: str) -> str:
    if not isinstance(value, str) or value.startswith("v") or not VERSION_RE.fullmatch(value):
        raise ReleaseError("invalid_version", "version format is invalid", 422)
    return value


def next_version(value: str) -> str:
    """Return the next single-digit SemVer release (0.0.9 -> 0.1.0).

    The release registry intentionally uses one decimal digit per component so
    release ordering remains explicit in the small initial deployment.
    """
    value = validate_version(value)
    major, minor, patch = (int(part) for part in value.split("."))
    if patch < 9:
        patch += 1
    elif minor < 9:
        patch, minor = 0, minor + 1
    elif major < 9:
        patch, minor, major = 0, 0, major + 1
    else:
        raise ReleaseError("version_overflow", "version cannot be incremented", 422)
    return f"{major}.{minor}.{patch}"


def validate_tag(value: str, version: str) -> str:
    if not isinstance(value, str) or value != f"v{version}":
        raise ReleaseError("invalid_tag", "tag must match vMAJOR.MINOR.PATCH", 422)
    return value


def validate_revision(value: str) -> str:
    if not isinstance(value, str) or not REVISION_RE.fullmatch(value):
        raise ReleaseError("invalid_revision", "revision must be a 40 character hexadecimal value", 422)
    return value.lower()


def validate_channel(value: str) -> str:
    if value not in CHANNELS:
        raise ReleaseError("invalid_channel", "channel must be stable or beta", 422)
    return value


def safe_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    # S3 keys and staging paths are deliberately restricted to portable names.
    if any(part.startswith(".") for part in path.parts):
        raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
    return "/".join(path.parts)


def protected_job_path(root: Path, job_ref: str) -> Path:
    if not isinstance(job_ref, str) or not JOB_RE.fullmatch(job_ref):
        raise ReleaseError("invalid_staging_reference", "staging reference is invalid", 422)
    base = root.resolve()
    candidate = (base / job_ref).resolve()
    if candidate.parent != base or candidate == base:
        raise ReleaseError("invalid_staging_reference", "staging reference is invalid", 422)
    return candidate


def _asset_entries(assets: Any) -> list[dict[str, Any]]:
    """Flatten the public ``assets.docker/python`` manifest map."""
    entries: list[dict[str, Any]] = []
    if not isinstance(assets, Mapping):
        raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
    for group, values in assets.items():
        if group not in {"docker", "python"}:
            continue
        if isinstance(values, Mapping):
            values = [values]
        if not isinstance(values, list):
            raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
        for value in values:
            if isinstance(value, str):
                entries.append({"group": group, "path": value})
            elif isinstance(value, Mapping):
                item = dict(value)
                item["group"] = group
                item["path"] = item.get("path") or item.get("name") or item.get("file")
                entries.append(item)
            else:
                raise ReleaseError("invalid_manifest", "manifest assets are invalid", 422)
    if not entries:
        raise ReleaseError("invalid_manifest", "manifest must include docker or python assets", 422)
    for item in entries:
        item["path"] = safe_relative_path(item.get("path"))
    return entries


def validate_manifest(manifest: Mapping[str, Any], requested: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(manifest, Mapping):
        raise ReleaseError("invalid_manifest", "manifest is invalid", 422)
    result = dict(manifest)
    if result.get("schemaVersion") != 1:
        raise ReleaseError("invalid_manifest", "manifest schemaVersion must be 1", 422)
    version = validate_version(result.get("version"))
    revision = validate_revision(result.get("revision"))
    if not result.get("repository") or not isinstance(result.get("repository"), str):
        raise ReleaseError("invalid_manifest", "manifest repository is required", 422)
    validate_tag(result.get("tag"), version)
    release_id = result.get("releaseId")
    valid_release_id = (
        (isinstance(release_id, int) and not isinstance(release_id, bool) and release_id > 0)
        or (isinstance(release_id, str) and bool(release_id.strip()))
    )
    if not valid_release_id:
        raise ReleaseError("invalid_manifest", "manifest releaseId is required", 422)
    if result.get("draft") is not False or result.get("prerelease") is not False:
        raise ReleaseError("invalid_manifest", "manifest must be a final release", 422)
    if requested:
        if requested.get("project") and requested["project"] != result.get("project", requested["project"]):
            # The project is normally supplied by the API and not repeated in v1 manifests.
            raise ReleaseError("manifest_mismatch", "manifest project does not match request", 422)
        if requested.get("version") and validate_version(requested["version"]) != version:
            raise ReleaseError("manifest_mismatch", "manifest version does not match request", 422)
        if requested.get("revision") and validate_revision(requested["revision"]) != revision:
            raise ReleaseError("manifest_mismatch", "manifest revision does not match request", 422)
        if requested.get("tag") and requested["tag"] != result.get("tag"):
            raise ReleaseError("manifest_mismatch", "manifest tag does not match request", 422)
    entries = _asset_entries(result.get("assets"))
    result["version"] = version
    result["revision"] = revision
    result.setdefault("publishedAt", now_iso())
    result.setdefault("verifiedAt", now_iso())
    # The public object-storage v1 contract is consumed by Heyue's anonymous
    # updater, which requires this exact source marker in both manifest and
    # latest pointer documents. Normalize upstream or legacy values here so
    # every publication path emits the same public contract.
    result["verificationSource"] = "heyue-release-storage"
    result.setdefault("checksums", {})
    return result, entries

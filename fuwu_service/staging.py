"""Validation of local staging bundles before publication."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .errors import ReleaseError
from .validation import protected_job_path, safe_digest, safe_relative_path, validate_manifest


def read_staging_bundle(staging_dir: Path, job_ref: str, request: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], Path, bytes]:
    """Validate a staged manifest, SHA256SUMS and every asset it declares."""
    job_dir = protected_job_path(staging_dir, job_ref)
    manifest_path = job_dir / "manifest.json"
    sums_path = job_dir / "SHA256SUMS"
    if not job_dir.is_dir() or not manifest_path.is_file() or not sums_path.is_file():
        raise ReleaseError("staging_not_found", "staging job was not found", 404)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sums_bytes = sums_path.read_bytes()
        sums_text = sums_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("invalid_staging", "staging bundle is invalid", 422)
    manifest, entries = validate_manifest(manifest, request)
    checksums: dict[str, str] = {}
    for line in sums_text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise ReleaseError("invalid_checksums", "SHA256SUMS is invalid", 422)
        checksum_path = safe_relative_path(parts[1].lstrip("*"))
        if checksum_path in checksums:
            raise ReleaseError("invalid_checksums", "SHA256SUMS contains duplicate paths", 422)
        checksums[checksum_path] = parts[0].lower()
    if not checksums:
        raise ReleaseError("invalid_checksums", "SHA256SUMS is empty", 422)
    asset_names = [Path(entry["path"]).name for entry in entries]
    if len(asset_names) != len(set(asset_names)):
        raise ReleaseError("invalid_manifest", "asset names must be unique", 422)
    declared_checksums = manifest.get("checksums", {})
    if not isinstance(declared_checksums, Mapping):
        raise ReleaseError("invalid_checksums", "manifest checksums are invalid", 422)
    if declared_checksums.get("sha256") is not None and declared_checksums["sha256"] != safe_digest(sums_bytes):
        raise ReleaseError("checksum_mismatch", "checksum file digest does not match", 422)
    declared_entries = declared_checksums.get("entries")
    if declared_entries is not None:
        if not isinstance(declared_entries, Mapping):
            raise ReleaseError("invalid_checksums", "manifest checksums are invalid", 422)
        normalized_entries = {str(name): str(digest).lower() for name, digest in declared_entries.items()}
        if normalized_entries != checksums:
            raise ReleaseError("checksum_mismatch", "manifest checksums do not match SHA256SUMS", 422)
    declared_asset_paths = {entry["path"] for entry in entries}
    if set(checksums) != declared_asset_paths:
        raise ReleaseError("invalid_checksums", "SHA256SUMS paths do not match manifest assets", 422)
    for entry in entries:
        path = entry["path"]
        expected = checksums.get(path)
        declared = entry.get("sha256")
        if not expected or not isinstance(declared, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", declared):
            raise ReleaseError("invalid_checksums", "asset checksum is missing", 422)
        if declared.lower() != expected:
            raise ReleaseError("checksum_mismatch", "manifest checksum does not match SHA256SUMS", 422)
        if not isinstance(entry.get("size"), int) or isinstance(entry["size"], bool) or entry["size"] < 0:
            raise ReleaseError("invalid_manifest", "asset size is required", 422)
        source = (job_dir / path).resolve()
        if source.parent != job_dir.resolve() and job_dir.resolve() not in source.parents:
            raise ReleaseError("invalid_asset_path", "asset path is invalid", 422)
        if not source.is_file():
            raise ReleaseError("staging_asset_missing", "staging asset is missing", 422)
        body = source.read_bytes()
        digest = safe_digest(body)
        if digest != expected:
            raise ReleaseError("checksum_mismatch", "asset checksum does not match", 422)
        if len(body) != entry["size"]:
            raise ReleaseError("size_mismatch", "asset size does not match", 422)
        entry["sha256"] = digest
        entry["source"] = source
        entry["checksums"] = checksums
    return manifest, entries, job_dir, sums_bytes

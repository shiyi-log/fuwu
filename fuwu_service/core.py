"""Compatibility facade for the release management domain.

The implementation lives in focused modules:

- ``errors``: ``ReleaseError``
- ``validation``: version, channel, path and manifest contracts
- ``object_store``: in-memory and S3-compatible storage drivers
- ``repository``: SQLite persistence
- ``rbac``: token, account and session authorization
- ``staging``: local staging bundle validation
- ``github_releases``: GitHub release discovery and remote staging
- ``release_service``: publication, rollback and public downloads

Existing imports from ``fuwu_service.core`` keep working. New code should
import from the module that owns the name.
"""

from __future__ import annotations

from .errors import ReleaseError
from .github_releases import (
    _github_asset_url,
    _github_url,
    _GitHubRedirectHandler,
    _read_limited,
    discover_latest_release,
    stage_remote_release,
)
from .object_store import (
    AwsCredentials,
    MemoryObjectStore,
    ObjectStore,
    S3ObjectStore,
    _S3_OPENER,
    _NoRedirectHandler,
    _open_s3_url,
    load_aws_credentials,
)
from .rbac import RBAC
from .release_service import ReleaseService
from .repository import SQLiteRepository
from .staging import read_staging_bundle
from .validation import (
    CHANNELS,
    JOB_RE,
    PROJECT_RE,
    REVISION_RE,
    UTC,
    VERSION_RE,
    _asset_entries,
    json_bytes,
    next_version,
    now_iso,
    project_value,
    protected_job_path,
    safe_digest,
    safe_relative_path,
    validate_channel,
    validate_manifest,
    validate_revision,
    validate_tag,
    validate_version,
)

__all__ = [
    "AwsCredentials",
    "CHANNELS",
    "JOB_RE",
    "MemoryObjectStore",
    "ObjectStore",
    "PROJECT_RE",
    "RBAC",
    "REVISION_RE",
    "ReleaseError",
    "ReleaseService",
    "S3ObjectStore",
    "SQLiteRepository",
    "UTC",
    "VERSION_RE",
    "discover_latest_release",
    "json_bytes",
    "load_aws_credentials",
    "next_version",
    "now_iso",
    "project_value",
    "protected_job_path",
    "read_staging_bundle",
    "safe_digest",
    "safe_relative_path",
    "stage_remote_release",
    "validate_channel",
    "validate_manifest",
    "validate_revision",
    "validate_tag",
    "validate_version",
]

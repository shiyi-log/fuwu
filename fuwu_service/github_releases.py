"""GitHub release discovery and download into protected staging jobs.

Only GitHub HTTPS hosts are contacted, redirects drop the bearer token when
they cross hosts, and every response read is size-bounded.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ReleaseError
from .projects import normalize_repository
from .validation import JOB_RE, project_value, protected_job_path, safe_digest, validate_manifest


def _github_url(value: Any, *, api: bool = False) -> str:
    if not isinstance(value, str):
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422) from None
    allowed_hosts = {"api.github.com"} if api else {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    try:
        port = parsed.port
    except ValueError:
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422) from None
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.password or port not in (None, 443):
        raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
    return value


class _GitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Release asset API requests may redirect within api.github.com before
        # GitHub sends the response to release-assets.githubusercontent.com.
        # Validate both GitHub API and download hosts, and drop the bearer
        # token whenever the redirect crosses hosts.
        try:
            new_host = urllib.parse.urlsplit(newurl).hostname
        except ValueError:
            new_host = None
        _github_url(newurl, api=new_host == "api.github.com")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def _github_asset_url(repository: str, asset: Mapping[str, Any]) -> str:
    """Return the safest URL for a GitHub release asset download.

    GitHub's release metadata includes a numeric asset id and a browser URL.
    The API URL is preferred because it supports authenticated private-release
    downloads with ``Accept: application/octet-stream``. Older or synthetic
    metadata without an id continues to use the validated browser URL.
    """
    asset_id = asset.get("id")
    if asset_id is not None:
        if isinstance(asset_id, bool) or not isinstance(asset_id, int) or asset_id <= 0:
            raise ReleaseError("remote_asset_invalid", "remote release asset URL is invalid", 422)
        return _github_url(f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}", api=True)
    return _github_url(asset.get("browser_download_url"))


def _read_limited(response: Any, maximum: int) -> bytes:
    """Bound metadata/asset reads and reject truncation instead of staging it."""
    try:
        declared_length = response.headers.get("Content-Length")
        if declared_length is not None and int(declared_length) > maximum:
            raise ReleaseError("remote_asset_too_large", "remote release response is too large", 413)
    except (AttributeError, TypeError, ValueError):
        # A malformed or absent length is handled by the bounded read below;
        # it must never turn into an unbounded allocation.
        pass
    body = response.read(maximum + 1)
    if len(body) > maximum:
        raise ReleaseError("remote_asset_too_large", "remote release response is too large", 413)
    return body


def discover_latest_release(repository: str, *, github_token: str | None = None) -> dict[str, Any]:
    """Return the newest final release, its validated manifest and asset map.

    Keys starting with ``_`` carry raw GitHub records for staging and must be
    stripped before the result leaves the service.
    """
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ReleaseError("invalid_repository", "repository is invalid", 422)
    if github_token is not None and (not isinstance(github_token, str) or not github_token.strip() or len(github_token) > 512):
        raise ReleaseError("invalid_github_authorization", "GitHub authorization is invalid", 422)
    repository = repository.strip()
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fuwu-release-storage", "X-GitHub-Api-Version": "2022-11-28"}
    if github_token:
        headers["Authorization"] = "Bearer " + github_token.strip()
    opener = urllib.request.build_opener(_GitHubRedirectHandler())
    list_url = _github_url(f"https://api.github.com/repos/{repository}/releases?per_page=100", api=True)
    try:
        with opener.open(urllib.request.Request(list_url, headers=headers), timeout=30) as response:
            releases = json.loads(_read_limited(response, 8_000_000))
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("remote_release_unavailable", "remote release metadata is unavailable", 502) from None
    if not isinstance(releases, list):
        raise ReleaseError("remote_release_invalid", "remote release metadata is invalid", 422)
    # GitHub documents this collection as newest-first.  Keep the first
    # formal record so a prerelease or draft at the head cannot mask the
    # most recent publishable release.
    release = next(
        (
            item
            for item in releases
            if isinstance(item, Mapping)
            and item.get("draft") is False
            and item.get("prerelease") is False
            and isinstance(item.get("tag_name"), str)
        ),
        None,
    )
    if release is None:
        raise ReleaseError("remote_release_not_found", "no published GitHub release was found", 404)
    tag = release["tag_name"]
    remote_assets = release.get("assets")
    if not isinstance(remote_assets, list):
        raise ReleaseError("remote_release_invalid", "remote release assets are invalid", 422)
    by_name: dict[str, Mapping[str, Any]] = {}
    for item in remote_assets:
        if isinstance(item, Mapping) and isinstance(item.get("name"), str):
            by_name[item["name"]] = item
    manifest_asset = by_name.get("manifest.json")
    if manifest_asset is None:
        raise ReleaseError("remote_manifest_missing", "remote release manifest is missing", 422)
    manifest_url = _github_asset_url(repository, manifest_asset)
    manifest_headers = {**headers, "Accept": "application/octet-stream"}
    try:
        with opener.open(urllib.request.Request(manifest_url, headers=manifest_headers), timeout=30) as response:
            manifest = json.loads(_read_limited(response, 2_000_000))
    except ReleaseError:
        raise
    except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
        raise ReleaseError("remote_manifest_unavailable", "remote release manifest is unavailable", 502) from None
    try:
        manifest, entries = validate_manifest(manifest, {"tag": tag})
    except ReleaseError:
        raise
    manifest_repository = project_value(normalize_repository, manifest.get("repository"))
    if manifest_repository != repository.lower():
        raise ReleaseError("manifest_mismatch", "manifest repository does not match release repository", 422)
    asset_names = [entry["path"] for entry in entries]
    if len(asset_names) != len(set(asset_names)) or any(name not in by_name for name in asset_names):
        raise ReleaseError("remote_asset_missing", "remote release asset is missing", 422)
    checksum_asset = by_name.get("SHA256SUMS")
    if checksum_asset is None:
        raise ReleaseError("remote_checksums_missing", "remote release SHA256SUMS is missing", 422)
    # Validate URLs before returning metadata so callers can safely use the
    # response to start staging without discovering malformed asset records.
    for name in [*asset_names, "SHA256SUMS"]:
        _github_asset_url(repository, by_name[name])
    safe_assets = []
    for name in asset_names:
        item = by_name[name]
        size = item.get("size")
        safe_assets.append({"name": name, "size": size if isinstance(size, int) and size >= 0 else None})
    public_manifest = {
        key: manifest[key]
        for key in (
            "schemaVersion", "project", "repository", "version", "tag", "revision", "releaseId",
            "publishedAt", "draft", "prerelease", "assets", "checksums", "verifiedAt", "verificationSource",
        )
        if key in manifest
    }
    release_id = release.get("id")
    safe_release_id = release_id if isinstance(release_id, int) and not isinstance(release_id, bool) and release_id > 0 else None
    return {
        "repository": repository.lower(),
        "tag": tag,
        "version": manifest["version"],
        "revision": manifest["revision"],
        "releaseId": manifest["releaseId"],
        "githubReleaseId": safe_release_id,
        "publishedAt": release.get("published_at") if isinstance(release.get("published_at"), str) else None,
        "draft": False,
        "prerelease": False,
        "assets": safe_assets,
        "assetNames": asset_names,
        "manifest": public_manifest,
        "_manifest": manifest,
        "_release": release,
        "_assetMap": by_name,
    }


def stage_remote_release(
    staging_dir: Path,
    audit_event: Callable[..., None],
    request: Mapping[str, Any],
    *,
    actor: str,
    request_id: str,
    github_token: str | None = None,
    project_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Download a release's selected assets and SHA256SUMS into a new job.

    The job directory is removed on any failure. Staged bytes still go
    through the normal publish validation; nothing here touches storage.
    """
    allowed = {"repository", "tag", "staging_ref", "assets", "manifest", "latest"}
    if not isinstance(request, Mapping) or set(request) - allowed or not isinstance(request.get("repository"), str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", request["repository"]):
        raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
    latest = request.get("latest", False)
    if not isinstance(latest, bool):
        raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
    tag = request.get("tag")
    job_ref = request.get("staging_ref")
    assets = request.get("assets")
    manifest = request.get("manifest")
    if not isinstance(job_ref, str) or not JOB_RE.fullmatch(job_ref):
        raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
    discovery = None
    if latest:
        if any(key in request for key in ("tag", "assets", "manifest")):
            raise ReleaseError("invalid_remote_stage_request", "latest staging cannot include release details", 422)
        discovery = discover_latest_release(request["repository"], github_token=github_token)
        tag = discovery["tag"]
        assets = discovery["assetNames"]
        manifest = discovery["_manifest"]
    elif not isinstance(tag, str) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise ReleaseError("invalid_remote_stage_request", "remote staging request is invalid", 422)
    if not isinstance(assets, list) or not assets or len(assets) != len(set(assets)) or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}", name) for name in assets):
        raise ReleaseError("invalid_remote_stage_request", "remote asset list is invalid", 422)
    if not isinstance(manifest, Mapping):
        raise ReleaseError("invalid_remote_stage_request", "remote manifest is required", 422)
    manifest, manifest_entries = validate_manifest(manifest, {"tag": tag})
    manifest_repository = project_value(normalize_repository, manifest.get("repository"))
    if manifest_repository != request["repository"].lower():
        raise ReleaseError("manifest_mismatch", "manifest repository does not match request", 422)
    # Remote staging writes each selected asset at the job root.  Require
    # manifest paths to be the same safe filenames so publish cannot later
    # look for an uncreated nested path or silently collapse two names.
    manifest_names = {entry["path"] for entry in manifest_entries}
    if manifest_names != set(assets):
        raise ReleaseError("manifest_mismatch", "remote assets do not match manifest", 422)
    if github_token is not None and (not isinstance(github_token, str) or not github_token.strip() or len(github_token) > 512):
        raise ReleaseError("invalid_github_authorization", "GitHub authorization is invalid", 422)
    job_dir = protected_job_path(staging_dir, job_ref)
    if job_dir.exists():
        raise ReleaseError("staging_conflict", "staging job already exists", 409)
    opener = urllib.request.build_opener(_GitHubRedirectHandler())
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "fuwu-release-storage"}
    release = discovery.get("_release") if discovery else None
    by_name: dict[str, Mapping[str, Any]] | None = discovery.get("_assetMap") if discovery else None
    if not latest:
        api = _github_url(f"https://api.github.com/repos/{request['repository']}/releases/tags/{tag}", api=True)
        req = urllib.request.Request(api, headers=headers)
        if github_token:
            headers["Authorization"] = "Bearer " + github_token.strip()
            req = urllib.request.Request(api, headers=headers)
        try:
            with opener.open(req, timeout=30) as response:
                release = json.loads(_read_limited(response, 2_000_000))
        except (OSError, urllib.error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("remote_release_unavailable", "remote release metadata is unavailable", 502) from None
    else:
        if github_token:
            headers["Authorization"] = "Bearer " + github_token.strip()
    if not isinstance(release, Mapping) or release.get("tag_name") != tag or release.get("draft") or release.get("prerelease"):
        raise ReleaseError("remote_release_invalid", "remote release is not publishable", 422)
    if by_name is None:
        remote_assets = release.get("assets")
        if not isinstance(remote_assets, list):
            raise ReleaseError("remote_release_invalid", "remote release assets are invalid", 422)
        by_name = {}
        for item in remote_assets:
            if isinstance(item, Mapping) and isinstance(item.get("name"), str):
                by_name[item["name"]] = item
    for name in assets:
        if name not in by_name:
            raise ReleaseError("remote_asset_missing", "remote release asset is missing", 422)
        _github_asset_url(request["repository"], by_name[name])
    checksum_name = "SHA256SUMS"
    if checksum_name not in by_name:
        raise ReleaseError("remote_checksums_missing", "remote release SHA256SUMS is missing", 422)
    _github_asset_url(request["repository"], by_name[checksum_name])
    if any(name not in by_name for name in assets):
        raise ReleaseError("remote_asset_missing", "remote release asset is missing", 422)
    job_dir.mkdir(mode=0o700)
    try:
        download_headers = {"Accept": "application/octet-stream", "User-Agent": "fuwu-release-storage"}
        if github_token:
            download_headers["Authorization"] = "Bearer " + github_token.strip()
        for name in assets:
            target = job_dir / name
            download = urllib.request.Request(_github_asset_url(request["repository"], by_name[name]), headers=download_headers)
            with opener.open(download, timeout=120) as response:
                target.write_bytes(_read_limited(response, 1_500_000_000))
            target.chmod(0o600)
        checksum_req = urllib.request.Request(_github_asset_url(request["repository"], by_name[checksum_name]), headers=download_headers)
        with opener.open(checksum_req, timeout=30) as response:
            try:
                upstream_sums = _read_limited(response, 2_000_000).decode("utf-8")
            except UnicodeDecodeError:
                raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS is invalid", 422) from None
        upstream: dict[str, str] = {}
        for line in upstream_sums.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
                name = Path(parts[1].lstrip("*")).name
                if name in upstream:
                    raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS contains duplicate assets", 422)
                upstream[name] = parts[0].lower()
        if any(name not in upstream for name in assets):
            raise ReleaseError("remote_checksums_invalid", "remote SHA256SUMS does not cover selected assets", 422)
        checksums = []
        for name in assets:
            body = (job_dir / name).read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if digest != upstream[name]:
                raise ReleaseError("remote_checksum_mismatch", "remote asset checksum does not match SHA256SUMS", 422)
            declared = next(entry for entry in manifest_entries if Path(entry["path"]).name == name)
            if declared.get("sha256") and declared["sha256"].lower() != digest:
                raise ReleaseError("remote_checksum_mismatch", "remote asset checksum does not match manifest", 422)
            if declared.get("size") is not None and declared["size"] != len(body):
                raise ReleaseError("remote_size_mismatch", "remote asset size does not match manifest", 422)
            checksums.append(f"{digest}  {name}")
        # The downloaded checksum file is normalized to the exact asset
        # set selected for this staging job.  Keep the staged manifest
        # self-consistent with that normalized file; GitHub manifests may
        # describe the upstream checksum file byte-for-byte, while this
        # service deliberately writes its own canonical representation.
        normalized_sums = ("\n".join(checksums) + "\n").encode("utf-8")
        staged_manifest = dict(manifest)
        staged_checksums = dict(manifest.get("checksums", {})) if isinstance(manifest.get("checksums"), Mapping) else {}
        staged_checksums.update({
            "name": "SHA256SUMS",
            "sha256": safe_digest(normalized_sums),
            "entries": {
                parts[1]: parts[0]
                for parts in (line.split(maxsplit=1) for line in checksums)
            },
        })
        staged_manifest["checksums"] = staged_checksums
        manifest_path = job_dir / "manifest.json"
        manifest_path.write_text(json.dumps(staged_manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        manifest_path.chmod(0o600)
        sums_path = job_dir / "SHA256SUMS"
        sums_path.write_bytes(normalized_sums)
        sums_path.chmod(0o600)
        manifest = staged_manifest
    except ReleaseError:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except (OSError, urllib.error.URLError):
        shutil.rmtree(job_dir, ignore_errors=True)
        raise ReleaseError("remote_asset_unavailable", "remote release asset is unavailable", 502) from None
    audit_event("remote_stage", project_id=project_id, version=None, channel=None, actor=actor, request_id=request_id, details={"repository": request["repository"], "tag": tag, "staging_ref": job_ref, "assets": assets})
    release_summary = {
        "repository": request["repository"].lower(),
        "tag": tag,
        "version": manifest["version"],
        "revision": manifest["revision"],
        "releaseId": manifest["releaseId"],
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }
    if discovery:
        release_summary.update({key: value for key, value in discovery.items() if key in {"githubReleaseId", "publishedAt"}})
    return 201, {"status": "staged", "job": {"id": request_id, "status": "staged", "progress": 100}, "staging_ref": job_ref, "release": release_summary}

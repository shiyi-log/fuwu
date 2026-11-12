"""Publication, rollback and anonymous download resolution for releases."""

from __future__ import annotations

import json
import mimetypes
import threading
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping

from .errors import ReleaseError
from .github_releases import discover_latest_release, stage_remote_release
from .object_store import ObjectStore, S3ObjectStore
from .projects import normalize_repository, validate_project_id
from .repository import SQLiteRepository
from .staging import read_staging_bundle
from .validation import (
    CHANNELS,
    _asset_entries,
    json_bytes,
    now_iso,
    project_value,
    safe_digest,
    safe_relative_path,
    validate_channel,
    validate_version,
)


class ReleaseService:
    def __init__(self, repository: SQLiteRepository, store: ObjectStore, *, staging_dir: str | Path = "staging", prefix: str = "releases"):
        self.repository = repository
        self.store = store
        self.staging_dir = Path(staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.prefix = prefix.strip("/")
        self._mutation_lock = threading.RLock()

    def create_project(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        """Register metadata; HTTP adapters enforce projects.manage."""
        with self._mutation_lock, self.repository.atomic():
            project = self.repository.create_project(request)
            self.repository.audit_event("project_created", project_id=project["id"], version=None, channel=None, actor=actor, request_id=request_id, details={"status": "created", "repository": project["repository"]})
            return 201, project

    def public_download(self, project: str, version: str, name: str) -> dict[str, Any]:
        """Resolve a published stable asset for the anonymous Fuwu route."""
        project = project_value(validate_project_id, project)
        version = validate_version(version)
        name = safe_relative_path(name)
        if "/" in name:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        release = self.repository.get_release(project, version, "stable")
        if not release or release.get("status") != "published":
            raise ReleaseError("object_not_found", "download object was not found", 404)
        if name == "manifest.json":
            body = json_bytes(release["manifest"])
            return {"name": name, "key": release["manifest_key"], "size": len(body), "sha256": safe_digest(body), "body": body}
        artifacts = {item["name"]: item for item in self.repository.list_artifacts(release["id"])}
        artifact = artifacts.get(name)
        if not artifact:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        return {"name": name, "key": artifact["object_key"], "size": artifact["size"], "sha256": artifact["sha256"]}

    def public_storage_download(self, key: str) -> tuple[dict[str, Any], bool]:
        """Resolve an anonymous object-storage key through published records.

        Heyue configures a project prefix such as ``releases/heyue`` and
        appends the manifest keys returned by ``latest.json``.  Keep this
        compatibility surface strictly mapped to the same published whitelist
        as ``/api/downloads``; never proxy an arbitrary object-store key.
        """
        try:
            normalized = safe_relative_path(key)
        except ReleaseError:
            raise ReleaseError("object_not_found", "download object was not found", 404) from None
        prefix_parts = self.prefix.split("/")
        parts = normalized.split("/")
        if not prefix_parts or any(not part for part in prefix_parts) or parts[:len(prefix_parts)] != prefix_parts:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        relative = parts[len(prefix_parts):]
        if len(relative) != 3 or not relative[0] or not relative[1] or not relative[2]:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        project, version_or_channel, name = relative
        if version_or_channel in CHANNELS and name == "latest.json":
            try:
                return self.public_latest_download(project, version_or_channel), True
            except ReleaseError as error:
                if error.code in {"invalid_project", "invalid_channel", "storage_verification_failed"}:
                    raise ReleaseError("object_not_found", "download object was not found", 404) from None
                raise
        if not version_or_channel.startswith("v"):
            raise ReleaseError("object_not_found", "download object was not found", 404)
        version = version_or_channel[1:]
        try:
            return self.public_download(project, version, name), False
        except ReleaseError as error:
            if error.code in {"invalid_project", "invalid_version", "invalid_asset_path"}:
                raise ReleaseError("object_not_found", "download object was not found", 404) from None
            raise

    def public_latest_download(self, project: str, channel: str) -> dict[str, Any]:
        """按数据库发行绑定解析当前 stable 指针，并返回存储中的原始字节。

        客户端只能提供项目和渠道；指针键及不可变 manifest 键均由数据库记录
        推导，并在返回前校验两个存储对象。这样匿名接口不会变成任意对象代理。
        """
        project = project_value(validate_project_id, project)
        channel = validate_channel(channel)
        if channel != "stable":
            raise ReleaseError("object_not_found", "download object was not found", 404)
        registered = self.repository.get_project(project)
        if not registered or not registered["active"]:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        latest = self.repository.get_latest(project, channel)
        if not latest:
            raise ReleaseError("object_not_found", "download object was not found", 404)
        try:
            version = validate_version(latest["version"])
            _, manifest_key, latest_key = self._keys(project, version, channel)
        except (ReleaseError, KeyError, TypeError):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502) from None
        if latest.get("pointer_key") != latest_key:
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        release = self.repository.get_release_by_id(str(latest.get("release_pk", "")))
        if (
            not release
            or release.get("project_id") != project
            or release.get("channel") != channel
            or release.get("version") != version
            or release.get("status") != "published"
            or release.get("digest") != latest.get("digest")
            or release.get("manifest_key") != manifest_key
        ):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)

        try:
            manifest_head = self.store.head_object(manifest_key)
            manifest_body = self.store.get_object(manifest_key)
            if manifest_head.get("sha256") != safe_digest(manifest_body) or int(manifest_head.get("size", -1)) != len(manifest_body):
                raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
            pointer_head = self.store.head_object(latest_key)
            pointer_body = self.store.get_object(latest_key)
            if pointer_head.get("sha256") != safe_digest(pointer_body) or int(pointer_head.get("size", -1)) != len(pointer_body):
                raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
            pointer = json.loads(pointer_body)
        except ReleaseError as error:
            if error.code == "object_not_found":
                raise ReleaseError("object_not_found", "download object was not found", 404) from None
            raise
        except (TypeError, ValueError, UnicodeDecodeError):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502) from None

        if not isinstance(pointer, Mapping):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        manifest_digest = safe_digest(manifest_body)
        if manifest_body != json_bytes(release["manifest"]):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        manifest = release["manifest"]
        if (
            not isinstance(manifest, Mapping)
            or manifest.get("project") != project
            or manifest.get("repository") != registered["repository"]
            or manifest.get("version") != version
            or manifest.get("tag") != release["tag"]
            or manifest.get("revision") != release["revision"]
            or str(manifest.get("releaseId")) != str(release["release_id"])
        ):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        expected_pointer = {
            "schemaVersion": 1,
            "project": project,
            "channel": channel,
            "version": release["version"],
            "tag": release["tag"],
            "revision": release["revision"],
            "releaseId": manifest["releaseId"],
            "manifestKey": manifest_key,
            "manifestSha256": manifest_digest,
            "publishedAt": release["published_at"],
            "immutable": True,
            "verificationSource": manifest.get("verificationSource"),
        }
        if json_bytes(pointer) != json_bytes(expected_pointer):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        return {
            "name": "latest.json",
            "key": latest_key,
            "size": len(pointer_body),
            "sha256": safe_digest(pointer_body),
            "body": pointer_body,
        }

    def open_public_download(self, descriptor: Mapping[str, Any]):
        """Open a published asset for streaming without exposing storage credentials."""
        body = descriptor.get("body")
        if isinstance(body, bytes):
            return BytesIO(body)
        if isinstance(self.store, S3ObjectStore):
            return self.store.open_object(str(descriptor["key"]))
        return BytesIO(self.store.get_object(str(descriptor["key"])))

    def update_project(self, project_id: str, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        """Change display metadata/active only; repository binding is immutable."""
        with self._mutation_lock, self.repository.atomic():
            project = self.repository.update_project(project_id, request)
            self.repository.audit_event("project_updated", project_id=project["id"], version=None, channel=None, actor=actor, request_id=request_id, details={"status": "updated", "changed_fields": sorted(request), "active": project["active"]})
            return 200, project

    @staticmethod
    def _idempotency_scope(operation: str, request: Mapping[str, Any], *, project: str, channel: str, actor: str) -> dict[str, str]:
        normalized = {key: value for key, value in request.items() if key != "requestId"}
        normalized["channel"] = channel
        if operation == "publish":
            normalized["staging_ref"] = request.get("staging_ref") or request.get("job_ref")
            normalized.pop("job_ref", None)
        return {"operation": operation, "project_id": project, "channel": channel, "actor": actor, "fingerprint": safe_digest(json_bytes(normalized))}

    def _keys(self, project: str, version: str, channel: str) -> tuple[str, str, str]:
        # Immutable objects use releases/{project}/vX.Y.Z paths.
        immutable = f"{self.prefix}/{project}/v{version}"
        manifest_key = f"{immutable}/manifest.json"
        latest_key = f"{self.prefix}/{project}/{channel}/latest.json"
        return immutable, manifest_key, latest_key

    def _put_immutable(self, key: str, body: bytes, *, content_type: str, sha256: str) -> dict[str, Any]:
        """Write an immutable object only when the key is absent or identical."""
        if safe_digest(body) != sha256:
            raise ReleaseError("checksum_mismatch", "asset checksum does not match", 422)
        try:
            self.store.put_object(key, body, content_type=content_type, sha256=sha256, if_none_match=True)
        except ReleaseError as error:
            if error.code != "object_precondition_failed":
                raise
            existing = self.store.head_object(key)
            if existing.get("sha256") != sha256 or int(existing.get("size", -1)) != len(body):
                raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
            return existing
        return self.store.head_object(key)

    def _put_release_manifest(self, key: str, candidate: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
        """Reuse the original immutable bytes when only the target channel differs.

        Older manifests retain their source channel. Conditional creation and
        full HEAD/GET verification prevent a promotion from overwriting them.
        """
        candidate_body = json_bytes(candidate)
        try:
            self.store.put_object(key, candidate_body, content_type="application/json", sha256=safe_digest(candidate_body), if_none_match=True)
        except ReleaseError as error:
            if error.code != "object_precondition_failed":
                raise
        head = self.store.head_object(key)
        body = self.store.get_object(key)
        if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
            raise ReleaseError("storage_verification_failed", "immutable manifest verification failed", 502)
        try:
            stored = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409) from None
        expected = {name: value for name, value in candidate.items() if name != "channel"}
        actual = {name: value for name, value in stored.items() if name != "channel"} if isinstance(stored, dict) else None
        if json_bytes(actual) != json_bytes(expected):
            raise ReleaseError("immutable_object_conflict", "immutable object already exists with different content", 409)
        return stored, body

    @staticmethod
    def _latest_pointer(manifest: Mapping[str, Any], manifest_key: str, manifest_digest: str, *, channel: str) -> dict[str, Any]:
        """Build a v1 latest pointer without duplicating manifest fields."""
        return {
            "schemaVersion": 1,
            "project": manifest["project"],
            "channel": channel,
            "version": manifest["version"],
            "tag": manifest["tag"],
            "revision": manifest["revision"],
            "releaseId": manifest["releaseId"],
            "manifestKey": manifest_key,
            "manifestSha256": manifest_digest,
            "publishedAt": manifest["publishedAt"],
            "immutable": True,
            "verificationSource": manifest["verificationSource"],
        }

    def _pointer_snapshot(self, key: str) -> dict[str, Any] | None:
        try:
            head = self.store.head_object(key)
        except ReleaseError as error:
            if error.code == "object_not_found":
                return None
            raise
        body = self.store.get_object(key)
        if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
            raise ReleaseError("storage_verification_failed", "latest pointer verification failed", 502)
        if not head.get("etag"):
            raise ReleaseError("storage_precondition_unavailable", "storage conditional writes are unavailable", 502)
        return {**head, "body": body}

    def _restore_pointer(self, key: str, before: dict[str, Any] | None, body: bytes, written_etag: str | None) -> str:
        """Compensate this write using its ETag; never clobber another writer."""
        if not written_etag:
            current = self._pointer_snapshot(key)
            if (current is None and before is None) or (current and before and current["body"] == before["body"]):
                return "unchanged"
            if current is None or current["body"] != body:
                raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
            written_etag = current["etag"]
        if before is None:
            self.store.delete_object(key, if_match=written_etag)
            try:
                self.store.head_object(key)
            except ReleaseError as error:
                if error.code == "object_not_found":
                    return "removed"
                raise
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        restored = self.store.put_object(key, before["body"], content_type="application/json", sha256=before["sha256"], cache_control="no-store, max-age=0", if_match=written_etag)
        head = self.store.head_object(key)
        if head.get("sha256") != before["sha256"] or head.get("size") != len(before["body"]):
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        if restored.get("etag") and head.get("etag") != restored["etag"]:
            raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503)
        return "restored"

    def _finalize_pointer(self, key: str, body: bytes, finalize, *, operation: str, project: str, version: str, channel: str, actor: str, request_id: str) -> None:
        """Local DB transaction plus conditional S3 write and compensation.

        S3 and SQLite cannot share a transaction. A process crash or persistent
        storage outage can still require operator reconciliation; never report
        such an outcome as a successful publication.
        """
        before = self._pointer_snapshot(key)
        written_etag = None
        attempted = False
        try:
            with self.repository.atomic():
                attempted = True
                result = self.store.put_object(key, body, content_type="application/json", sha256=safe_digest(body), cache_control="no-store, max-age=0", if_none_match=before is None, if_match=before["etag"] if before else None)
                written_etag = result.get("etag")
                head = self.store.head_object(key)
                if head.get("sha256") != safe_digest(body) or head.get("size") != len(body):
                    raise ReleaseError("storage_verification_failed", "uploaded latest pointer verification failed", 502)
                if written_etag and head.get("etag") != written_etag:
                    raise ReleaseError("object_precondition_failed", "storage object changed concurrently", 409)
                finalize()
        except Exception as error:
            if isinstance(error, ReleaseError) and error.code == "object_precondition_failed" and written_etag is None:
                # A rejected conditional PUT did not write any pointer bytes.
                attempted = False
            if attempted:
                recovery = "failed"
                try:
                    recovery = self._restore_pointer(key, before, body, written_etag)
                except Exception:
                    pass
                self.repository.audit_event("pointer_recovery", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": recovery, "operation": operation, "pointer_key": key, "before_digest": before["sha256"] if before else None, "attempted_digest": safe_digest(body)})
                if recovery == "failed":
                    raise ReleaseError("pointer_reconciliation_required", "latest pointer requires reconciliation", 503) from None
            if isinstance(error, ReleaseError):
                raise
            raise ReleaseError("publication_finalize_failed", "release finalization failed", 503) from None


    def publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        with self._mutation_lock:
            return self._publish(request, actor=actor, request_id=request_id)

    def stage_remote_release(self, request: Mapping[str, Any], *, actor: str, request_id: str,
                             github_token: str | None = None, project_id: str | None = None) -> tuple[int, dict[str, Any]]:
        """Download a GitHub release into a protected staging job.

        Remote bytes are always put through the existing ``publish`` validation
        path.  The optional token is supplied by a server-side authorization
        store; it is intentionally not accepted as part of ``request``.
        """
        with self._mutation_lock:
            return self._stage_remote_release(request, actor=actor, request_id=request_id,
                                              github_token=github_token, project_id=project_id)

    def discover_latest_github_release(self, repository: str, *, github_token: str | None = None) -> dict[str, Any]:
        """Return the latest published GitHub release and its validated manifest.

        GitHub's release listing is ordered newest-first.  We deliberately
        filter draft and prerelease records before selecting the first item,
        then fetch and validate ``manifest.json`` through the same authenticated
        asset path used by remote staging.  The response contains only release
        metadata and asset names; no GitHub URLs or credentials are returned.
        """
        result = self._discover_latest_github_release(repository, github_token=github_token)
        return {key: value for key, value in result.items() if not key.startswith("_")}

    def _discover_latest_github_release(self, repository: str, *, github_token: str | None = None) -> dict[str, Any]:
        return discover_latest_release(repository, github_token=github_token)

    def _stage_remote_release(self, request: Mapping[str, Any], *, actor: str, request_id: str,
                              github_token: str | None = None, project_id: str | None = None) -> tuple[int, dict[str, Any]]:
        return stage_remote_release(self.staging_dir, self.repository.audit_event, request, actor=actor,
                                    request_id=request_id, github_token=github_token, project_id=project_id)

    def sync_latest_github_release(
        self,
        project_id: str,
        repository: str,
        channel: str,
        *,
        actor: str,
        request_id: str,
        github_token: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Publish a newer formal GitHub release through protected staging."""
        project_id = project_value(validate_project_id, project_id)
        repository = project_value(normalize_repository, repository)
        channel = validate_channel(channel)
        registered = self.repository.get_project(project_id)
        if registered is None:
            raise ReleaseError("project_not_found", "project was not found", 404)
        if not registered["active"]:
            raise ReleaseError("project_inactive", "project is inactive", 403)
        if registered["repository"].lower() != repository:
            raise ReleaseError("project_repository_mismatch", "repository does not match registered project", 409)
        with self._mutation_lock:
            discovered = self._discover_latest_github_release(repository, github_token=github_token)
            latest = {key: value for key, value in discovered.items() if not key.startswith("_")}
            latest["project"] = project_id
            version = discovered["version"]
            version_key = tuple(int(part) for part in version.split("."))

            pointer = self.repository.get_latest(project_id, channel)
            if pointer and tuple(int(part) for part in pointer["version"].split(".")) > version_key:
                return 200, {"status": "current_newer", "latest": latest, "current": {"version": pointer["version"]}}

            existing = self.repository.get_release(project_id, version, channel)
            if existing:
                remote_entries = _asset_entries(discovered["_manifest"].get("assets"))
                published_entries = _asset_entries(existing["manifest"].get("assets"))

                def signature(entries: list[dict[str, Any]]) -> list[tuple[str, Any, str]]:
                    return sorted(
                        (entry["path"], entry.get("size"), str(entry.get("sha256", "")).lower())
                        for entry in entries
                    )

                same_release = (
                    existing["repository"].lower() == repository
                    and str(existing["release_id"]) == str(discovered["releaseId"])
                    and existing["tag"] == discovered["tag"]
                    and existing["revision"] == discovered["revision"]
                    and signature(remote_entries) == signature(published_entries)
                )
                if not same_release:
                    raise ReleaseError("release_conflict", "same version already has different release content", 409)
                return 200, {"status": "already_published", "latest": latest, "release": self._public_release(existing)}

            staging_ref = "sync-" + uuid.uuid4().hex
            self._stage_remote_release(
                {
                    "repository": repository,
                    "tag": discovered["tag"],
                    "staging_ref": staging_ref,
                    "assets": discovered["assetNames"],
                    "manifest": discovered["_manifest"],
                },
                actor=actor,
                request_id=request_id + "-stage",
                github_token=github_token,
                project_id=project_id,
            )
            _, published = self._publish(
                {
                    "project": project_id,
                    "channel": channel,
                    "version": version,
                    "tag": discovered["tag"],
                    "revision": discovered["revision"],
                    "staging_ref": staging_ref,
                },
                actor=actor,
                request_id=request_id,
            )
            return 201, {"status": "published", "latest": latest, "release": published["release"]}


    def _publish(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        allowed = {"project", "channel", "version", "tag", "revision", "staging_ref", "job_ref", "requestId"}
        if not isinstance(request, Mapping) or set(request) - allowed:
            raise ReleaseError("invalid_publish_request", "publish request is invalid", 422)
        project = project_value(validate_project_id, request.get("project"))
        version = validate_version(request.get("version"))
        channel = validate_channel(request.get("channel", "stable"))
        job_ref = request.get("staging_ref") or request.get("job_ref")
        if not isinstance(job_ref, str):
            raise ReleaseError("invalid_staging_reference", "staging reference is required", 422)
        registered = self.repository.get_project(project)
        if registered and not registered["active"]:
            raise ReleaseError("project_inactive", "project is inactive", 403)
        scope = self._idempotency_scope("publish", request, project=project, channel=channel, actor=actor)
        cached = self.repository.idempotency_get(request_id, **scope)
        if cached:
            _, response = cached
            return 200, response
        try:
            manifest, entries, job_dir, sums_bytes = read_staging_bundle(self.staging_dir, job_ref, {**request, "version": version, "project": project})
            self.repository.ensure_project(project, repository=manifest["repository"])
        except ReleaseError:
            self.repository.audit_event(
                "validation_failed",
                project_id=project,
                version=version,
                channel=channel,
                actor=actor,
                request_id=request_id,
                details={"jobId": request_id, "status": "failed", "progress": 0},
            )
            raise
        self.repository.audit_event(
            "validation",
            project_id=project,
            version=version,
            channel=channel,
            actor=actor,
            request_id=request_id,
            details={"jobId": request_id, "status": "validated", "progress": 100},
        )
        release_digest = safe_digest(json_bytes(manifest) + sums_bytes)
        existing = self.repository.get_release(project, version, channel)
        if existing:
            if existing["digest"] != release_digest:
                self.repository.audit_event("publish_failed", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "reason": "release_conflict", "digest": release_digest})
                raise ReleaseError("release_conflict", "same version already has a different digest", 409)
            response = {"status": "idempotent", "release": self._public_release(existing)}
            self.repository.idempotency_put(request_id, "publish", 200, response, **{key: value for key, value in scope.items() if key != "operation"})
            return 200, response
        immutable, manifest_key, latest_key = self._keys(project, version, channel)
        sums_key = f"{immutable}/SHA256SUMS"
        artifact_rows: list[dict[str, Any]] = []
        try:
            # Upload each asset under its immutable prefix and immediately verify a HEAD digest.
            for entry in entries:
                object_key = f"{immutable}/{entry['path']}"
                body = entry["source"].read_bytes()
                head = self._put_immutable(object_key, body, content_type=mimetypes.guess_type(entry["path"])[0] or "application/octet-stream", sha256=entry["sha256"])
                if head.get("sha256") != entry["sha256"] or int(head.get("size", -1)) != entry["size"]:
                    raise ReleaseError("storage_verification_failed", "uploaded asset verification failed", 502)
                artifact_rows.append({"release_id": "", "name": entry["path"], "object_key": object_key, "size": entry["size"], "sha256": entry["sha256"]})
            sums_digest = safe_digest(sums_bytes)
            sums_head = self._put_immutable(sums_key, sums_bytes, content_type="text/plain; charset=utf-8", sha256=sums_digest)
            if sums_head.get("sha256") != sums_digest or int(sums_head.get("size", -1)) != len(sums_bytes):
                raise ReleaseError("storage_verification_failed", "uploaded checksum verification failed", 502)
            artifact_rows.append({"release_id": "", "name": "SHA256SUMS", "object_key": sums_key, "size": len(sums_bytes), "sha256": sums_digest})
            published = {
                key: manifest[key]
                for key in (
                    "schemaVersion",
                    "project",
                    "repository",
                    "version",
                    "tag",
                    "revision",
                    "releaseId",
                    "publishedAt",
                    "draft",
                    "prerelease",
                    "verifiedAt",
                    "verificationSource",
                )
                if key in manifest
            }
            published["project"] = project
            published["channel"] = channel
            published["checksums"] = {
                "name": "SHA256SUMS",
                "key": sums_key,
                "sha256": sums_digest,
                "entries": {Path(entry["path"]).name: entry["sha256"] for entry in entries},
            }
            published["assets"] = {}
            for group in ("docker", "python"):
                grouped = [
                    {"name": Path(entry["path"]).name, "key": f"{immutable}/{entry['path']}", "size": entry["size"], "sha256": entry["sha256"]}
                    for entry in entries if entry["group"] == group
                ]
                if grouped:
                    published["assets"][group] = grouped[0] if len(grouped) == 1 else grouped
            published, manifest_body = self._put_release_manifest(manifest_key, published)
            release_pk = uuid.uuid4().hex
            for row in artifact_rows:
                row["release_id"] = release_pk
            # The latest pointer is only written after all immutable objects are verified.
            pointer_body = json_bytes(self._latest_pointer(published, manifest_key, safe_digest(manifest_body), channel=channel))
            release = {"id": release_pk, "project_id": project, "version": version, "channel": channel, "repository": published["repository"], "tag": published["tag"], "revision": published["revision"], "release_id": published["releaseId"], "published_at": published.get("publishedAt") or now_iso(), "digest": release_digest, "manifest_key": manifest_key, "status": "published", "manifest_json": json.dumps(published, sort_keys=True)}
            response = {"status": "published", "job": {"id": request_id, "operation": "publish", "status": "published", "progress": 100}, "release": self._public_release({**release, "manifest": published})}
            def finalize():
                self.repository.save_release(release, artifact_rows)
                self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": release_pk, "version": version, "digest": release_digest, "pointer_key": latest_key, "updated_at": now_iso()})
                self.repository.audit_event("publish", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "published", "progress": 100, "immutable_prefix": immutable, "manifest_key": manifest_key, "latest_key": latest_key, "digest": release_digest, "artifacts": [{"key": row["object_key"], "size": row["size"], "sha256": row["sha256"]} for row in artifact_rows]})
                self.repository.idempotency_put(request_id, "publish", 201, response, **{key: value for key, value in scope.items() if key != "operation"})
            self._finalize_pointer(latest_key, pointer_body, finalize, operation="publish", project=project, version=version, channel=channel, actor=actor, request_id=request_id)
            return 201, response
        except ReleaseError:
            self.repository.audit_event("publish_failed", project_id=project, version=version, channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "digest": release_digest})
            raise

    def rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        with self._mutation_lock:
            return self._rollback(request, actor=actor, request_id=request_id)

    def _rollback(self, request: Mapping[str, Any], *, actor: str, request_id: str) -> tuple[int, dict[str, Any]]:
        allowed = {"project", "channel", "release_id", "version", "confirm", "reason", "requestId"}
        if not isinstance(request, Mapping) or set(request) - allowed:
            raise ReleaseError("invalid_rollback_request", "rollback request is invalid", 422)
        project = project_value(validate_project_id, request.get("project"))
        channel = validate_channel(request.get("channel", "stable"))
        scope = self._idempotency_scope("rollback", request, project=project, channel=channel, actor=actor)
        cached = self.repository.idempotency_get(request_id, **scope)
        if cached:
            _, response = cached
            return 200, response
        if request.get("confirm") is not True:
            raise ReleaseError("confirmation_required", "rollback confirmation is required", 400)
        reason = str(request.get("reason") or "").strip()
        if not reason:
            raise ReleaseError("invalid_reason", "rollback reason is required", 422)
        target = None
        if request.get("release_id"):
            target = self.repository.get_release_by_id(str(request["release_id"]))
        elif request.get("version"):
            target = self.repository.get_release(project, validate_version(request["version"]), channel)
        if not target or target["project_id"] != project or target["channel"] != channel:
            raise ReleaseError("release_not_found", "target release was not found", 404)
        before = self.repository.get_latest(project, channel)
        manifest_key = target["manifest_key"]
        manifest_head = self.store.head_object(manifest_key)
        manifest_body = self.store.get_object(manifest_key)
        if manifest_head.get("sha256") != safe_digest(manifest_body):
            raise ReleaseError("storage_verification_failed", "immutable manifest verification failed", 502)
        manifest = json.loads(manifest_body)
        pointer_key = self._keys(project, target["version"], channel)[2]
        pointer = json_bytes(self._latest_pointer(manifest, manifest_key, safe_digest(manifest_body), channel=channel))
        response = {"status": "rolled_back", "job": {"id": request_id, "operation": "rollback", "status": "rolled_back", "progress": 100}, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"], "channel": channel}}
        def finalize():
            self.repository.set_latest({"project_id": project, "channel": channel, "release_pk": target["id"], "version": target["version"], "digest": target["digest"], "pointer_key": pointer_key, "updated_at": now_iso()})
            self.repository.audit_event("rollback", project_id=project, version=target["version"], channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "rolled_back", "progress": 100, "before": {"version": before["version"], "digest": before["digest"]} if before else None, "after": {"version": target["version"], "digest": target["digest"]}, "reason": reason[:500], "pointer_key": pointer_key})
            self.repository.idempotency_put(request_id, "rollback", 200, response, **{key: value for key, value in scope.items() if key != "operation"})
        try:
            self._finalize_pointer(pointer_key, pointer, finalize, operation="rollback", project=project, version=target["version"], channel=channel, actor=actor, request_id=request_id)
        except ReleaseError:
            self.repository.audit_event("rollback_failed", project_id=project, version=target["version"], channel=channel, actor=actor, request_id=request_id, details={"jobId": request_id, "status": "failed", "progress": 0, "before": response["before"], "after": response["after"], "pointer_key": pointer_key})
            raise
        return 200, response

    def status(self, project_id: str | None = None, channel: str | None = None) -> dict[str, Any]:
        if project_id is not None:
            project_id = project_value(validate_project_id, project_id)
        if channel is not None:
            channel = validate_channel(channel)
        projects = self.repository.list_projects([project_id] if project_id is not None else None)
        releases = self.repository.list_releases(project_id, channel)
        if project_id is None and channel is not None:
            release_project_ids = {release["project_id"] for release in releases}
            projects = [project for project in projects if project["id"] in release_project_ids]
        return {
            "provider": type(self.store).__name__,
            "prefix": self.prefix,
            "project": project_id,
            "channel": channel,
            "projects": len(projects),
            "releases": len(releases),
            "jobs": {
                "validation": self._job_summary("validation", project_id, channel),
                "publish": self._job_summary("publish", project_id, channel),
                "rollback": self._job_summary("rollback", project_id, channel),
            },
        }

    def releases(self, project_id: str | None = None, channel: str | None = None) -> list[dict[str, Any]]:
        if project_id is not None:
            project_id = project_value(validate_project_id, project_id)
        if channel is not None:
            channel = validate_channel(channel)
        result = []
        for release in self.repository.list_releases(project_id, channel):
            latest = self.repository.get_latest(release["project_id"], release["channel"])
            artifacts = self.repository.list_artifacts(release["id"])
            head_status = "verified"
            try:
                self.store.head_object(release["manifest_key"])
                for artifact in artifacts:
                    self.store.head_object(artifact["object_key"])
            except ReleaseError:
                head_status = "unverified"
            result.append({**self._public_release(release), "latest": bool(latest and latest["release_pk"] == release["id"]), "objectHeadStatus": head_status, "artifacts": [{"name": artifact["name"], "key": artifact["object_key"], "size": artifact["size"], "sha256": artifact["sha256"]} for artifact in artifacts]})
        return result

    def _job_summary(self, event_type: str, project_id: str | None = None, channel: str | None = None) -> dict[str, Any]:
        events = self.repository.list_audit(project_id, limit=500, channel=channel)
        matching = [event for event in events if event["event_type"] == event_type or event["event_type"] == f"{event_type}_failed"]
        latest = matching[0] if matching else None
        return {"status": (latest["details"].get("status") if latest else "idle"), "progress": (latest["details"].get("progress", 0) if latest else 0), "jobId": (latest["details"].get("jobId") if latest else None), "requestId": (latest["request_id"] if latest else None), "updatedAt": (latest["created_at"] if latest else None)}

    @staticmethod
    def _public_release(value: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value[key] for key in ("id", "project_id", "version", "channel", "repository", "tag", "revision", "release_id", "published_at", "digest", "manifest_key", "status") if key in value}

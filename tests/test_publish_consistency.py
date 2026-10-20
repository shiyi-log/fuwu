"""Focused offline fault injection for publication's final consistency boundary."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fuwu_service.core import (
    AwsCredentials, MemoryObjectStore, ReleaseError, ReleaseService,
    S3ObjectStore, SQLiteRepository, json_bytes, load_aws_credentials, safe_digest,
)


class FaultStore(MemoryObjectStore):
    def __init__(self):
        super().__init__()
        self.fail_pointer_head = False
        self.fail_next_head = False
        self.block_recovery = False
        self.block_body = None

    def put_object(self, key, body, **kwargs):
        if self.block_recovery and body == self.block_body:
            raise ReleaseError("storage_unavailable", "storage service is unavailable", 503)
        result = super().put_object(key, body, **kwargs)
        if key.endswith("/latest.json") and self.fail_pointer_head:
            self.fail_pointer_head = False
            self.fail_next_head = True
        return result

    def head_object(self, key, **kwargs):
        if key.endswith("/latest.json") and self.fail_next_head:
            self.fail_next_head = False
            raise ReleaseError("storage_unavailable", "storage service is unavailable", 503)
        return super().head_object(key, **kwargs)


class FaultRepository(SQLiteRepository):
    def __init__(self):
        super().__init__()
        self.fail_latest = False

    def set_latest(self, value):
        super().set_latest(value)
        if self.fail_latest:
            self.fail_latest = False
            raise RuntimeError("private database connection information")


class PublishConsistencyTests(unittest.TestCase):
    pointer_key = "releases/fuwu/stable/latest.json"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = FaultRepository()
        self.store = FaultStore()
        self.service = ReleaseService(self.repository, self.store, staging_dir=self.root)

    def tearDown(self):
        self.repository.conn.close()
        self.directory.cleanup()

    def job(self, version="0.0.1", *, project="fuwu"):
        job = self.root / (project + "-" + version)
        job.mkdir(exist_ok=True)
        body = version.encode()
        digest = safe_digest(body)
        manifest = {
            "schemaVersion": 1, "repository": "example-org/" + project,
            "version": version, "tag": "v" + version, "revision": "a" * 40,
            "releaseId": "release-" + version, "publishedAt": "2026-10-20T00:00:00Z",
            "verifiedAt": "2026-10-20T00:00:00Z", "draft": False, "prerelease": False,
            "assets": {"python": {"path": "asset.tgz", "size": len(body), "sha256": digest}},
        }
        (job / "manifest.json").write_bytes(json_bytes(manifest))
        (job / "SHA256SUMS").write_text(digest + "  asset.tgz\n")
        (job / "asset.tgz").write_bytes(body)
        return job, manifest

    def publish(self, version="0.0.1", *, project="fuwu"):
        job, _ = self.job(version, project=project)
        return self.service.publish({"project": project, "channel": "stable", "version": version, "staging_ref": job.name}, actor="acceptance", request_id="publish-" + version)

    def test_publish_rejects_paths_credentials_and_extra_fields_before_idempotency(self):
        request = {"project": "fuwu", "version": "0.0.1", "staging_ref": "protected-job"}
        for cached in (False, True):
            request_id = "cached-request" if cached else "new-request"
            if cached:
                self.repository.idempotency_put(request_id, "publish", 201, {"status": "published"})
            for key in ("credentials", "path", "aws_secret_access_key", "body", "sourcePath", "bucket"):
                with self.subTest(cached=cached, key=key):
                    with patch.object(self.repository, "idempotency_get", wraps=self.repository.idempotency_get) as lookup:
                        with self.assertRaises(ReleaseError) as error:
                            self.service.publish({**request, key: "/private/sensitive-placeholder"}, actor="acceptance", request_id=request_id)
                        lookup.assert_not_called()
                    self.assertEqual(error.exception.status, 422)
                    self.assertEqual(error.exception.code, "invalid_publish_request")
                    self.assertEqual(error.exception.message, "publish request is invalid")
        self.assertEqual(self.store.objects, {})
        self.assertEqual(self.repository.list_audit(), [])

    def test_rollback_rejects_extra_fields_before_idempotency(self):
        self.repository.idempotency_put("cached-rollback", "rollback", 200, {"status": "rolled_back"})
        with patch.object(self.repository, "idempotency_get", wraps=self.repository.idempotency_get) as lookup:
            with self.assertRaises(ReleaseError) as error:
                self.service.rollback({"project": "fuwu", "version": "0.0.1", "confirm": True, "reason": "验证", "credentials": "sensitive-placeholder"}, actor="acceptance", request_id="cached-rollback")
            lookup.assert_not_called()
        self.assertEqual(error.exception.status, 422)
        self.assertEqual(error.exception.code, "invalid_rollback_request")
        self.assertEqual(error.exception.message, "rollback request is invalid")

    def test_channel_promotion_reuses_verified_manifest_bytes_and_target_pointer(self):
        job, _ = self.job()
        request = {"project": "fuwu", "version": "0.0.1", "staging_ref": job.name}
        self.service.publish({**request, "channel": "beta"}, actor="acceptance", request_id="beta-first")
        manifest_key = "releases/fuwu/v0.0.1/manifest.json"
        original = dict(self.store.objects)
        original_body = self.store.get_object(manifest_key)
        with patch.object(self.store, "head_object", wraps=self.store.head_object) as head, patch.object(self.store, "get_object", wraps=self.store.get_object) as read:
            status, response = self.service.publish({**request, "channel": "stable"}, actor="acceptance", request_id="stable-promote")
            head.assert_any_call(manifest_key)
            read.assert_any_call(manifest_key)
        self.assertEqual(status, 201)
        self.assertEqual(response["release"]["channel"], "stable")
        for key, value in original.items():
            self.assertEqual(self.store.objects[key], value)
        self.assertEqual(self.store.get_object(manifest_key), original_body)
        self.assertEqual(json.loads(original_body)["channel"], "beta")
        for channel in ("stable", "beta"):
            pointer = json.loads(self.store.get_object(f"releases/fuwu/{channel}/latest.json"))
            self.assertEqual(pointer["channel"], channel)
            self.assertEqual(pointer["manifestSha256"], safe_digest(original_body))
        with patch.object(self.store, "put_object", wraps=self.store.put_object) as write:
            repeated_status, repeated = self.service.publish({**request, "channel": "stable"}, actor="acceptance", request_id="stable-promote")
            write.assert_not_called()
        self.assertEqual((repeated_status, repeated), (200, response))

    def test_channel_promotion_rejects_other_manifest_changes_without_overwrite(self):
        job, manifest = self.job()
        request = {"project": "fuwu", "version": "0.0.1", "staging_ref": job.name}
        self.service.publish({**request, "channel": "beta"}, actor="acceptance", request_id="beta-first")
        original = dict(self.store.objects)
        manifest["releaseId"] = "different-release-identity"
        (job / "manifest.json").write_bytes(json_bytes(manifest))
        with self.assertRaises(ReleaseError) as error:
            self.service.publish({**request, "channel": "stable"}, actor="acceptance", request_id="stable-conflict")
        self.assertEqual((error.exception.code, error.exception.status), ("immutable_object_conflict", 409))
        self.assertEqual(self.store.objects, original)
        self.assertIsNone(self.repository.get_latest("fuwu", "stable"))
        self.assertIsNone(self.repository.idempotency_get("stable-conflict"))

    def test_channel_promotion_requires_matching_head_and_get_digest(self):
        job, _ = self.job()
        request = {"project": "fuwu", "version": "0.0.1", "staging_ref": job.name}
        self.service.publish({**request, "channel": "beta"}, actor="acceptance", request_id="beta-first")
        manifest_key = "releases/fuwu/v0.0.1/manifest.json"
        original = dict(self.store.objects)
        original_read = self.store.get_object
        def changed_read(key):
            body = original_read(key)
            return body + b" " if key == manifest_key else body
        with patch.object(self.store, "get_object", side_effect=changed_read):
            with self.assertRaises(ReleaseError) as error:
                self.service.publish({**request, "channel": "stable"}, actor="acceptance", request_id="stable-corrupt")
        self.assertEqual((error.exception.code, error.exception.status), ("storage_verification_failed", 502))
        self.assertEqual(self.store.objects, original)
        self.assertIsNone(self.repository.get_latest("fuwu", "stable"))

    def test_rollback_uses_target_channel_when_manifest_origin_is_different(self):
        job, _ = self.job()
        request = {"project": "fuwu", "version": "0.0.1", "staging_ref": job.name}
        self.service.publish({**request, "channel": "beta"}, actor="acceptance", request_id="beta-first")
        self.service.publish({**request, "channel": "stable"}, actor="acceptance", request_id="stable-promote")
        self.publish("0.0.2")
        immutable = {key: value for key, value in self.store.objects.items() if not key.endswith("/latest.json")}
        beta_pointer = self.store.get_object("releases/fuwu/beta/latest.json")
        status, response = self.service.rollback({"project": "fuwu", "channel": "stable", "version": "0.0.1", "confirm": True, "reason": "cross-channel regression"}, actor="acceptance", request_id="rollback-origin-beta")
        self.assertEqual(status, 200)
        self.assertEqual(response["before"]["version"], "0.0.2")
        self.assertEqual(response["after"]["version"], "0.0.1")
        pointer = json.loads(self.store.get_object("releases/fuwu/stable/latest.json"))
        self.assertEqual((pointer["channel"], pointer["version"]), ("stable", "0.0.1"))
        self.assertEqual(pointer["manifestSha256"], safe_digest(self.store.get_object(pointer["manifestKey"])))
        self.assertEqual(self.store.get_object("releases/fuwu/beta/latest.json"), beta_pointer)
        self.assertEqual({key: value for key, value in self.store.objects.items() if not key.endswith("/latest.json")}, immutable)

    def test_staging_requires_matching_manifest_sums_content_and_size(self):
        for mode in ("manifest_digest", "missing_sums_entry", "extra_sums_entry", "content", "size", "missing_size", "duplicate_sums", "manifest_checksum_file", "manifest_checksum_entries"):
            with self.subTest(mode=mode):
                job, manifest = self.job()
                asset = manifest["assets"]["python"]
                if mode == "manifest_digest":
                    asset["sha256"] = "0" * 64
                elif mode == "missing_sums_entry":
                    (job / "SHA256SUMS").write_text(asset["sha256"] + "  different.tgz\n")
                elif mode == "extra_sums_entry":
                    (job / "SHA256SUMS").write_text((job / "SHA256SUMS").read_text() + asset["sha256"] + "  extra.tgz\n")
                elif mode == "content":
                    (job / "asset.tgz").write_bytes(b"changed")
                elif mode == "size":
                    asset["size"] += 1
                elif mode == "missing_size":
                    del asset["size"]
                elif mode == "duplicate_sums":
                    text = (job / "SHA256SUMS").read_text()
                    (job / "SHA256SUMS").write_text(text + text)
                elif mode == "manifest_checksum_file":
                    manifest["checksums"] = {"sha256": "0" * 64}
                elif mode == "manifest_checksum_entries":
                    manifest["checksums"] = {"entries": {"asset.tgz": "0" * 64}}
                (job / "manifest.json").write_bytes(json_bytes(manifest))
                with self.assertRaises(ReleaseError):
                    self.service.publish({"project": "fuwu", "version": "0.0.1", "staging_ref": job.name}, actor="acceptance", request_id="invalid-" + mode)
                self.assertEqual(self.store.objects, {})

    def test_generic_project_can_publish_python_asset_without_docker(self):
        code, _ = self.publish(project="sample-app")
        self.assertEqual(code, 201)

    def test_first_pointer_head_failure_removes_new_pointer(self):
        self.store.fail_pointer_head = True
        with self.assertRaises(ReleaseError):
            self.publish()
        self.assertNotIn(self.pointer_key, self.store.objects)
        self.assertIsNone(self.repository.get_latest("fuwu", "stable"))
        self.assertIsNone(self.repository.get_release("fuwu", "0.0.1", "stable"))
        self.assertTrue(any(row["event_type"] == "pointer_recovery" and row["details"]["status"] == "removed" for row in self.repository.list_audit()))

    def test_publish_head_failure_restores_exact_before_pointer(self):
        self.publish()
        before = self.store.get_object(self.pointer_key)
        self.store.fail_pointer_head = True
        with self.assertRaises(ReleaseError):
            self.publish("0.0.2")
        self.assertEqual(self.store.get_object(self.pointer_key), before)
        self.assertEqual(self.repository.get_latest("fuwu", "stable")["version"], "0.0.1")
        self.assertIsNone(self.repository.get_release("fuwu", "0.0.2", "stable"))

    def test_publish_database_failure_reverts_all_final_rows_and_pointer(self):
        self.publish()
        before = self.store.get_object(self.pointer_key)
        self.repository.fail_latest = True
        with self.assertRaises(ReleaseError) as error:
            self.publish("0.0.2")
        self.assertEqual(error.exception.code, "publication_finalize_failed")
        self.assertNotIn("private", error.exception.message)
        self.assertEqual(self.store.get_object(self.pointer_key), before)
        self.assertEqual(self.repository.get_latest("fuwu", "stable")["version"], "0.0.1")
        self.assertIsNone(self.repository.get_release("fuwu", "0.0.2", "stable"))
        self.assertIsNone(self.repository.idempotency_get("publish-0.0.2"))
        self.assertIn("releases/fuwu/v0.0.2/manifest.json", self.store.objects)

    def test_rollback_head_and_database_failures_restore_newer_pointer(self):
        self.publish()
        self.publish("0.0.2")
        before = self.store.get_object(self.pointer_key)
        for failure in ("head", "database"):
            with self.subTest(failure=failure):
                self.store.fail_pointer_head = failure == "head"
                self.repository.fail_latest = failure == "database"
                with self.assertRaises(ReleaseError):
                    self.service.rollback({"project": "fuwu", "channel": "stable", "version": "0.0.1", "confirm": True, "reason": "故障注入"}, actor="acceptance", request_id="rollback-" + failure)
                self.assertEqual(self.store.get_object(self.pointer_key), before)
                self.assertEqual(self.repository.get_latest("fuwu", "stable")["version"], "0.0.2")
                self.assertIsNone(self.repository.idempotency_get("rollback-" + failure))
                self.assertEqual(self.repository.list_audit()[0]["event_type"], "rollback_failed")

    def test_failed_compensation_reports_reconciliation_and_preserves_history(self):
        self.publish()
        self.store.block_body = self.store.get_object(self.pointer_key)
        self.store.block_recovery = True
        self.repository.fail_latest = True
        with self.assertRaises(ReleaseError) as error:
            self.publish("0.0.2")
        self.assertEqual(error.exception.code, "pointer_reconciliation_required")
        self.assertEqual(self.repository.get_latest("fuwu", "stable")["version"], "0.0.1")
        self.assertIsNone(self.repository.idempotency_get("publish-0.0.2"))
        self.assertTrue(any(row["event_type"] == "pointer_recovery" and row["details"]["status"] == "failed" for row in self.repository.list_audit()))
        self.assertIn("releases/fuwu/v0.0.1/manifest.json", self.store.objects)

    def test_immutable_conditional_create_handles_race_identically_or_conflict(self):
        class RaceStore(MemoryObjectStore):
            competitor = b"same"
            def put_object(self, key, body, **kwargs):
                self.assert_conditional = kwargs.get("if_none_match")
                if key not in self.objects:
                    self.objects[key] = (self.competitor, safe_digest(self.competitor))
                return super().put_object(key, body, **kwargs)
        for competitor in (b"same", b"different"):
            store = RaceStore()
            store.competitor = competitor
            service = ReleaseService(self.repository, store, staging_dir=self.root)
            if competitor == b"same":
                service._put_immutable("releases/fuwu/v0.0.1/race", b"same", content_type="text/plain", sha256=safe_digest(b"same"))
            else:
                with self.assertRaises(ReleaseError) as error:
                    service._put_immutable("releases/fuwu/v0.0.1/race", b"same", content_type="text/plain", sha256=safe_digest(b"same"))
                self.assertEqual(error.exception.code, "immutable_object_conflict")
            self.assertTrue(store.assert_conditional)
            self.assertEqual(store.get_object("releases/fuwu/v0.0.1/race"), competitor)


class S3ConditionalTests(unittest.TestCase):
    def test_conditional_headers_are_signed_and_precondition_error_is_safe(self):
        store = S3ObjectStore("sample-bucket", credentials=AwsCredentials("example", "test-secret"))
        request = store._signed_request("PUT", "releases/fuwu/v0.0.1/a", b"a", if_none_match=True)
        self.assertEqual(request.get_header("If-none-match"), "*")
        self.assertIn("if-none-match", request.get_header("Authorization"))
        failure = urllib.error.HTTPError(request.full_url, 412, "sensitive provider response", {}, None)
        with patch("fuwu_service.core._open_s3_url", side_effect=failure):
            with self.assertRaises(ReleaseError) as error:
                store.put_object("releases/fuwu/v0.0.1/a", b"a", if_none_match=True)
        self.assertEqual(error.exception.code, "object_precondition_failed")
        self.assertNotIn("sensitive", error.exception.message)

    def test_root_ownership_is_required_and_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials"
            path.write_text("AWS_ACCESS_KEY_ID=example\nAWS_SECRET_ACCESS_KEY=test-secret\n")
            path.chmod(0o600)
            mode = path.stat().st_mode
            with patch("fuwu_service.core.os.fstat", return_value=SimpleNamespace(st_mode=mode, st_uid=501)):
                with self.assertRaises(ReleaseError) as error:
                    load_aws_credentials({"AWS_CREDENTIALS_FILE": str(path)})
            self.assertEqual(error.exception.code, "credentials_not_protected")
        env = {"AWS_ACCESS_KEY_ID": "example", "AWS_SECRET_ACCESS_KEY": "test-secret"}
        self.assertEqual(S3ObjectStore.from_env(env).timeout, 300)
        for value in ("0", "-1", "nan", "inf", "3601", "invalid"):
            with self.subTest(timeout=value), self.assertRaises(ReleaseError):
                S3ObjectStore.from_env({**env, "FUWU_S3_TIMEOUT": value})


if __name__ == "__main__":
    unittest.main()

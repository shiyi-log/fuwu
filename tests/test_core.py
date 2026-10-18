from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from fuwu_service.core import (
    AwsCredentials,
    MemoryObjectStore,
    RBAC,
    ReleaseError,
    ReleaseService,
    S3ObjectStore,
    SQLiteRepository,
    load_aws_credentials,
    next_version,
    safe_relative_path,
    validate_version,
)
from fuwu_service.worker import ReleaseWorker


class ReleaseServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.repo = SQLiteRepository(":memory:")
        self.store = MemoryObjectStore()
        self.service = ReleaseService(self.repo, self.store, staging_dir=self.staging)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_job(self, name="job-1", *, version="0.0.1", revision="a" * 40, content=b"python-wheel") -> str:
        job = self.staging / name
        job.mkdir()
        asset = "fuwu.whl"
        digest = hashlib.sha256(content).hexdigest()
        manifest = {
            "schemaVersion": 1,
            "repository": "https://github.com/shiyi-log/fuwu",
            "version": version,
            "tag": f"v{version}",
            "revision": revision,
            "releaseId": "rel-0.0.1",
            "publishedAt": "2026-10-19T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": {"python": [{"path": asset, "sha256": digest}]},
        }
        (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (job / "SHA256SUMS").write_text(f"{digest}  {asset}\n", encoding="utf-8")
        (job / asset).write_bytes(content)
        return name

    def test_publish_is_immutable_and_idempotent(self) -> None:
        status, response = self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-1")
        self.assertEqual(status, 201)
        self.assertEqual(response["status"], "published")
        self.assertEqual(response["job"]["progress"], 100)
        self.assertIn("releases/fuwu/v0.0.1/manifest.json", self.store.objects)
        self.assertIn("releases/fuwu/v0.0.1/SHA256SUMS", self.store.objects)
        self.assertIn("releases/fuwu/stable/latest.json", self.store.objects)
        latest = json.loads(self.store.objects["releases/fuwu/stable/latest.json"][0])
        self.assertEqual(set(latest), {"schemaVersion", "project", "channel", "version", "tag", "revision", "releaseId", "manifestKey", "manifestSha256", "publishedAt", "immutable", "verificationSource"})
        self.assertTrue(latest["immutable"])
        releases = self.service.releases("fuwu", "stable")
        self.assertTrue(releases[0]["latest"])
        self.assertEqual(releases[0]["objectHeadStatus"], "verified")
        status2, response2 = self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": "job-1"}, actor="admin", request_id="req-1")
        self.assertEqual(status2, 200)
        self.assertEqual(response2["status"], "published")
        self.assertEqual(self.service.status()["jobs"]["validation"]["status"], "validated")

    def test_same_version_different_digest_is_rejected(self) -> None:
        request = {"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}
        self.service.publish(request, actor="admin", request_id="req-1")
        self.make_job("job-2", content=b"different")
        with self.assertRaisesRegex(ReleaseError, "same version"):
            self.service.publish({**request, "staging_ref": "job-2"}, actor="admin", request_id="req-2")
        self.assertEqual(self.repo.get_latest("fuwu", "stable")["version"], "0.0.1")

    def test_preexisting_immutable_object_is_not_overwritten(self) -> None:
        self.store.put_object("releases/fuwu/v0.0.1/fuwu.whl", b"old", sha256=hashlib.sha256(b"old").hexdigest())
        with self.assertRaisesRegex(ReleaseError, "immutable object"):
            self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-preexisting")
        self.assertEqual(self.store.get_object("releases/fuwu/v0.0.1/fuwu.whl"), b"old")

    def test_rollback_moves_pointer_and_keeps_immutable_objects(self) -> None:
        self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-1")
        self.make_job("job-2", version="0.0.2", content=b"second")
        self.service.publish({"project": "fuwu", "version": "0.0.2", "channel": "stable", "staging_ref": "job-2"}, actor="admin", request_id="req-2")
        release = self.repo.get_release("fuwu", "0.0.1", "stable")
        status, response = self.service.rollback({"project": "fuwu", "channel": "stable", "release_id": release["id"], "confirm": True, "reason": "验证回滚"}, actor="admin", request_id="req-3")
        self.assertEqual(status, 200)
        self.assertEqual(response["after"]["version"], "0.0.1")
        self.assertIn("releases/fuwu/v0.0.2/fuwu.whl", self.store.objects)
        self.assertEqual(self.repo.get_latest("fuwu", "stable")["version"], "0.0.1")
        self.assertEqual(self.repo.list_audit("fuwu")[0]["event_type"], "rollback")

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(ReleaseError):
            safe_relative_path("../secret")
        with self.assertRaises(ReleaseError):
            safe_relative_path("/etc/passwd")
        with self.assertRaises(ReleaseError):
            self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": "../outside"}, actor="admin", request_id="req-x")

    def test_version_format_and_carry(self) -> None:
        self.assertEqual(validate_version("0.0.1"), "0.0.1")
        self.assertEqual(next_version("0.0.0"), "0.0.1")
        self.assertEqual(next_version("0.0.9"), "0.1.0")
        with self.assertRaises(ReleaseError):
            validate_version("0.0.10")
        with self.assertRaises(ReleaseError):
            validate_version("v0.0.1")

    def test_audit_does_not_contain_secret_or_private_path(self) -> None:
        self.service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-1")
        text = json.dumps(self.repo.list_audit())
        self.assertNotIn("AWS_SECRET", text)
        self.assertNotIn(str(self.root), text)

    def test_failed_head_verification_does_not_update_latest(self) -> None:
        class BrokenStore(MemoryObjectStore):
            def head_object(self, key, *, public=False):
                head = super().head_object(key, public=public)
                if key.endswith("fuwu.whl"):
                    head["sha256"] = "0" * 64
                return head

        service = ReleaseService(self.repo, BrokenStore(), staging_dir=self.staging)
        with self.assertRaisesRegex(ReleaseError, "verification failed"):
            service.publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-fail")
        self.assertIsNone(self.repo.get_latest("fuwu", "stable"))

    def test_release_worker_runs_same_service_contract(self) -> None:
        worker = ReleaseWorker(self.service)
        try:
            result = worker.submit_publish({"project": "fuwu", "version": "0.0.1", "channel": "stable", "staging_ref": self.make_job()}, actor="admin", request_id="req-worker").result(timeout=5)
            self.assertEqual(result[0], 201)
        finally:
            worker.shutdown()


class SigV4Tests(unittest.TestCase):
    def test_sigv4_request_contains_signed_headers_without_secret(self) -> None:
        store = S3ObjectStore("heyue", "ap-southeast-1", credentials=AwsCredentials("AKIDEXAMPLE", "secret-value"))
        request = store._signed_request("PUT", "releases/fuwu/v0.0.1/a.txt", b"hello", sha256=hashlib.sha256(b"hello").hexdigest())
        self.assertIn("AWS4-HMAC-SHA256", request.headers["Authorization"])
        self.assertIsNotNone(request.get_header("X-amz-meta-sha256"))
        self.assertNotIn("secret-value", str(request.headers))

    def test_credentials_file_requires_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials"
            path.write_text("AWS_" + "ACCESS_KEY_ID=AKIA\nAWS_" + "SECRET_ACCESS_KEY=secret\n", encoding="utf-8")
            path.chmod(0o600)
            credentials = load_aws_credentials({"AWS_CREDENTIALS_FILE": str(path)})
            self.assertEqual(credentials.access_key, "AKIA")
            path.chmod(0o644)
            with self.assertRaisesRegex(ReleaseError, "permissions"):
                load_aws_credentials({"AWS_CREDENTIALS_FILE": str(path)})
            with self.assertRaisesRegex(ReleaseError, "unavailable"):
                load_aws_credentials({"AWS_CREDENTIALS_FILE": str(path) + ".missing", "AWS_EC2_METADATA_DISABLED": "1"})


class RBACTests(unittest.TestCase):
    def test_permissions_are_independent(self) -> None:
        auth = RBAC({"reader": ["release.storage.read"], "publisher": ["release.storage.publish"]})
        self.assertEqual(auth.require("Bearer reader", "release.storage.read"), "reader"[:8])
        with self.assertRaisesRegex(ReleaseError, "permission denied"):
            auth.require("Bearer reader", "release.storage.publish")


if __name__ == "__main__":
    unittest.main()

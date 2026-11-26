from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from fuwu_service.core import MemoryObjectStore, ReleaseError, ReleaseService, SQLiteRepository


class PublicDownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.service = ReleaseService(SQLiteRepository(root / "db.sqlite3"), MemoryObjectStore(), staging_dir=root / "staging")
        self.addCleanup(self.service.repository.conn.close)
        self.addCleanup(self.directory.cleanup)
        body = b"heyue package"
        digest = hashlib.sha256(body).hexdigest()
        job = Path(self.directory.name) / "staging" / "job-1"
        job.mkdir()
        (job / "heyue-python-1.0.2.tar.gz").write_bytes(body)
        manifest = {
            "schemaVersion": 1,
            "repository": "shiyi-log/heyue",
            "version": "1.0.2",
            "tag": "v1.0.2",
            "revision": "1" * 40,
            "releaseId": 1,
            "draft": False,
            "prerelease": False,
            "assets": {"python": [{"path": "heyue-python-1.0.2.tar.gz", "size": len(body), "sha256": digest}]},
        }
        (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (job / "SHA256SUMS").write_text(f"{digest}  heyue-python-1.0.2.tar.gz\n", encoding="utf-8")
        status, response = self.service.publish(
            {"project": "heyue", "version": "1.0.2", "channel": "stable", "staging_ref": "job-1"},
            actor="test",
            request_id="publish-1",
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["status"], "published")

    def test_resolves_only_published_manifest_and_assets(self) -> None:
        manifest = self.service.public_download("heyue", "1.0.2", "manifest.json")
        self.assertEqual(manifest["name"], "manifest.json")
        self.assertEqual(json.loads(manifest["body"])["version"], "1.0.2")
        self.assertEqual(hashlib.sha256(manifest["body"]).hexdigest(), manifest["sha256"])

        asset = self.service.public_download("heyue", "1.0.2", "heyue-python-1.0.2.tar.gz")
        self.assertEqual(asset["size"], len(b"heyue package"))
        self.assertEqual(asset["sha256"], hashlib.sha256(b"heyue package").hexdigest())
        stream = self.service.open_public_download(asset)
        self.assertEqual(stream.read(), b"heyue package")

    def test_unknown_asset_is_not_resolved(self) -> None:
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_download("heyue", "1.0.2", "secret.txt")

    def _publish_version(self, version: str, job_name: str, channel: str = "stable") -> None:
        body = f"heyue package {version}".encode()
        digest = hashlib.sha256(body).hexdigest()
        job = Path(self.directory.name) / "staging" / job_name
        job.mkdir()
        (job / f"heyue-python-{version}.tar.gz").write_bytes(body)
        manifest = {
            "schemaVersion": 1,
            "repository": "shiyi-log/heyue",
            "version": version,
            "tag": f"v{version}",
            "revision": version.replace(".", "")[:1].ljust(40, "a"),
            "releaseId": int(version.replace(".", "")),
            "draft": False,
            "prerelease": False,
            "assets": {"python": [{"path": f"heyue-python-{version}.tar.gz", "size": len(body), "sha256": digest}]},
        }
        (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (job / "SHA256SUMS").write_text(f"{digest}  heyue-python-{version}.tar.gz\n", encoding="utf-8")
        status, response = self.service.publish(
            {"project": "heyue", "version": version, "channel": channel, "staging_ref": job_name},
            actor="test",
            request_id=f"publish-{job_name}",
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["status"], "published")

    def test_versions_lists_published_stable_by_numeric_order(self) -> None:
        self._publish_version("1.0.10", "job-2")
        self._publish_version("1.0.9", "job-3")
        descriptor = self.service.public_versions("heyue")
        self.assertEqual(descriptor["name"], "versions.json")
        self.assertEqual(hashlib.sha256(descriptor["body"]).hexdigest(), descriptor["sha256"])
        document = json.loads(descriptor["body"])
        self.assertEqual(document["project"], "heyue")
        self.assertEqual(document["channel"], "stable")
        self.assertEqual([item["version"] for item in document["versions"]], ["1.0.10", "1.0.9", "1.0.2"])
        first = document["versions"][0]
        self.assertEqual(set(first), {"version", "tag", "revision", "releaseId", "publishedAt", "manifestKey", "manifestSha256"})
        self.assertEqual(first["tag"], "v1.0.10")
        self.assertEqual(first["manifestKey"], "releases/heyue/v1.0.10/manifest.json")

    def test_versions_excludes_beta_and_unpublished(self) -> None:
        self._publish_version("1.0.11", "job-beta", channel="beta")
        document = json.loads(self.service.public_versions("heyue")["body"])
        self.assertEqual([item["version"] for item in document["versions"]], ["1.0.2"])
        release = self.service.repository.get_release("heyue", "1.0.2", "stable")
        self.service.repository.update_release_status(release["id"], "failed")
        document = json.loads(self.service.public_versions("heyue")["body"])
        self.assertEqual(document["versions"], [])

    def test_versions_hides_unknown_and_inactive_projects(self) -> None:
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_versions("nope")
        self.service.repository.update_project("heyue", {"active": False})
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_versions("heyue")


if __name__ == "__main__":
    unittest.main()

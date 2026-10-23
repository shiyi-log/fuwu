from __future__ import annotations

import hashlib
import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import Client  # noqa: E402
from django.urls import resolve  # noqa: E402

from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, SQLiteRepository, safe_digest  # noqa: E402
from fuwu_service.server import ApiHandler  # noqa: E402
from release_api import views  # noqa: E402


class PublicLatestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = MemoryObjectStore()
        self.service = ReleaseService(SQLiteRepository(root / "db.sqlite3"), self.store, staging_dir=root / "staging")
        self.addCleanup(self.service.repository.conn.close)
        self.addCleanup(self.directory.cleanup)

        body = b"latest asset"
        digest = hashlib.sha256(body).hexdigest()
        job = root / "staging" / "job-1"
        job.mkdir()
        (job / "heyue-python-1.0.2.tar.gz").write_bytes(body)
        manifest = {
            "schemaVersion": 1,
            "repository": "shiyi-log/heyue",
            "version": "1.0.2",
            "tag": "v1.0.2",
            "revision": "1" * 40,
            "releaseId": 1002,
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

    def test_service_binds_latest_to_active_published_release(self) -> None:
        descriptor = self.service.public_latest_download("heyue", "stable")
        pointer = json.loads(descriptor["body"])
        self.assertEqual(pointer["project"], "heyue")
        self.assertEqual(pointer["channel"], "stable")
        self.assertEqual(pointer["version"], "1.0.2")
        self.assertEqual(pointer["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
        self.assertEqual(descriptor["sha256"], safe_digest(descriptor["body"]))

        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "beta")

        pointer["version"] = "0.0.1"
        pointer_body = json.dumps(pointer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.store.put_object(descriptor["key"], pointer_body, sha256=safe_digest(pointer_body))
        with self.assertRaisesRegex(ReleaseError, "latest pointer verification failed"):
            self.service.public_latest_download("heyue", "stable")

        self.service.repository.update_project("heyue", {"active": False})
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "stable")

    def test_service_fails_closed_for_missing_storage_or_stale_database_pointer(self) -> None:
        descriptor = self.service.public_latest_download("heyue", "stable")
        self.store.objects.pop(descriptor["key"])
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "stable")

        # 恢复有效指针，再让数据库摘要与已发布发行不一致；路由不得提供旧指针。
        self.store.put_object(descriptor["key"], descriptor["body"], sha256=descriptor["sha256"])
        self.service.repository.conn.execute("UPDATE latest SET digest=? WHERE project_id=? AND channel=?", ("0" * 64, "heyue", "stable"))
        self.service.repository.conn.commit()
        with self.assertRaisesRegex(ReleaseError, "latest pointer verification failed"):
            self.service.public_latest_download("heyue", "stable")

    def test_django_latest_route_is_anonymous_and_non_cacheable(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            response = client.get("/api/downloads/heyue/stable/latest.json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(json.loads(response.content)["version"], "1.0.2")
            self.assertEqual(client.get("/api/downloads/heyue/beta/latest.json").status_code, 404)
            self.assertEqual(client.get("/api/downloads/heyue/1.0.2/latest.json").status_code, 404)
            versioned_asset = client.get("/api/downloads/heyue/1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(versioned_asset.status_code, 200)
            self.assertEqual(b"".join(versioned_asset.streaming_content), b"latest asset")
            self.assertEqual(versioned_asset["Cache-Control"], "public, max-age=31536000, immutable")
            v_versioned_asset = client.get("/api/downloads/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(v_versioned_asset.status_code, 200)
            self.assertEqual(b"".join(v_versioned_asset.streaming_content), b"latest asset")
            self.assertIs(resolve("/api/downloads/heyue/1.0.2/latest.json").func, views.download_view)
            self.assertIs(resolve("/api/downloads/heyue/stable/latest.json").func, views.latest_download_view)
            self.assertEqual(client.post("/api/downloads/heyue/stable/latest.json").status_code, 405)

    def test_django_object_storage_key_routes_use_published_whitelist(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            latest = client.get("/releases/heyue/stable/latest.json")
            self.assertEqual(latest.status_code, 200)
            self.assertEqual(latest["Cache-Control"], "no-store, max-age=0")
            latest_body = b"".join(latest.streaming_content)
            self.assertEqual(json.loads(latest_body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            manifest = client.get("/releases/heyue/v1.0.2/manifest.json")
            self.assertEqual(manifest.status_code, 200)
            self.assertEqual(manifest["Cache-Control"], "public, max-age=31536000, immutable")
            manifest_body = b"".join(manifest.streaming_content)
            self.assertEqual(json.loads(manifest_body)["version"], "1.0.2")
            asset = client.get("/releases/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(asset.status_code, 200)
            asset_body = b"".join(asset.streaming_content)
            self.assertEqual(asset_body, b"latest asset")
            self.assertEqual(asset["X-Fuwu-SHA256"], safe_digest(asset_body))
            self.assertEqual(client.get("/releases/heyue/v1.0.2/secret.txt").status_code, 404)
            self.assertEqual(client.get("/releases/heyue/v1.0.2/%2e%2e/secret.txt").status_code, 404)

    def test_lightweight_latest_route_is_anonymous(self) -> None:
        handler = type("LatestTestHandler", (ApiHandler,), {"service": self.service, "rbac": RBAC({})})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        thread.join(timeout=0)
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request("GET", "/api/downloads/heyue/stable/latest.json")
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(response.getheader("Cache-Control"), "no-store, max-age=0")
            self.assertEqual(json.loads(body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            connection.request("GET", "/api/downloads/heyue/1.0.2/latest.json")
            versioned = connection.getresponse()
            versioned.read()
            self.assertEqual(versioned.status, 404)
            connection.request("GET", "/api/downloads/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            v_versioned = connection.getresponse()
            v_versioned.read()
            self.assertEqual(v_versioned.status, 200)
            connection.request("GET", "/releases/heyue/stable/latest.json")
            storage_latest = connection.getresponse()
            storage_body = storage_latest.read()
            self.assertEqual(storage_latest.status, 200)
            self.assertEqual(storage_latest.getheader("Cache-Control"), "no-store, max-age=0")
            self.assertEqual(json.loads(storage_body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            connection.request("GET", "/releases/heyue/v1.0.2/manifest.json")
            storage_manifest = connection.getresponse()
            manifest_body = storage_manifest.read()
            self.assertEqual(storage_manifest.status, 200)
            self.assertEqual(json.loads(manifest_body)["version"], "1.0.2")
            self.assertEqual(storage_manifest.getheader("X-Fuwu-SHA256"), safe_digest(manifest_body))
            connection.request("GET", "/releases/heyue/v1.0.2/%2e%2e/secret.txt")
            traversal = connection.getresponse()
            traversal.read()
            self.assertEqual(traversal.status, 404)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()

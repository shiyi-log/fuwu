from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("FUWU_AUTH_TOKENS", json.dumps({"reader": ["release.storage.read"], "admin": ["release.storage.read", "release.storage.publish", "release.storage.rollback", "superadmin"]}))

import django

django.setup()

from django.test import Client  # noqa: E402

from release_api import views  # noqa: E402


class DjangoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["FUWU_DATABASE"] = str(Path(self.tmp.name) / "fuwu.sqlite3")
        os.environ["FUWU_STAGING_DIR"] = str(Path(self.tmp.name) / "staging")
        Path(os.environ["FUWU_STAGING_DIR"]).mkdir()
        views.get_service.cache_clear()
        self.client = Client()

    def tearDown(self) -> None:
        views.get_service.cache_clear()
        self.tmp.cleanup()

    def _job(self) -> str:
        job = Path(os.environ["FUWU_STAGING_DIR"]) / "api-job"
        job.mkdir()
        body = b"api-asset"
        digest = hashlib.sha256(body).hexdigest()
        manifest = {"schemaVersion": 1, "repository": "example-org/sample-app", "version": "0.0.1", "tag": "v0.0.1", "revision": "b" * 40, "releaseId": "api-rel", "draft": False, "prerelease": False, "assets": {"python": [{"path": "sample-app.tar.gz", "sha256": digest, "size": len(body)}]}}
        (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (job / "SHA256SUMS").write_text(f"{digest}  sample-app.tar.gz\n", encoding="utf-8")
        (job / "sample-app.tar.gz").write_bytes(body)
        return "api-job"

    def test_auth_health_and_publish_routes(self) -> None:
        self.assertEqual(self.client.get("/healthz").status_code, 200)
        self.assertEqual(self.client.get("/api/projects").status_code, 401)
        response = self.client.post("/api/release-storage/publish", data=json.dumps({"project": "sample-app", "version": "0.0.1", "channel": "stable", "staging_ref": self._job()}), content_type="application/json", HTTP_AUTHORIZATION="Bearer admin", HTTP_IDEMPOTENCY_KEY="api-req")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["job"]["progress"], 100)
        releases = self.client.get("/api/releases?project=sample-app", HTTP_AUTHORIZATION="Bearer reader")
        self.assertEqual(releases.status_code, 200)
        self.assertTrue(releases.json()["items"][0]["latest"])

    def test_storage_settings_route_is_registered_in_django(self) -> None:
        response = self.client.patch(
            "/api/settings/storage",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "unauthorized")


if __name__ == "__main__":
    unittest.main()

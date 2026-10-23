from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from fuwu_service.core import MemoryObjectStore, ReleaseError, ReleaseService, SQLiteRepository, _GitHubRedirectHandler


class _Response:
    def __init__(self, body: bytes):
        self.body = body
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.body


class _Opener:
    def __init__(self, release: dict, assets: dict[str, bytes], *, fail_asset: str | None = None):
        self.release = release
        self.assets = assets
        self.fail_asset = fail_asset
        self.requests = []

    def open(self, request, timeout=0):
        self.requests.append((request, timeout))
        if "/releases/tags/" in request.full_url:
            return _Response(json.dumps(self.release).encode())
        name = request.full_url.rsplit("/", 1)[-1]
        if name == self.fail_asset:
            raise urllib.error.URLError("asset unavailable")
        return _Response(self.assets[name])


class RemoteStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.service = ReleaseService(SQLiteRepository(root / "db.sqlite3"), MemoryObjectStore(), staging_dir=root / "staging")
        self.addCleanup(self.service.repository.conn.close)
        self.addCleanup(self.directory.cleanup)
        self.body = b"remote release payload"
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.manifest = {
            "schemaVersion": 1,
            "repository": "owner/repository",
            "version": "0.0.1",
            "tag": "v0.0.1",
            "revision": "a" * 40,
            "releaseId": "release-1",
            "draft": False,
            "prerelease": False,
            "assets": {"python": {"path": "package.tar.gz", "sha256": self.digest, "size": len(self.body)}},
        }
        self.release = {
            "tag_name": "v0.0.1",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "package.tar.gz", "browser_download_url": "https://github.com/owner/repository/releases/download/v0.0.1/package.tar.gz"},
                {"name": "SHA256SUMS", "browser_download_url": "https://github.com/owner/repository/releases/download/v0.0.1/SHA256SUMS"},
            ],
        }

    def _request(self, **overrides):
        value = {"repository": "owner/repository", "tag": "v0.0.1", "staging_ref": "remote-job", "assets": ["package.tar.gz"], "manifest": self.manifest}
        value.update(overrides)
        return value

    def test_stage_downloads_with_server_token_and_publish_consumes_staging(self) -> None:
        sums = f"{self.digest}  package.tar.gz\n".encode()
        opener = _Opener(self.release, {"package.tar.gz": self.body, "SHA256SUMS": sums})
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            status, staged = self.service.stage_remote_release(self._request(), actor="admin", request_id="stage-1", github_token="ghs-test-token")
        self.assertEqual(status, 201)
        self.assertEqual(staged["staging_ref"], "remote-job")
        api_request = opener.requests[0][0]
        asset_request = opener.requests[1][0]
        self.assertEqual(api_request.get_header("Authorization"), "Bearer ghs-test-token")
        self.assertEqual(asset_request.get_header("Authorization"), "Bearer ghs-test-token")
        job = Path(self.directory.name) / "staging" / "remote-job"
        self.assertEqual((job / "package.tar.gz").read_bytes(), self.body)
        self.assertEqual((job / "SHA256SUMS").read_text(), f"{self.digest}  package.tar.gz\n")
        status, published = self.service.publish({"project": "remote", "version": "0.0.1", "channel": "stable", "staging_ref": "remote-job"}, actor="admin", request_id="publish-1")
        self.assertEqual(status, 201)
        self.assertEqual(published["status"], "published")
        events = self.service.repository.list_audit()
        self.assertNotIn("ghs-test-token", json.dumps(events))

    def test_stage_normalizes_manifest_checksum_file_digest_before_publish(self) -> None:
        upstream_sums = f"{self.digest} *package.tar.gz\n".encode()
        manifest = {
            **self.manifest,
            "checksums": {
                "name": "SHA256SUMS",
                "sha256": hashlib.sha256(upstream_sums).hexdigest(),
                "entries": {"package.tar.gz": self.digest},
            },
        }
        opener = _Opener(self.release, {"package.tar.gz": self.body, "SHA256SUMS": upstream_sums})
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            status, _staged = self.service.stage_remote_release(
                self._request(manifest=manifest), actor="admin", request_id="stage-normalize"
            )
        self.assertEqual(status, 201)
        status, published = self.service.publish(
            {"project": "remote", "version": "0.0.1", "channel": "stable", "staging_ref": "remote-job"},
            actor="admin",
            request_id="publish-normalize",
        )
        self.assertEqual(status, 201)
        self.assertEqual(published["status"], "published")

    def test_stage_prefers_authenticated_asset_api_url_when_id_is_present(self) -> None:
        release = {
            **self.release,
            "assets": [
                {"id": 123, "name": "package.tar.gz"},
                {"id": 456, "name": "SHA256SUMS"},
            ],
        }

        class ApiAssetOpener(_Opener):
            def open(self, request, timeout=0):
                self.requests.append((request, timeout))
                if "/releases/tags/" in request.full_url:
                    return _Response(json.dumps(self.release).encode())
                asset_id = request.full_url.rsplit("/", 1)[-1]
                if asset_id == "123":
                    return _Response(self.assets["package.tar.gz"])
                if asset_id == "456":
                    return _Response(self.assets["SHA256SUMS"])
                raise AssertionError(f"unexpected asset URL: {request.full_url}")

        sums = f"{self.digest}  package.tar.gz\n".encode()
        opener = ApiAssetOpener(release, {"package.tar.gz": self.body, "SHA256SUMS": sums})
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            status, staged = self.service.stage_remote_release(
                self._request(), actor="admin", request_id="stage-api-asset", github_token="ghs-test-token"
            )

        self.assertEqual(status, 201)
        self.assertEqual(staged["staging_ref"], "remote-job")
        asset_requests = [request for request, _timeout in opener.requests[1:]]
        self.assertEqual(
            [request.full_url for request in asset_requests],
            [
                "https://api.github.com/repos/owner/repository/releases/assets/123",
                "https://api.github.com/repos/owner/repository/releases/assets/456",
            ],
        )
        for request in asset_requests:
            self.assertEqual(request.get_header("Accept"), "application/octet-stream")
            self.assertEqual(request.get_header("Authorization"), "Bearer ghs-test-token")

    def test_request_cannot_supply_github_token(self) -> None:
        with self.assertRaisesRegex(ReleaseError, "remote staging request is invalid") as error:
            self.service.stage_remote_release({**self._request(), "github_token": "sensitive"}, actor="admin", request_id="stage-invalid")
        self.assertEqual(error.exception.code, "invalid_remote_stage_request")

    def test_failed_asset_download_removes_partial_job(self) -> None:
        first_body = b"first"
        first_digest = hashlib.sha256(first_body).hexdigest()
        manifest = {**self.manifest, "assets": {"python": [
            self.manifest["assets"]["python"],
            {"path": "first.tar.gz", "sha256": first_digest, "size": len(first_body)},
        ]}}
        release = {**self.release, "assets": [
            {"name": "first.tar.gz", "browser_download_url": "https://github.com/owner/repository/releases/download/v0.0.1/first.tar.gz"},
            {"name": "package.tar.gz", "browser_download_url": "https://github.com/owner/repository/releases/download/v0.0.1/package.tar.gz"},
            {"name": "SHA256SUMS", "browser_download_url": "https://github.com/owner/repository/releases/download/v0.0.1/SHA256SUMS"},
        ]}
        sums = f"{first_digest}  first.tar.gz\n{self.digest}  package.tar.gz\n".encode()
        opener = _Opener(release, {"first.tar.gz": first_body, "package.tar.gz": self.body, "SHA256SUMS": sums}, fail_asset="package.tar.gz")
        request = self._request(assets=["first.tar.gz", "package.tar.gz"], manifest=manifest)
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ReleaseError, "remote release asset is unavailable"):
                self.service.stage_remote_release(request, actor="admin", request_id="stage-fail")
        self.assertFalse((Path(self.directory.name) / "staging" / "remote-job").exists())

    def test_invalid_redirect_host_is_rejected_before_following(self) -> None:
        request = type("Request", (), {"full_url": "https://github.com/owner/repository/releases/download/v0.0.1/package.tar.gz"})()
        with self.assertRaisesRegex(ReleaseError, "remote release asset URL is invalid"):
            _GitHubRedirectHandler().redirect_request(request, None, 302, "Found", {}, "https://evil.example/download")

    def test_invalid_upstream_checksum_is_rejected_and_job_is_removed(self) -> None:
        opener = _Opener(self.release, {"package.tar.gz": self.body, "SHA256SUMS": ("0" * 64 + "  package.tar.gz\n").encode()})
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ReleaseError, "remote asset checksum does not match"):
                self.service.stage_remote_release(self._request(), actor="admin", request_id="stage-checksum")
        self.assertFalse((Path(self.directory.name) / "staging" / "remote-job").exists())

    def test_duplicate_upstream_checksum_entries_are_rejected(self) -> None:
        sums = f"{self.digest}  package.tar.gz\n{self.digest}  package.tar.gz\n".encode()
        opener = _Opener(self.release, {"package.tar.gz": self.body, "SHA256SUMS": sums})
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ReleaseError, "remote SHA256SUMS contains duplicate assets"):
                self.service.stage_remote_release(self._request(), actor="admin", request_id="stage-duplicate")
        self.assertFalse((Path(self.directory.name) / "staging" / "remote-job").exists())

    def test_invalid_upstream_metadata_encoding_is_safe(self) -> None:
        opener = _Opener(self.release, {})
        opener.open = lambda request, timeout=0: _Response(b"\xff")  # type: ignore[method-assign]
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ReleaseError, "remote release metadata is unavailable"):
                self.service.stage_remote_release(self._request(), actor="admin", request_id="stage-encoding")

    def test_discover_latest_filters_draft_and_prerelease_and_reads_manifest(self) -> None:
        formal = {
            "id": 1001,
            "tag_name": "v0.0.1",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-10-24T00:00:00Z",
            "assets": [
                {"id": 11, "name": "manifest.json", "size": 300},
                {"id": 12, "name": "package.tar.gz", "size": len(self.body)},
                {"id": 13, "name": "SHA256SUMS", "size": 75},
            ],
        }
        releases = [
            {"id": 1002, "tag_name": "v0.0.2-rc1", "draft": False, "prerelease": True, "assets": []},
            {"id": 1003, "tag_name": "v0.0.3", "draft": True, "prerelease": False, "assets": []},
            formal,
        ]

        class LatestOpener:
            def __init__(self):
                self.requests = []

            def open(self, request, timeout=0):
                self.requests.append((request, timeout))
                if "/releases?per_page=100" in request.full_url:
                    return _Response(json.dumps(releases).encode())
                if request.full_url.endswith("/assets/11"):
                    return _Response(json.dumps(self_manifest).encode())
                raise AssertionError(f"unexpected URL: {request.full_url}")

        self_manifest = self.manifest
        opener = LatestOpener()
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            discovered = self.service.discover_latest_github_release("owner/repository", github_token="ghs-test-token")
        self.assertEqual(discovered["tag"], "v0.0.1")
        self.assertEqual(discovered["version"], "0.0.1")
        self.assertEqual(discovered["githubReleaseId"], 1001)
        self.assertEqual(discovered["assetNames"], ["package.tar.gz"])
        self.assertEqual(discovered["manifest"]["verificationSource"], "heyue-release-storage")
        self.assertNotIn("_release", discovered)
        self.assertNotIn("_assetMap", discovered)

    def test_stage_latest_discovers_and_stages_formal_release(self) -> None:
        sums = f"{self.digest}  package.tar.gz\n".encode()
        manifest_body = json.dumps(self.manifest).encode()
        package_body = self.body
        release = {
            "id": 1001,
            "tag_name": "v0.0.1",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"id": 11, "name": "manifest.json", "size": len(manifest_body)},
                {"id": 12, "name": "package.tar.gz", "size": len(self.body)},
                {"id": 13, "name": "SHA256SUMS", "size": len(sums)},
            ],
        }

        class LatestStageOpener:
            def __init__(self):
                self.requests = []

            def open(self, request, timeout=0):
                self.requests.append((request, timeout))
                if "/releases?per_page=100" in request.full_url:
                    return _Response(json.dumps([release]).encode())
                if request.full_url.endswith("/assets/11"):
                    return _Response(manifest_body)
                if request.full_url.endswith("/assets/12"):
                    return _Response(package_body)
                if request.full_url.endswith("/assets/13"):
                    return _Response(sums)
                raise AssertionError(f"unexpected URL: {request.full_url}")

        opener = LatestStageOpener()
        request = {"repository": "owner/repository", "staging_ref": "latest-job", "latest": True}
        with patch("fuwu_service.core.urllib.request.build_opener", return_value=opener):
            status, staged = self.service.stage_remote_release(request, actor="admin", request_id="stage-latest", github_token="ghs-test-token")
        self.assertEqual(status, 201)
        self.assertEqual(staged["staging_ref"], "latest-job")
        self.assertEqual(staged["release"]["tag"], "v0.0.1")
        job = Path(self.directory.name) / "staging" / "latest-job"
        self.assertEqual((job / "package.tar.gz").read_bytes(), self.body)
        self.assertEqual(json.loads((job / "manifest.json").read_text())["tag"], "v0.0.1")
        self.assertTrue(all(request.get_header("Authorization") == "Bearer ghs-test-token" for request, _ in opener.requests))

    def test_discover_latest_rejects_when_only_prerelease_exists(self) -> None:
        class OnlyPrereleaseOpener:
            def open(self, request, timeout=0):
                return _Response(json.dumps([{"tag_name": "v0.0.2-rc1", "draft": False, "prerelease": True, "assets": []}]).encode())

        with patch("fuwu_service.core.urllib.request.build_opener", return_value=OnlyPrereleaseOpener()):
            with self.assertRaisesRegex(ReleaseError, "no published GitHub release") as error:
                self.service.discover_latest_github_release("owner/repository")
        self.assertEqual(error.exception.code, "remote_release_not_found")


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fuwu_service.administration import AdministrationStore
from fuwu_service.core import AwsCredentials, MemoryObjectStore, ReleaseError, ReleaseService, S3ObjectStore, SQLiteRepository
from fuwu_service.storage_settings import (
    build_managed_store,
    load_managed_storage,
    masked_access_key,
    read_managed_credentials,
    update_storage_settings,
    validate_storage_settings,
    write_managed_credentials,
)


class ManagedStorageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.admin = AdministrationStore(self.root / "state.sqlite3")
        self.addCleanup(self.admin.conn.close)
        self.repository = SQLiteRepository(self.root / "state.sqlite3")
        self.addCleanup(self.repository.conn.close)
        self.service = ReleaseService(self.repository, MemoryObjectStore(), staging_dir=self.root / "staging")
        self.credentials_file = self.root.resolve() / "private" / "credentials"
        self.body = {
            "bucket": "sample-storage",
            "region": "us-east-1",
            "endpoint": "https://s3.us-east-1.amazonaws.com",
            "prefix": "releases",
            "accessKeyId": "EXAMPLEACCESSKEY1234",
            "secretAccessKey": "example-secret-canary",
            "sessionToken": "example-session-canary",
        }

    def as_simulated_root(self):
        """Test root-only behavior without granting the test process root."""
        real_lstat = Path.lstat
        real_fstat = os.fstat

        def root_stat(result):
            return SimpleNamespace(st_mode=result.st_mode, st_uid=0, st_gid=0, st_size=result.st_size)

        def fake_lstat(path):
            return root_stat(real_lstat(path))

        def fake_fstat(fd):
            return root_stat(real_fstat(fd))

        return (
            patch("fuwu_service.storage_settings.os.geteuid", return_value=0),
            patch("fuwu_service.storage_settings.os.fchown"),
            patch("fuwu_service.storage_settings.Path.lstat", autospec=True, side_effect=fake_lstat),
            patch("fuwu_service.storage_settings.os.fstat", side_effect=fake_fstat),
        )

    def test_managed_credentials_are_file_only_masked_and_reload_after_restart(self):
        patches = self.as_simulated_root()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        payload = update_storage_settings(self.admin, self.service, self.body, credentials_file=self.credentials_file)
        self.assertEqual(payload["credentialSource"], "managed")
        self.assertEqual(payload["accessKeyHint"], "****1234")
        self.assertTrue(payload["credentialsConfigured"])
        self.assertTrue(payload["secretAccessKeyConfigured"])
        self.assertTrue(payload["sessionTokenConfigured"])
        self.assertNotIn(self.body["secretAccessKey"], repr(payload))
        self.assertNotIn(self.body["sessionToken"], repr(payload))
        self.assertNotIn(self.body["accessKeyId"], repr(payload))

        rows = self.admin.conn.execute("SELECT key,value_json FROM service_settings").fetchall()
        stored = repr([tuple(row) for row in rows])
        self.assertNotIn(self.body["secretAccessKey"], stored)
        self.assertNotIn(self.body["sessionToken"], stored)
        self.assertNotIn(self.body["accessKeyId"], stored)
        self.assertEqual(self.credentials_file.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.credentials_file.stat().st_mode & 0o777, 0o600)

        reopened = AdministrationStore(self.root / "state.sqlite3")
        self.addCleanup(reopened.conn.close)
        managed = load_managed_storage(reopened, credentials_file=self.credentials_file)
        self.assertIsNotNone(managed)
        self.assertEqual(managed.settings["bucket"], "sample-storage")
        self.assertEqual(managed.credentials.secret_key, self.body["secretAccessKey"])
        store_and_prefix = build_managed_store(reopened, credentials_file=self.credentials_file, env={})
        self.assertEqual(store_and_prefix[0].credentials.session_token, self.body["sessionToken"])
        self.assertEqual(store_and_prefix[1], "releases")

    def test_access_key_hint_never_reveals_short_values(self):
        for value in ("a", "ab", "abc", "abcd"):
            with self.subTest(length=len(value)):
                self.assertEqual(masked_access_key(value), "****")
        self.assertEqual(masked_access_key("abcde"), "****bcde")

    def test_credentials_require_secure_transport_and_root_process(self):
        with self.assertRaises(ReleaseError) as insecure:
            update_storage_settings(self.admin, self.service, self.body, credentials_file=self.credentials_file, request_secure=False)
        self.assertEqual(insecure.exception.code, "secure_transport_required")
        self.assertNotIn(self.body["secretAccessKey"], insecure.exception.message)

        with patch("fuwu_service.storage_settings.os.geteuid", return_value=501):
            with self.assertRaises(ReleaseError) as non_root:
                write_managed_credentials(self.credentials_file, self.body["accessKeyId"], self.body["secretAccessKey"], None)
        self.assertEqual(non_root.exception.code, "credential_storage_unavailable")
        self.assertFalse(self.credentials_file.parent.exists())

    def test_database_failure_compensates_new_and_replaced_credentials(self):
        patches = self.as_simulated_root()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

        with patch.object(self.admin, "save_storage_configuration", side_effect=RuntimeError("database failure")):
            with self.assertRaisesRegex(RuntimeError, "database failure"):
                update_storage_settings(self.admin, self.service, self.body, credentials_file=self.credentials_file)
        self.assertFalse(self.credentials_file.exists())
        self.assertFalse(self.admin.storage_configuration().get("configured"))

        update_storage_settings(self.admin, self.service, self.body, credentials_file=self.credentials_file)
        original = self.credentials_file.read_bytes()
        rotated = {**self.body, "accessKeyId": "ROTATEDACCESSKEY1234", "secretAccessKey": "replacement-secret"}
        with patch.object(self.admin, "save_storage_configuration", side_effect=RuntimeError("database failure")):
            with self.assertRaisesRegex(RuntimeError, "database failure"):
                update_storage_settings(self.admin, self.service, rotated, credentials_file=self.credentials_file)
        self.assertEqual(self.credentials_file.read_bytes(), original)
        self.assertNotIn(rotated["secretAccessKey"].encode(), self.credentials_file.read_bytes())

    def test_managed_destination_rejects_environment_mixing_and_bad_endpoints(self):
        self.assertEqual(
            validate_storage_settings({**self.body, "accessKeyId": "", "secretAccessKey": "", "sessionToken": ""})["endpoint"],
            "https://s3.us-east-1.amazonaws.com",
        )
        for endpoint in (
            "http://objects.example.invalid",
            "https://user:pass@objects.example.invalid",
            "https://objects.example.invalid/path?token=hidden",
            "https://objects.example.invalid/#fragment",
            "https://127.0.0.1/private",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ReleaseError) as invalid:
                    validate_storage_settings({**self.body, "endpoint": endpoint})
                self.assertEqual(invalid.exception.code, "invalid_storage_settings")
                self.assertNotIn("hidden", invalid.exception.message)
        allowed = "https://objects.example.invalid"
        result = validate_storage_settings({**self.body, "endpoint": allowed}, allowed_endpoints=allowed)
        self.assertEqual(result["endpoint"], allowed)

    def test_existing_releases_lock_destination_but_allow_credential_rotation(self):
        project = self.repository.create_project({"id": "sample", "name": "Sample", "repository": "example/sample"})
        self.repository.save_release({
            "id": "release-id", "project_id": project["id"], "version": "0.0.1", "channel": "stable",
            "repository": project["repository"], "tag": "v0.0.1", "revision": "a" * 40,
            "release_id": "1", "published_at": "2026-01-01T00:00:00Z", "digest": "b" * 64,
            "manifest_key": "releases/sample/v0.0.1/manifest.json", "status": "published", "manifest_json": "{}",
        }, [])
        with self.assertRaises(ReleaseError) as locked:
            update_storage_settings(self.admin, self.service, self.body, credentials_file=self.credentials_file)
        self.assertEqual(locked.exception.code, "storage_destination_locked")

        same = {**self.body, "bucket": "existing", "region": "us-east-1", "endpoint": "https://s3.us-east-1.amazonaws.com", "prefix": "releases"}
        self.admin.save_storage_configuration({field: same[field] for field in ("bucket", "region", "endpoint", "prefix")})
        patches = self.as_simulated_root()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        changed = {**same, "accessKeyId": "ROTATEDACCESSKEY1234", "secretAccessKey": "rotated-secret", "sessionToken": ""}
        payload = update_storage_settings(self.admin, self.service, changed, credentials_file=self.credentials_file)
        self.assertEqual(payload["bucket"], "existing")
        self.assertEqual(payload["accessKeyHint"], "****1234")

    def test_probe_is_read_only_bounded_and_does_not_return_provider_errors(self):
        store = S3ObjectStore("sample-storage", credentials=AwsCredentials("EXAMPLEACCESSKEY1234", "top-secret"), timeout=300)
        response = Mock()
        response.status = 200
        response.headers = {}
        response.read.return_value = b""
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch("fuwu_service.core._open_s3_url", return_value=response) as open_request:
            result = store.probe(timeout=3)
        self.assertTrue(result["reachable"])
        args, kwargs = open_request.call_args
        self.assertEqual(args[0].get_method(), "HEAD")
        self.assertEqual(kwargs["timeout"], 3)
        self.assertEqual(args[0].full_url, "https://s3.us-east-1.amazonaws.com/sample-storage")
        import urllib.error
        failure = urllib.error.HTTPError(args[0].full_url, 403, "provider secret and response", {}, None)
        with patch("fuwu_service.core._open_s3_url", side_effect=failure):
            failed = store.probe()
        self.assertFalse(failed["reachable"])
        self.assertNotIn("provider", repr(failed))
        self.assertNotIn("secret", repr(failed))

    def test_s3_requests_do_not_follow_provider_redirects(self):
        seen = []

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                seen.append(self.path)
                if self.path == "/sample-storage":
                    self.send_response(302)
                    self.send_header("Location", "/redirect-target")
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        store = S3ObjectStore("sample-storage", endpoint=f"http://127.0.0.1:{server.server_port}",
                              credentials=AwsCredentials("EXAMPLEACCESSKEY1234", "test-secret"), timeout=2)
        result = store.probe(timeout=1)
        self.assertFalse(result["reachable"])
        self.assertEqual(seen, ["/sample-storage"])

    def test_unsafe_existing_credential_file_is_rejected(self):
        self.credentials_file.parent.mkdir(mode=0o700)
        self.credentials_file.write_text("AWS_ACCESS_KEY_ID=example\nAWS_SECRET_ACCESS_KEY=secret\n", encoding="utf-8")
        self.credentials_file.chmod(0o600)
        with self.assertRaises(ReleaseError) as error:
            read_managed_credentials(self.credentials_file)
        self.assertEqual(error.exception.code, "credentials_unavailable")


if __name__ == "__main__":
    unittest.main()

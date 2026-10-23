import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()
from django.test import Client

from fuwu_service.accounts import AccountStore, get_account_store
from fuwu_service.administration import administration_store
from fuwu_service.api import handle_api
from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseService, SQLiteRepository
from fuwu_service.server import ApiHandler
from fuwu_service import accounts as account_module, administration as administration_module


class ManagementApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"FUWU_DATABASE": str(self.root / "test.sqlite3"), "FUWU_AUTH_TOKENS": "{}"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.accounts = get_account_store()
        def close_cached_stores():
            key = str(self.root / "test.sqlite3")
            account = account_module._STORES.pop(key, None)
            if account:
                account.close()
            operation_store = administration_module._stores.pop(key, None)
            if operation_store:
                operation_store.conn.close()
        self.addCleanup(close_cached_stores)
        # These tests exercise HTTP integration, not password work-factor tuning.
        self.hasher_patch = patch("django.contrib.auth.hashers.PBKDF2PasswordHasher.iterations", 1000)
        self.hasher_patch.start()
        self.addCleanup(self.hasher_patch.stop)
        self.admin = self.accounts.bootstrap_superadmin("admin", "Strong-Admin-Password-2026", "管理员")
        self.service = ReleaseService(SQLiteRepository(self.root / "test.sqlite3"), MemoryObjectStore(), staging_dir=self.root / "staging")
        self.addCleanup(self.service.repository.conn.close)
        for project in ("alpha", "beta"):
            self.service.create_project({"id": project, "name": project, "repository": "owner/" + project}, actor="user:" + self.admin["id"], request_id="create-" + project)
        self.reader = self.accounts.create_user(self.admin["id"], {"username": "reader", "displayName": "只读", "password": "Strong-Reader-Password-2026",
            "roles": ["viewer"], "permissions": ["logs.read"], "project_ids": ["alpha"]})
        self.rbac = RBAC.from_env()

    def call(self, method, path, *, token=None, body=None, query=None, request_id="api-test", request_secure=False):
        return handle_api(self.service, self.rbac, method=method, path=path, query=query or {}, body=body or {},
            authorization="Bearer " + token if token else None, request_id=request_id, client_id="test-client", request_secure=request_secure)

    def login(self, username="admin", password="Strong-Admin-Password-2026"):
        status, result = self.call("POST", "/api/auth/login", body={"username": username, "password": password})
        self.assertEqual(status, 200, result)
        return result["data"]["accessToken"]

    def test_account_login_profile_and_logout_revoke_session(self):
        token = self.login()
        status, profile = self.call("GET", "/api/user/info", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(profile["data"]["homePath"], "/projects")
        self.assertIn("superadmin", profile["data"]["roles"])
        self.assertIsNone(profile["data"]["project_ids"])
        self.assertEqual(self.call("POST", "/api/auth/logout", token=token)[0], 200)
        self.assertEqual(self.call("GET", "/api/projects", token=token)[0], 401)

    def test_projects_status_logs_and_writes_enforce_account_scope(self):
        token = self.login("reader", "Strong-Reader-Password-2026")
        status, result = self.call("GET", "/api/projects", token=token)
        self.assertEqual((status, [item["id"] for item in result["items"]]), (200, ["alpha"]))
        self.assertEqual(self.call("GET", "/api/projects/beta", token=token)[0], 403)
        self.assertEqual(self.call("GET", "/api/releases", token=token, query={"project": "beta"})[0], 403)
        status, result = self.call("GET", "/api/release-storage/status", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(result["projects"], 1)
        self.assertEqual(result["releases"], 0)
        self.assertIsNone(result["reachable"])
        self.assertIsNone(result["connectivityCheckedAt"])
        self.assertEqual(self.call("GET", "/api/users", token=token)[0], 403)
        self.assertEqual(self.call("PATCH", "/api/settings", token=token, body={"appName": "changed"})[0], 403)
        self.assertEqual(self.call("POST", "/api/release-storage/publish", token=token, body={"project": "alpha"})[0], 403)
        status, logs = self.call("GET", "/api/logs", token=token)
        self.assertEqual(status, 200)
        self.assertTrue(all(row["project_id"] == "alpha" for row in logs["items"]))

    def test_latest_release_discovery_uses_project_repository_and_global_github_token(self):
        token = self.login()
        discovered = {
            "repository": "owner/alpha",
            "tag": "v0.0.1",
            "version": "0.0.1",
            "revision": "a" * 40,
            "releaseId": "release-1",
            "githubReleaseId": 123,
            "publishedAt": "2026-10-24T00:00:00Z",
            "draft": False,
            "prerelease": False,
            "assets": [{"name": "alpha.tar.gz", "size": 12}],
            "assetNames": ["alpha.tar.gz"],
            "manifest": {"schemaVersion": 1},
        }
        github_store = type("GithubStore", (), {"get_token": lambda _self: "ghs-test-token"})()
        with patch("fuwu_service.api._github_authorizations", return_value=github_store), \
             patch.object(self.service, "discover_latest_github_release", return_value=discovered) as discover:
            status, result = self.call("GET", "/api/release-storage/latest", token=token, query={"project": "alpha"})
        self.assertEqual(status, 200)
        self.assertEqual(result["project"], "alpha")
        self.assertEqual(result["tag"], "v0.0.1")
        self.assertEqual(result["repository"], "owner/alpha")
        discover.assert_called_once_with("owner/alpha", github_token="ghs-test-token")

    def test_operational_logs_use_safe_route_and_exclude_credentials(self):
        token = self.login()
        canary = "PASSWORD-CANARY-DO-NOT-LOG"
        self.call("POST", "/api/auth/login", body={"username": "nobody", "password": canary})
        self.call("GET", "/api/unknown/" + canary, token=token, query={"secret": canary})
        status, settings = self.call("PATCH", "/api/settings", token=token, body={"appName": "fuwu 测试", "logRetentionDays": 30})
        self.assertEqual(status, 200)
        self.assertEqual(settings["appName"], "fuwu 测试")
        status, logs = self.call("GET", "/api/logs", token=token)
        self.assertEqual(status, 200)
        text = json.dumps(logs)
        self.assertNotIn(canary, text)
        self.assertNotIn(token, text)
        self.assertNotIn("password_hash", text)
        self.assertNotIn(str(self.root), text)
        self.assertTrue(any(row["category"] == "auth" and row["status_code"] == 401 for row in logs["items"]))
        self.assertTrue(any(row["route"] == "/api/unknown" for row in logs["items"]))

    def test_storage_settings_route_requires_secure_superadmin_and_masks_secrets(self):
        admin_token = self.login()
        reader_token = self.login("reader", "Strong-Reader-Password-2026")
        status, settings = self.call("GET", "/api/settings", token=admin_token)
        self.assertEqual(status, 200)
        self.assertIn("storage", settings)
        self.assertNotIn("secretAccessKey", settings["storage"])
        self.assertNotIn("sessionToken", settings["storage"])
        reader_settings = self.call("GET", "/api/settings", token=reader_token)[1]
        self.assertIsNone(reader_settings["storage"]["accessKeyHint"])

        canary = "secret-value-must-not-appear"
        body = {"bucket": "sample-storage", "region": "us-east-1", "prefix": "releases",
                "accessKeyId": "EXAMPLEACCESSKEY1234", "secretAccessKey": canary,
                "sessionToken": "session-value-must-not-appear", "requestId": canary}
        self.assertEqual(self.call("PATCH", "/api/settings/storage", token=admin_token, body=body)[0], 400)
        self.assertEqual(self.call("PATCH", "/api/settings/storage", token=reader_token, body=body, request_secure=True)[0], 403)

        response_payload = {"bucket": "sample-storage", "region": "us-east-1", "endpoint": "https://s3.us-east-1.amazonaws.com",
                            "prefix": "releases", "credentialsConfigured": True,
                            "credentialSource": "managed", "accessKeyHint": "****1234"}
        with patch("fuwu_service.api.update_storage_settings", return_value=response_payload) as update:
            status, result = self.call("PATCH", "/api/settings/storage", token=admin_token, body=body,
                                       request_secure=True, request_id=canary)
        self.assertEqual(status, 200)
        self.assertNotIn(canary, json.dumps(result))
        self.assertNotIn("EXAMPLEACCESSKEY1234", json.dumps(result))
        self.assertNotIn("session-value-must-not-appear", json.dumps(result))
        logged_id = administration_module.administration_store().conn.execute(
            "SELECT request_id FROM operation_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        self.assertRegex(logged_id, r"^req-")
        self.assertNotEqual(logged_id, canary)

    def test_django_and_real_http_adapters_share_account_contract(self):
        handler = type("ManagementTestHandler", (ApiHandler,), {"service": self.service, "rbac": self.rbac})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            response = client.post("/api/auth/login", data=json.dumps({"username": "reader", "password": "Strong-Reader-Password-2026"}), content_type="application/json")
            self.assertEqual(response.status_code, 200)
            session = response.json()["data"]["accessToken"]
            response = client.get("/api/projects", HTTP_AUTHORIZATION="Bearer " + session)
            self.assertEqual([p["id"] for p in response.json()["items"]], ["alpha"])
        url = "http://127.0.0.1:" + str(server.server_port)
        request = urllib.request.Request(url + "/api/projects", headers={"Authorization": "Bearer " + session})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual([p["id"] for p in json.loads(response.read())["items"]], ["alpha"])
        request = urllib.request.Request(url + "/api/projects/beta", headers={"Authorization": "Bearer " + session})
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()

from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch

from http.server import ThreadingHTTPServer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from fuwu_service.accounts import get_account_store
from fuwu_service.api import handle_api
from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseService, SQLiteRepository
from fuwu_service.server import ApiHandler
from fuwu_service import accounts as account_module, administration as administration_module


class GitHubAuthorizationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            "FUWU_DATABASE": str(self.root / "test.sqlite3"),
            "FUWU_AUTH_TOKENS": "{}",
            "FUWU_GITHUB_CREDENTIALS_FILE": str(self.root / "private" / "github.json"),
        })
        self.environment.start()
        self.accounts = get_account_store()
        self.admin = self.accounts.bootstrap_superadmin("admin", "Strong-Admin-Password-2026", "管理员")
        self.manager = self.accounts.create_user(self.admin["id"], {
            "username": "manager", "displayName": "项目经理", "password": "Strong-Manager-Password-2026",
            "roles": ["viewer"], "permissions": ["projects.manage"], "project_ids": ["alpha"],
        })
        self.viewer = self.accounts.create_user(self.admin["id"], {
            "username": "viewer", "displayName": "只读用户", "password": "Strong-Viewer-Password-2026",
            "roles": ["viewer"], "project_ids": ["alpha"],
        })
        self.service = ReleaseService(SQLiteRepository(self.root / "test.sqlite3"), MemoryObjectStore(), staging_dir=self.root / "staging")
        self.service.create_project({"id": "alpha", "name": "alpha", "repository": "owner/alpha"}, actor="user:" + self.admin["id"], request_id="create-alpha")
        self.service.create_project({"id": "beta", "name": "beta", "repository": "owner/beta"}, actor="user:" + self.admin["id"], request_id="create-beta")
        self.rbac = RBAC.from_env()

    def tearDown(self):
        key = str(self.root / "test.sqlite3")
        store = account_module._STORES.pop(key, None)
        if store:
            store.close()
        operation_store = administration_module._stores.pop(key, None)
        if operation_store:
            operation_store.conn.close()
        self.service.repository.conn.close()
        self.environment.stop()
        self.temp.cleanup()

    def call(self, method, path, *, token=None, body=None, request_id="github-api-test"):
        return handle_api(
            self.service, self.rbac, method=method, path=path, query={}, body=body or {},
            authorization="Bearer " + token if token else None, request_id=request_id,
            client_id="test-client", request_secure=True,
        )

    def login(self, username, password):
        status, response = self.call("POST", "/api/auth/login", body={"username": username, "password": password})
        self.assertEqual(status, 200, response)
        return response["data"]["accessToken"]

    def test_project_scoped_authorization_does_not_expose_token(self):
        admin_token = self.login("admin", "Strong-Admin-Password-2026")
        manager_token = self.login("manager", "Strong-Manager-Password-2026")
        secret = "github_pat_secret_value"
        identity = {"login": "release-bot", "node_id": "MDQ6VXNlcjE=", "scopes": ["repo"]}
        with patch("fuwu_service.api.verify_github_token", return_value=identity) as verify, \
             patch("fuwu_service.api.verify_github_repository_access") as verify_repo:
            status, response = self.call("POST", "/api/projects/alpha/github-authorization", token=manager_token, body={"token": secret})
        self.assertEqual(status, 200, response)
        self.assertNotIn(secret, json.dumps(response))
        verify.assert_called_once_with(secret)
        verify_repo.assert_called_once_with(secret, "owner/alpha")
        self.assertEqual(self.call("GET", "/api/projects/alpha/github-authorization", token=manager_token)[1]["login"], "release-bot")
        self.assertEqual(self.call("GET", "/api/projects/beta/github-authorization", token=manager_token)[0], 403)
        self.assertEqual(self.call("POST", "/api/projects/beta/github-authorization", token=manager_token, body={"token": secret})[0], 403)
        status, response = self.call("DELETE", "/api/projects/alpha/github-authorization", token=admin_token)
        self.assertEqual((status, response["authorized"]), (200, False))
        self.assertNotIn(secret, json.dumps(response))
        raw = (self.root / "private" / "github.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        audit = self.service.repository.list_audit("alpha")
        self.assertTrue(any(item["event_type"] == "github_authorized" for item in audit))
        self.assertTrue(all(secret not in json.dumps(item) for item in audit))

    def test_authorization_requires_projects_manage_and_rejects_extra_fields(self):
        manager_token = self.login("manager", "Strong-Manager-Password-2026")
        viewer_token = self.login("viewer", "Strong-Viewer-Password-2026")
        self.assertEqual(self.call("GET", "/api/projects/alpha/github-authorization", token=viewer_token)[0], 403)
        self.assertEqual(self.call("DELETE", "/api/projects/alpha/github-authorization", token=viewer_token)[0], 403)
        insecure_status, insecure_response = handle_api(
            self.service, self.rbac, method="POST", path="/api/projects/alpha/github-authorization", query={},
            body={"token": "github_pat_secret"}, authorization="Bearer " + manager_token,
            request_id="token-canary", client_id="test-client", request_secure=False,
        )
        self.assertEqual((insecure_status, insecure_response["code"]), (400, "secure_transport_required"))
        self.assertNotEqual(insecure_response["request_id"], "token-canary")
        self.assertNotIn("token-canary", json.dumps(administration_module.administration_store().logs()))
        status, response = self.call("POST", "/api/projects/alpha/github-authorization", token=manager_token, body={"token": "github_pat_secret", "login": "leak"})
        self.assertEqual(status, 422)
        self.assertEqual(response["code"], "invalid_github_authorization")
        self.assertEqual(self.call("GET", "/api/projects/alpha/github-authorization")[0], 401)

    def test_lightweight_adapter_dispatches_secure_delete_and_rejects_a_body(self):
        """The HTTP adapter must preserve the same revoke contract as Django."""

        admin_token = self.login("admin", "Strong-Admin-Password-2026")
        identity = {"login": "release-bot", "node_id": "MDQ6VXNlcjE=", "scopes": ["repo"]}
        handler = type("GitHubAuthorizationTestHandler", (ApiHandler,), {"service": self.service, "rbac": self.rbac})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)

        base = "http://127.0.0.1:" + str(server.server_port)
        auth_headers = {"Authorization": "Bearer " + admin_token}
        malformed = urllib.request.Request(
            base + "/api/projects/alpha/github-authorization",
            data=b"{",
            headers={"X-Request-ID": "token-canary", "Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as malformed_error:
            urllib.request.urlopen(malformed, timeout=5)
        malformed_payload = json.loads(malformed_error.exception.read())
        self.assertEqual(malformed_error.exception.code, 400)
        self.assertEqual(malformed_payload["code"], "invalid_json")
        self.assertNotEqual(malformed_payload["request_id"], "token-canary")
        malformed_error.exception.close()

        with patch("fuwu_service.api.verify_github_token", return_value=identity), \
             patch("fuwu_service.api.verify_github_repository_access"):
            request = urllib.request.Request(
                base + "/api/projects/alpha/github-authorization",
                data=json.dumps({"token": "github_pat_http"}).encode(),
                headers={**auth_headers, "Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)

        request = urllib.request.Request(
            base + "/api/projects/alpha/github-authorization",
            headers=auth_headers,
            method="DELETE",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertFalse(json.loads(response.read())["authorized"])

        # DELETE bodies are parsed by the adapter and rejected by handle_api;
        # they must not be silently ignored as an authorization revoke.
        request = urllib.request.Request(
            base + "/api/projects/alpha/github-authorization",
            data=json.dumps({"token": "github_pat_http"}).encode(),
            headers={**auth_headers, "Content-Type": "application/json"},
            method="DELETE",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(error.exception.code, 422)
        error.exception.close()


if __name__ == "__main__":
    unittest.main()

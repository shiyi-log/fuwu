"""Release-candidate checks across module, database and HTTP boundaries."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.core import mail
from django.test import Client, override_settings

from fuwu_service import accounts, core, projects
from fuwu_service.accounts import AccountStore
from fuwu_service.errors import ReleaseError
from fuwu_service.object_store import MemoryObjectStore
from fuwu_service.rbac import RBAC
from fuwu_service.release_service import ReleaseService
from fuwu_service.repository import SQLiteRepository
from fuwu_service.server import ApiHandler
from release_api import views

PASSWORD = "old-integration-password-2026"
NEW_PASSWORD = "new-integration-password-2026"


class RecoveryTransportIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "accounts.sqlite3"
        self.repository = SQLiteRepository(self.database)
        self.addCleanup(self.repository.conn.close)
        self.store = AccountStore(self.database)
        self.addCleanup(self.store.close)
        self.service = ReleaseService(self.repository, MemoryObjectStore(), staging_dir=Path(self.directory.name) / "staging")
        self.rbac = RBAC(account_store_factory=lambda: self.store)
        patches = (
            patch.object(PBKDF2PasswordHasher, "iterations", 1000),
            patch.object(accounts, "get_account_store", return_value=self.store),
            patch.object(views, "get_service", return_value=self.service),
            override_settings(
                SECRET_KEY="integration-only-reset-secret-2026",
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                EMAIL_HOST="mail.example.test",
                DEFAULT_FROM_EMAIL="service@example.test",
                SECURE_PROXY_SSL_HEADER=None,
            ),
        )
        for context in patches:
            context.start() if hasattr(context, "start") else context.enable()
            self.addCleanup(context.stop if hasattr(context, "stop") else context.disable)
        self.client = Client()

    def request(self, adapter, path, body, *, headers=None, remote="127.0.0.1", trusted=False):
        headers = headers or {}
        header_items = list(headers.items()) if hasattr(headers, "items") else headers
        data = json.dumps(body).encode()
        with patch.dict(os.environ, {"FUWU_TRUST_X_FORWARDED_PROTO": "1" if trusted else "0"}), override_settings(
            SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https") if trusted else None,
        ):
            if adapter == "django":
                response = self.client.generic("POST", path, data=data, content_type="application/json", REMOTE_ADDR=remote,
                    **{"HTTP_" + name.upper().replace("-", "_"): value for name, value in header_items})
                return response.status_code, response.json()
            # Exercise BaseHTTPRequestHandler parsing/dispatch over a private
            # socket pair; no listening port or long-running server is needed.
            handler = type("RecoveryHandler", (ApiHandler,), {"service": self.service, "rbac": self.rbac})
            client, server = socket.socketpair()
            try:
                client.settimeout(3)
                request = f"POST {path} HTTP/1.0\r\nHost: testserver\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                request += "".join(f"{name}: {value}\r\n" for name, value in header_items)
                client.sendall(request.encode() + b"\r\n" + data)
                client.shutdown(socket.SHUT_WR)
                handler(server, (remote, 12345), None)
                server.close()
                response = http.client.HTTPResponse(client)
                response.begin()
                try:
                    return response.status, json.loads(response.read())
                finally:
                    response.close()
            finally:
                client.close()
                server.close()

    def test_both_adapters_reject_proxy_spoofing_without_loopback_bypass(self):
        rejected = (
            ("127.0.0.1", {"X-Forwarded-Proto": "https"}, False),
            ("127.0.0.1", {"X-Forwarded-Proto": "http"}, True),
            ("127.0.0.1", {"X-Forwarded-For": "203.0.113.8"}, True),
            ("127.0.0.1", {"Forwarded": "for=203.0.113.8;proto=https"}, True),
            ("203.0.113.8", {}, False),
            ("203.0.113.8", {"X-Forwarded-Proto": "https"}, False),
            ("127.0.0.1", {"X-Forwarded-Proto": "https, http"}, True),
            ("127.0.0.1", {"X-Forwarded-Proto": "http, https"}, True),
        )
        for adapter in ("django", "http"):
            for remote, headers, trusted in rejected:
                for path, body in (
                    ("/api/auth/password-reset/code", {"email": "owner@example.test"}),
                    ("/api/auth/password-reset", {"email": "owner@example.test", "code": "123456", "newPassword": NEW_PASSWORD}),
                ):
                    with self.subTest(adapter=adapter, remote=remote, headers=headers, trusted=trusted, path=path):
                        status, payload = self.request(adapter, path, body, remote=remote, headers=headers, trusted=trusted)
                        self.assertEqual(status, 400)
                        self.assertEqual(payload["code"], "secure_transport_required")
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM account_password_reset_codes").fetchone()[0], 0)

    def test_lightweight_adapter_rejects_duplicate_forwarded_protocol_lines(self):
        for values in (("https", "http"), ("http", "https"), ("https", "https")):
            with self.subTest(values=values):
                status, payload = self.request("http", "/api/auth/password-reset/code", {"email": "missing@example.test"},
                    headers=[("X-Forwarded-Proto", value) for value in values], trusted=True)
                self.assertEqual(status, 400)
                self.assertEqual(payload["code"], "secure_transport_required")

    def test_trusted_https_reset_and_direct_loopback_both_adapters(self):
        for adapter in ("django", "http"):
            email = adapter + "@example.test"
            if adapter == "django":
                admin = self.store.bootstrap_superadmin(email, PASSWORD)
            else:
                self.store.create_user(admin["id"], {"username": email, "password": PASSWORD, "roles": ["viewer"], "project_ids": []})
            session = self.store.login(email, PASSWORD)["accessToken"]
            with self.subTest(adapter=adapter):
                status, _ = self.request(adapter, "/api/auth/password-reset/code", {"email": email},
                    headers={"X-Forwarded-For": "203.0.113.8", "X-Forwarded-Proto": "https"}, trusted=True)
                self.assertEqual(status, 200)
                self.assertEqual(mail.outbox[-1].to, [email])
                code = mail.outbox[-1].body.split("验证码是 ")[1].split("，")[0]
                status, body = self.request(adapter, "/api/auth/password-reset", {"email": email, "code": code, "newPassword": NEW_PASSWORD},
                    headers={"X-Forwarded-Proto": "https"}, trusted=True)
                self.assertEqual((status, body["data"]["status"]), (200, "password_reset"))
                with self.assertRaises(ReleaseError):
                    self.store.authenticate(session)
                with self.assertRaises(ReleaseError):
                    self.store.login(email, PASSWORD)
                self.assertEqual(self.store.login(email, NEW_PASSWORD)["user"]["username"], email)
                status, _ = self.request(adapter, "/api/auth/password-reset/code", {"email": "missing@example.test"})
                self.assertEqual(status, 200)


class ReleaseUpgradeIntegrationTests(unittest.TestCase):
    def test_legacy_project_exports_keep_error_and_validation_contract(self):
        self.assertIs(core.ReleaseError, ReleaseError)
        self.assertIs(core.RBAC, RBAC)
        self.assertIs(core.ReleaseService, ReleaseService)
        self.assertIs(core.SQLiteRepository, SQLiteRepository)
        self.assertIs(core.MemoryObjectStore, MemoryObjectStore)
        self.assertEqual(core.normalize_repository("https://github.com/Example-org/Demo"), "example-org/demo")
        self.assertEqual(core.project_create_values({"id": "demo", "name": "Demo", "repository": "example-org/demo"})["id"], "demo")
        with self.assertRaises(core.ProjectInputError):
            projects.validate_project_id("../demo")
        with self.assertRaises(projects.ProjectInputError):
            core.validate_project_id("../demo")

    def test_009_account_upgrade_preserves_release_rows_hashes_and_sessions(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(PBKDF2PasswordHasher, "iterations", 1000):
            database = Path(directory) / "legacy.sqlite3"
            repository = SQLiteRepository(database)
            self.addCleanup(repository.conn.close)
            staging = Path(directory) / "staging"
            job = staging / "upgrade-job"
            job.mkdir(parents=True)
            content = b"release-upgrade-fixture"
            digest = hashlib.sha256(content).hexdigest()
            (job / "demo.tar.gz").write_bytes(content)
            (job / "SHA256SUMS").write_text(f"{digest}  demo.tar.gz\n")
            manifest = {"schemaVersion": 1, "repository": "example-org/demo", "version": "1.0.14", "tag": "v1.0.14", "revision": "a" * 40,
                "releaseId": "release-upgrade", "draft": False, "prerelease": False,
                "assets": {"python": [{"path": "demo.tar.gz", "size": len(content), "sha256": digest}]}}
            (job / "manifest.json").write_text(json.dumps(manifest))
            service = core.ReleaseService(repository, core.MemoryObjectStore(), staging_dir=staging)
            status, _ = service.publish({"project": "demo", "version": "1.0.14", "channel": "stable", "staging_ref": "upgrade-job"}, actor="test-admin", request_id="upgrade-publish")
            self.assertEqual(status, 201)
            with AccountStore(database) as legacy:
                admin = legacy.bootstrap_superadmin("legacy-admin", PASSWORD)
                session = legacy.login("legacy-admin", PASSWORD)["accessToken"]
                # 0.0.9 used the same account/session schema, without the three
                # additive recovery tables introduced by 0.1.0.
                for name in ("account_password_reset_codes", "account_password_reset_limits", "account_security_events"):
                    legacy.conn.execute(f"DROP TABLE {name}")
                tables = [row[0] for row in legacy.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                before = {name: [tuple(row) for row in legacy.conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in tables}
            for _ in range(2):
                with AccountStore(database, existing_only=True) as upgraded:
                    after = {name: [tuple(row) for row in upgraded.conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid')] for name in tables}
                    self.assertEqual(after, before)
                    self.assertEqual(upgraded.authenticate(session)["id"], admin["id"])
                    self.assertEqual(upgraded.conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    for name in ("account_password_reset_codes", "account_password_reset_limits", "account_security_events"):
                        self.assertEqual(upgraded.conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0], 0)
            latest = json.loads(service.public_latest_download("demo", "stable")["body"])
            self.assertEqual(latest["version"], "1.0.14")
            self.assertEqual(latest["manifestSha256"], service.public_download("demo", "1.0.14", "manifest.json")["sha256"])


if __name__ == "__main__":
    unittest.main()

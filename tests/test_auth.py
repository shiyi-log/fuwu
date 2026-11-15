from __future__ import annotations

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

from django.conf import settings  # noqa: E402
from django.test import Client, override_settings  # noqa: E402

from fuwu_service.accounts import get_account_store  # noqa: E402
from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, SQLiteRepository  # noqa: E402
from fuwu_service.server import ApiHandler  # noqa: E402
from release_api import views  # noqa: E402


# Synthetic test values only. Production deployments inject random opaque tokens.
READER = "reader-test-token-" + "a" * 32
ADMIN = "admin-test-token-" + "b" * 32
PERMISSIONS = {READER: ["release.storage.read"], ADMIN: ["release.storage.read", "release.storage.publish", "release.storage.rollback", "superadmin"]}


class SessionTests(unittest.TestCase):
    def test_sessions_expire_reject_tampering_and_follow_current_permissions(self) -> None:
        rbac = RBAC(PERMISSIONS)
        with patch("fuwu_service.rbac.time.time", return_value=1000):
            session, expires_at = rbac.issue_session(ADMIN)
        self.assertEqual(expires_at, 4600)
        with patch("fuwu_service.rbac.time.time", return_value=1001):
            actor, permissions = rbac.authenticate("Bearer " + session)
            self.assertIn("superadmin", permissions)
            self.assertNotIn(ADMIN[:8], actor)
            reduced = RBAC({ADMIN: ["release.storage.read"]})
            self.assertEqual(reduced.authenticate("Bearer " + session)[1], {"release.storage.read"})
            for token in [session[:-1] + ("0" if session[-1] != "0" else "1"), "fuwu-session-v1.invalid.bad"]:
                with self.assertRaises(ReleaseError) as error:
                    rbac.authenticate("Bearer " + token)
                self.assertEqual(error.exception.status, 401)
            with self.assertRaises(ReleaseError):
                RBAC({}).authenticate("Bearer " + session)
        with patch("fuwu_service.rbac.time.time", return_value=4600):
            with self.assertRaises(ReleaseError):
                rbac.authenticate("Bearer " + session)

    def test_sessions_never_contain_management_secret_and_have_distinct_values(self) -> None:
        rbac = RBAC(PERMISSIONS)
        first, _ = rbac.issue_session(READER)
        second, _ = rbac.issue_session(READER)
        self.assertNotEqual(first, second)
        self.assertNotIn(READER, first)
        self.assertNotIn(READER[:8], rbac.authenticate("Bearer " + READER)[0])
        self.assertEqual(rbac.authenticate("Bearer " + first), rbac.authenticate("Bearer " + READER))


class AuthenticationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            "FUWU_AUTH_TOKENS": json.dumps(PERMISSIONS),
            "FUWU_DATABASE": str(Path(self.directory.name) / "django.sqlite3"),
            "FUWU_STAGING_DIR": str(Path(self.directory.name) / "staging"),
            "FUWU_STORAGE": "memory",
        })
        self.environment.start()
        self.secret_key = patch.object(settings, "SECRET_KEY", "test-only-email-reset-key-2026")
        self.secret_key.start()
        views.get_service.cache_clear()
        self.django = Client()
        self.repository = SQLiteRepository(str(Path(self.directory.name) / "http.sqlite3"))
        service = ReleaseService(self.repository, MemoryObjectStore(), staging_dir=Path(self.directory.name) / "http-staging")
        handler = type("AuthTestHandler", (ApiHandler,), {"service": service, "rbac": RBAC(PERMISSIONS)})
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=2)
        self.repository.conn.close()
        views.get_service.cache_clear()
        self.environment.stop()
        self.secret_key.stop()
        self.directory.cleanup()

    def request(self, adapter: str, method: str, path: str, body: dict | None = None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else b"{}"
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        if adapter == "django":
            response = self.django.generic(method, path, data=data, content_type="application/json", **({"HTTP_AUTHORIZATION": "Bearer " + token} if token else {}))
            return response.status_code, response.json(), dict(response.items())
        connection = http.client.HTTPConnection(*self.http.server_address, timeout=3)
        try:
            connection.request(method, path, body=data if method == "POST" else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read()), dict(response.getheaders())
        finally:
            connection.close()

    def test_lightweight_adapter_trusts_forwarded_https_only_when_enabled(self) -> None:
        from types import SimpleNamespace

        from fuwu_service.server import ApiHandler

        handler = SimpleNamespace(
            client_address=("192.0.2.10", 12345),
            headers={"X-Forwarded-Proto": "https"},
            connection=object(),
        )
        with patch.dict(os.environ, {"FUWU_TRUST_X_FORWARDED_PROTO": "0"}):
            self.assertFalse(ApiHandler._request_is_secure(handler))
        with patch.dict(os.environ, {"FUWU_TRUST_X_FORWARDED_PROTO": "true"}):
            self.assertTrue(ApiHandler._request_is_secure(handler))
            handler.headers["X-Forwarded-Proto"] = "https, http"
            self.assertFalse(ApiHandler._request_is_secure(handler))

    def test_password_reset_rejects_public_http_requests(self) -> None:
        requests = [
            ("/api/auth/password-reset/code", {"email": "owner@example.test"}),
            ("/api/auth/password-reset", {
                "email": "owner@example.test", "code": "123456", "newPassword": "New-Account-Password-2026",
            }),
        ]
        for path, body in requests:
            with self.subTest(path=path):
                response = self.django.generic(
                    "POST", path, data=json.dumps(body), content_type="application/json",
                    REMOTE_ADDR="203.0.113.10",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "secure_transport_required")

    def test_login_info_codes_logout_and_release_access_are_compatible(self) -> None:
        for adapter in ["django", "http"]:
            with self.subTest(adapter=adapter):
                status, login, headers = self.request(adapter, "POST", "/api/auth/login", {"token": READER})
                self.assertEqual(status, 200)
                self.assertEqual(login["code"], 0)
                self.assertEqual(headers["Cache-Control"], "no-store")
                session = login["data"]["accessToken"]
                self.assertNotEqual(session, READER)
                self.assertNotIn(READER, json.dumps(login))
                status, info, _ = self.request(adapter, "GET", "/api/user/info", token=session)
                self.assertEqual(status, 200)
                self.assertEqual(info["data"]["roles"], ["release-operator"])
                self.assertEqual(info["data"]["homePath"], "/release-management")
                self.assertNotIn(READER[:8], json.dumps(info))
                status, codes, _ = self.request(adapter, "GET", "/api/auth/codes", token=session)
                self.assertEqual((status, codes["data"]), (200, ["release.storage.read"]))
                self.assertEqual(self.request(adapter, "GET", "/api/projects", token=session)[0], 200)
                self.assertEqual(self.request(adapter, "POST", "/api/release-storage/publish", {}, token=session)[0], 403)
                self.assertEqual(self.request(adapter, "POST", "/api/auth/logout", token=session)[:2], (200, {"code": 0, "data": None}))

    def test_only_explicit_superadmin_receives_super_role(self) -> None:
        for adapter in ["django", "http"]:
            with self.subTest(adapter=adapter):
                _, login, _ = self.request(adapter, "POST", "/api/auth/login", {"token": ADMIN})
                session = login["data"]["accessToken"]
                _, info, _ = self.request(adapter, "GET", "/api/user/info", token=session)
                self.assertEqual(info["data"]["roles"], ["super"])
                _, codes, _ = self.request(adapter, "GET", "/api/auth/codes", token=session)
                self.assertEqual(set(codes["data"]), set(PERMISSIONS[ADMIN]) - {"superadmin"})
                self.assertNotIn("superadmin", codes["data"])

    def test_invalid_credentials_and_old_mock_accounts_fail_without_leaks(self) -> None:
        for adapter in ["django", "http"]:
            with self.subTest(adapter=adapter):
                for body in [{"username": "vben", "password": "123456"}, {"token": "invalid-sensitive-token"}, {"token": READER, "roles": ["super"]}, {"token": {"secret": "invalid"}}]:
                    status, error, _ = self.request(adapter, "POST", "/api/auth/login", body)
                    self.assertEqual(status, 401)
                    self.assertEqual(error["code"], "unauthorized")
                    self.assertEqual(error["message"], "authentication required")
                    self.assertNotIn("invalid-sensitive-token", json.dumps(error))
                    self.assertNotIn(READER, json.dumps(error))
                for path in ["/api/auth/codes", "/api/user/info"]:
                    self.assertEqual(self.request(adapter, "GET", path)[0], 401)
                self.assertEqual(self.request(adapter, "POST", "/api/auth/logout")[0], 401)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="mail.example.test",
        DEFAULT_FROM_EMAIL="fuwu@example.test",
    )
    def test_email_password_reset_on_django_and_lightweight_adapters(self) -> None:
        from django.core import mail

        store = get_account_store(Path(self.directory.name) / "django.sqlite3")
        for adapter in ["django", "http"]:
            with self.subTest(adapter=adapter):
                username = f"{adapter}-owner"
                email = f"{adapter}-owner@example.test"
                if adapter == "django":
                    user = store.bootstrap_superadmin(username, "Old-Account-Password-2026")
                else:
                    user = store.create_user(store.conn.execute("SELECT id FROM account_users ORDER BY created_at LIMIT 1").fetchone()[0], {
                        "username": username, "password": "Old-Account-Password-2026", "roles": ["superadmin"], "project_ids": None,
                    })
                store.set_superadmin_email(username, email)

                status, accepted, _ = self.request(adapter, "POST", "/api/auth/password-reset/code", {"email": email})
                self.assertEqual(status, 200)
                self.assertEqual(accepted["code"], 0)
                self.assertNotIn(email, json.dumps(accepted))
                self.assertNotIn("code", accepted["data"])
                reset_email = mail.outbox[-1]
                self.assertEqual(reset_email.to, [email])
                code = reset_email.body.split("验证码是 ", 1)[1].split("，", 1)[0]
                self.assertEqual(len(code), 6)

                before = len(mail.outbox)
                status, unknown, _ = self.request(adapter, "POST", "/api/auth/password-reset/code", {"email": "missing@example.test"})
                self.assertEqual(status, 200)
                self.assertEqual(unknown, accepted)
                self.assertEqual(len(mail.outbox), before)

                session = store.login(email, "Old-Account-Password-2026")["accessToken"]
                status, result, _ = self.request(adapter, "POST", "/api/auth/password-reset", {
                    "email": email, "code": code, "newPassword": "New-Account-Password-2026",
                })
                self.assertEqual(status, 200)
                self.assertEqual(result["data"]["status"], "password_reset")
                with self.assertRaises(ReleaseError):
                    store.authenticate(session)
                store.login(email, "New-Account-Password-2026")
                status, replay, _ = self.request(adapter, "POST", "/api/auth/password-reset", {
                    "email": email, "code": code, "newPassword": "Third-Account-Password-2026",
                })
                self.assertEqual(status, 400)
                self.assertEqual(replay["code"], "invalid_reset_code")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        EMAIL_HOST="mail.example.test",
        DEFAULT_FROM_EMAIL="fuwu@example.test",
    )
    def test_email_delivery_failure_keeps_generic_response_and_discards_code(self) -> None:
        from unittest.mock import patch

        store = get_account_store(Path(self.directory.name) / "django.sqlite3")
        admin = store.bootstrap_superadmin("mail-failure", "Old-Account-Password-2026")
        store.set_superadmin_email(admin["username"], "mail-failure@example.test")
        with patch("fuwu_service.auth.send_mail", side_effect=RuntimeError("secret email and code must not leak")):
            status, response, _ = self.request("django", "POST", "/api/auth/password-reset/code", {"email": "mail-failure@example.test"})
        self.assertEqual(status, 200)
        self.assertEqual(response["data"]["message"], "如果该邮箱对应有效账号，验证码将发送至邮箱。")
        self.assertNotIn("mail-failure@example.test", json.dumps(response))
        self.assertNotIn("secret email", json.dumps(response))
        self.assertEqual(store.conn.execute("SELECT COUNT(*) FROM account_password_reset_codes").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

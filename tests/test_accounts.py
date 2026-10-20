from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.hashers import PBKDF2PasswordHasher

from fuwu_service import accounts, auth
from fuwu_service.accounts import AccountStore, KNOWN_PERMISSIONS, main
from fuwu_service.core import RBAC, ReleaseError

PASSWORD = "a-correct-account-password-2026"
NEW_PASSWORD = "another-account-password-2026"
DJANGO_DEFAULT_ITERATIONS = PBKDF2PasswordHasher.iterations


class AccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "accounts.sqlite3"
        # Only test work factors are reduced; the service uses Django's actual
        # PBKDF2PasswordHasher defaults in deployment.
        self.iterations = patch.object(PBKDF2PasswordHasher, "iterations", 1000)
        self.iterations.start()
        self.store = AccountStore(self.database)
        self.admin = self.store.bootstrap_superadmin("admin", PASSWORD, "管理员")

    def tearDown(self) -> None:
        self.store.close()
        self.iterations.stop()
        self.directory.cleanup()

    def create(self, username: str = "reader", **fields):
        return self.store.create_user(self.admin["id"], {"username": username, "password": PASSWORD, "roles": ["viewer"], "project_ids": ["sample-app"], **fields})

    def session(self, username: str = "admin", password: str = PASSWORD):
        return self.store.login(username, password)["accessToken"]

    def assert_error(self, code: str, function, *args, **kwargs):
        with self.assertRaises(ReleaseError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_bootstrap_stores_only_pbkdf2_and_has_no_default_account(self) -> None:
        self.assertEqual(self.admin["roles"], ["superadmin"])
        self.assertEqual(set(self.admin["permissions"]), set(KNOWN_PERMISSIONS))
        self.assertIsNone(self.admin["project_ids"])
        self.assertNotIn("password", json.dumps(self.admin))
        row = self.store.conn.execute("SELECT password_hash FROM account_users").fetchone()
        self.assertTrue(row[0].startswith("pbkdf2_sha256$"))
        self.assertTrue(PBKDF2PasswordHasher().verify(PASSWORD, row[0]))
        self.assertNotIn(PASSWORD, row[0])
        self.assert_error("bootstrap_not_allowed", self.store.bootstrap_superadmin, "second-admin", PASSWORD)
        with AccountStore(Path(self.directory.name) / "empty.sqlite3") as empty:
            self.assert_error("unauthorized", empty.login, "admin", PASSWORD)
            self.assertEqual(empty.conn.execute("SELECT COUNT(*) FROM account_users").fetchone()[0], 0)

    def test_session_persists_across_connections_and_database_contains_only_token_hash(self) -> None:
        result = self.store.login("ADMIN", PASSWORD)
        token = result["accessToken"]
        self.assertGreater(result["expiresAt"], int(time.time()))
        row = self.store.conn.execute("SELECT * FROM account_sessions").fetchone()
        self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token, "\n".join(self.store.conn.iterdump()))
        self.assertNotIn(PASSWORD, "\n".join(self.store.conn.iterdump()))
        with AccountStore(self.database) as second:
            self.assertIsNot(second.conn, self.store.conn)
            self.assertEqual(second.conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(second.conn.execute("SELECT COUNT(*) FROM account_schema_migrations").fetchone()[0], 1)
            self.assertEqual(second.authenticate(token)["id"], self.admin["id"])
            second.logout(token)
        self.assert_error("unauthorized", self.store.authenticate, token)
        self.store.logout(token)  # Idempotent revocation.

    def test_default_password_work_factor_matches_django(self) -> None:
        with patch.object(PBKDF2PasswordHasher, "iterations", DJANGO_DEFAULT_ITERATIONS):
            encoded = self.store._password(PASSWORD)
            self.assertEqual(PBKDF2PasswordHasher().decode(encoded)["iterations"], DJANGO_DEFAULT_ITERATIONS)
            self.assertTrue(PBKDF2PasswordHasher().verify(PASSWORD, encoded))

    def test_expired_and_unknown_sessions_fail_without_secret(self) -> None:
        with patch("fuwu_service.accounts.time.time", return_value=1000):
            token = self.session()
        with patch("fuwu_service.accounts.time.time", return_value=4600):
            self.assert_error("unauthorized", self.store.authenticate, token)
        for token in ["fuwu-account-v1.unknown", "bad-token", "x" * 1024]:
            error = self.assert_error("unauthorized", self.store.authenticate, token)
            self.assertNotIn(token, error.message)

    def test_project_scope_empty_denies_scoped_allows_and_only_superadmin_has_null(self) -> None:
        reader = self.create(project_ids=[])
        token = self.session("reader")
        self.assert_error("forbidden", self.store.require_project, token, "sample-app")
        self.store.update_user(self.admin["id"], reader["id"], {"project_ids": ["sample-app"]})
        token = self.session("reader")
        self.store.require_project(token, "sample-app")
        self.assert_error("forbidden", self.store.require_project, token, "another-project")
        self.assert_error("forbidden", self.store.require_project, token, "../sample-app")
        self.assert_error("invalid_account", self.store.update_user, self.admin["id"], reader["id"], {"project_ids": None})
        # Passing forged public fields cannot override the current database scope.
        self.assert_error("forbidden", self.store.require_project, {**reader, "project_ids": None, "roles": ["superadmin"]}, "another-project")
        self.store.require_project(self.session(), "future-project")

    def test_disable_role_permission_scope_changes_immediately_revoke_sessions(self) -> None:
        for index, change in enumerate([
            {"active": False}, {"roles": ["release-manager"]},
            {"permissions": ["logs.read"]}, {"project_ids": ["another-project"]},
            {"username": "renamed-user"},
        ]):
            with self.subTest(change=change):
                user = self.create("reader-" + str(index))
                first = self.session(user["username"])
                second = self.session(user["username"])
                self.store.update_user("user:" + self.admin["id"], user["id"], change)
                self.assert_error("unauthorized", self.store.authenticate, first)
                self.assert_error("unauthorized", self.store.authenticate, second)

    def test_project_scope_uses_registry_identifier_rules_and_fixed_errors(self) -> None:
        reader = self.create(project_ids=["sample-app.api", "future-project"])
        token = self.session("reader")
        self.store.require_project(token, "sample-app.api")
        for invalid in ("a..b", "sample-app.", "sample-app-", "sample-app_", "../sample-app", "Aproject"):
            with self.subTest(project=invalid):
                for function, args in (
                    (self.store.create_user, (self.admin["id"], {"username": "invalid-scope", "password": PASSWORD, "project_ids": [invalid]})),
                    (self.store.update_user, (self.admin["id"], reader["id"], {"project_ids": [invalid]})),
                ):
                    error = self.assert_error("invalid_account", function, *args)
                    self.assertEqual((error.message, error.status), ("account data is invalid", 400))
                    self.assertNotIn(invalid, error.message)
                error = self.assert_error("forbidden", self.store.require_project, token, invalid)
                self.assertEqual(error.message, "permission denied")
        self.assertEqual(self.store.authenticate(token)["project_ids"], ["future-project", "sample-app.api"])

    def test_display_name_change_retains_session_and_reads_new_name(self) -> None:
        user = self.create()
        token = self.session("reader")
        self.store.update_user(self.admin["id"], user["id"], {"displayName": "新的显示名"})
        self.assertEqual(self.store.authenticate(token)["displayName"], "新的显示名")

    def test_password_reset_and_change_revoke_all_sessions(self) -> None:
        user = self.create()
        tokens = [self.session("reader"), self.session("reader")]
        self.store.reset_password(self.admin["id"], user["id"], NEW_PASSWORD)
        for token in tokens:
            self.assert_error("unauthorized", self.store.authenticate, token)
        self.assert_error("unauthorized", self.store.login, "reader", PASSWORD)
        token = self.session("reader", NEW_PASSWORD)
        self.assert_error("unauthorized", self.store.change_password, user["id"], "incorrect", PASSWORD)
        self.store.authenticate(token)
        self.store.change_password(user["id"], NEW_PASSWORD, PASSWORD)
        self.assert_error("unauthorized", self.store.authenticate, token)
        self.session("reader", PASSWORD)

    def test_last_superadmin_cannot_be_deleted_disabled_or_demoted(self) -> None:
        self.assert_error("last_superadmin", self.store.delete_user, self.admin["id"], self.admin["id"])
        self.assert_error("last_superadmin", self.store.update_user, self.admin["id"], self.admin["id"], {"active": False})
        self.assert_error("last_superadmin", self.store.update_user, self.admin["id"], self.admin["id"], {"roles": ["viewer"], "project_ids": []})
        second = self.create("second-admin", roles=["superadmin"], project_ids=None)
        self.assert_error("self_deactivation", self.store.update_user, self.admin["id"], self.admin["id"], {"active": False})
        self.assert_error("self_deactivation", self.store.delete_user, self.admin["id"], self.admin["id"])
        self.store.update_user(self.admin["id"], second["id"], {"active": False})
        self.assert_error("last_superadmin", self.store.update_user, self.admin["id"], self.admin["id"], {"roles": [], "project_ids": []})

    def test_concurrent_superadmin_demotions_preserve_one_active_admin(self) -> None:
        second = self.create("second-admin", roles=["superadmin"], project_ids=None)
        barrier = threading.Barrier(2)

        def demote(user_id):
            with AccountStore(self.database) as connection:
                barrier.wait(timeout=3)
                try:
                    connection.update_user(user_id, user_id, {"roles": ["viewer"], "project_ids": []})
                    return "updated"
                except ReleaseError as error:
                    return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(demote, [self.admin["id"], second["id"]]))
        self.assertEqual(sorted(results), ["last_superadmin", "updated"])
        active_admins = [row for row in self.store.conn.execute("SELECT * FROM account_users WHERE active=1").fetchall() if "superadmin" in json.loads(row["roles_json"])]
        self.assertEqual(len(active_admins), 1)

    def test_non_manager_cannot_manage_and_delegated_manager_cannot_escalate(self) -> None:
        reader = self.create()
        for function, args in [
            (self.store.list_users, (reader["id"],)),
            (self.store.create_user, (reader["id"], {"username": "third", "password": PASSWORD})),
            (self.store.reset_password, (reader["id"], self.admin["id"], NEW_PASSWORD)),
        ]:
            self.assert_error("forbidden", function, *args)
        manager = self.create("scoped-manager", permissions=["users.manage", "release.storage.publish"])
        subordinate = self.store.create_user(manager["id"], {"username": "subordinate", "password": PASSWORD, "project_ids": ["sample-app"]})
        self.assertEqual(subordinate["roles"], ["viewer"])
        for fields in [{"roles": ["superadmin"], "project_ids": None}, {"permissions": ["logs.read"]}, {"project_ids": ["another-project"]}]:
            self.assert_error("forbidden", self.store.create_user, manager["id"], {"username": "over-grant", "password": PASSWORD, **fields})
        self.assert_error("forbidden", self.store.reset_password, manager["id"], self.admin["id"], NEW_PASSWORD)

    def test_delete_restricts_superadmin_and_removes_sessions(self) -> None:
        user = self.create()
        token = self.session("reader")
        self.store.delete_user(self.admin["id"], user["id"])
        self.assert_error("unauthorized", self.store.authenticate, token)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM account_sessions WHERE user_id=?", (user["id"],)).fetchone()[0], 0)

    def test_input_validation_password_bounds_and_duplicate_names(self) -> None:
        for password in ["short", " " * 12, "x" * 257]:
            self.assert_error("invalid_password", self.store.create_user, self.admin["id"], {"username": "invalid-password", "password": password})
        for fields in [
            {"username": "../bad"}, {"active": 1}, {"roles": ["root"]},
            {"permissions": ["superadmin"]}, {"project_ids": ["../bad"]},
            {"credential": "must-not-be-accepted"},
        ]:
            self.assert_error("invalid_account", self.store.create_user, self.admin["id"], {"username": "invalid-fields", "password": PASSWORD, **fields})
        reader = self.create()
        self.assert_error("username_exists", self.store.create_user, self.admin["id"], {"username": "READER", "password": PASSWORD})
        self.assert_error("username_exists", self.store.update_user, self.admin["id"], reader["id"], {"username": "ADMIN"})
        self.assertEqual(self.store.get_user(self.admin["id"], reader["id"])["username"], "reader")

    def test_login_errors_are_uniform_for_unknown_disabled_wrong_and_rate_limited(self) -> None:
        disabled = self.create(active=False)
        errors = []
        for username, password in [("unknown", PASSWORD), ("reader", PASSWORD), ("admin", "incorrect")]:
            error = self.assert_error("unauthorized", self.store.login, username, password)
            errors.append((error.code, error.message, error.status))
        with patch("fuwu_service.accounts.time.time", return_value=10000):
            for _ in range(accounts.LOGIN_MAX_FAILURES):
                error = self.assert_error("unauthorized", self.store.login, "admin", "incorrect")
            error = self.assert_error("unauthorized", self.store.login, "admin", PASSWORD)
            errors.append((error.code, error.message, error.status))
        self.assertTrue(all(value == errors[0] for value in errors))
        with patch("fuwu_service.accounts.time.time", return_value=10301):
            self.session()

    def test_client_throttle_and_account_throttle_persist_across_connections(self) -> None:
        with patch("fuwu_service.accounts.time.time", return_value=1000):
            for index in range(accounts.CLIENT_MAX_FAILURES):
                self.assert_error("unauthorized", self.store.login, "missing-" + str(index), "wrong", client_id="shared-client")
            with AccountStore(self.database) as second:
                self.assert_error("unauthorized", second.login, "admin", PASSWORD, client_id="shared-client")
                second.login("admin", PASSWORD, client_id="another-client")
            for _ in range(accounts.LOGIN_MAX_FAILURES):
                self.assert_error("unauthorized", self.store.login, "admin", "wrong", client_id="attacker")
            self.assert_error("unauthorized", self.store.login, "admin", PASSWORD, client_id="different-client")

    def test_rbac_account_permissions_scope_userinfo_and_revoking_logout(self) -> None:
        reader = self.create()
        rbac = RBAC({"automation-token": ["release.storage.publish"]}, account_store_factory=lambda: self.store)
        result = auth.login(rbac, {"username": "reader", "password": PASSWORD}, client_id="browser")
        token = result["data"]["accessToken"]
        authorization = "Bearer " + token
        actor, permissions = rbac.authenticate(authorization)
        self.assertEqual(actor, "user:" + reader["id"])
        self.assertEqual(permissions, {"release.storage.read"})
        self.assertEqual(rbac.public_user(authorization)["id"], reader["id"])
        self.assertEqual(rbac.project_scope(authorization), ["sample-app"])
        rbac.require_project(authorization, "sample-app")
        self.assert_error("forbidden", rbac.require_project, authorization, "another-project")
        info = auth.user_info(rbac, authorization)["data"]
        self.assertEqual(info["homePath"], "/projects")
        self.assertEqual(info["userId"], reader["id"])
        self.assertEqual(info["realName"], "reader")
        self.assertNotIn(token, json.dumps(info))
        self.assertEqual(auth.access_codes(rbac, authorization)["data"], ["release.storage.read"])
        self.assertIsNone(rbac.public_user("Bearer automation-token"))
        self.assertIsNone(rbac.project_scope("Bearer automation-token"))
        rbac.require_project("Bearer automation-token", "future-project")
        auth.logout(rbac, authorization)
        self.assert_error("unauthorized", rbac.authenticate, authorization)

    def test_rbac_environment_lazy_loading_and_no_implicit_database_for_explicit_tokens(self) -> None:
        with patch("fuwu_service.accounts.get_account_store", side_effect=AssertionError("must not open global database")):
            explicit = RBAC({})
            self.assert_error("unauthorized", explicit.authenticate, "Bearer fuwu-account-v1.unknown")
            self.assert_error("unauthorized", explicit.authenticate, "Bearer unknown")
            from_environment = RBAC.from_env({"FUWU_DATABASE": str(self.database), "FUWU_AUTH_TOKENS": "{}"})
            self.assertIsNone(from_environment._account_store)
        with patch("fuwu_service.accounts.get_account_store", return_value=self.store):
            actor, permissions = from_environment.authenticate("Bearer " + self.session())
            self.assertEqual(actor, "user:" + self.admin["id"])
            self.assertEqual(permissions, set(KNOWN_PERMISSIONS) | {"superadmin"})

    def test_role_catalog_fields_and_safe_bootstrap_cli(self) -> None:
        roles = self.store.role_catalog()
        self.assertEqual({role["id"] for role in roles}, {"superadmin", "release-manager", "viewer"})
        cli_database = str(Path(self.directory.name) / "cli.sqlite3")
        output = io.StringIO()
        with patch("builtins.input", side_effect=["cli-admin", "CLI 管理员"]), patch("getpass.getpass", side_effect=[PASSWORD, PASSWORD]), patch("sys.stdout", output):
            self.assertEqual(main(["bootstrap", "--database", cli_database]), 0)
        self.assertNotIn(PASSWORD, output.getvalue())
        with AccountStore(cli_database) as second:
            second.login("cli-admin", PASSWORD)
        with patch("sys.stderr", output), self.assertRaises(SystemExit):
            main(["bootstrap", "--password", PASSWORD])
        self.assertNotIn(PASSWORD, output.getvalue())


if __name__ == "__main__":
    unittest.main()

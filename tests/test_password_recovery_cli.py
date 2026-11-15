from __future__ import annotations

import contextlib
import getpass
import io
import os
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.contrib.auth.hashers import PBKDF2PasswordHasher

from fuwu_service.accounts import AccountStore, main
from fuwu_service.core import ReleaseError


OLD_PASSWORD = "old-password-recovery-test-2026"
NEW_PASSWORD = "new-password-recovery-test-2026"


class PasswordRecoveryCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "accounts.sqlite3"
        self.iterations = patch.object(PBKDF2PasswordHasher, "iterations", 1000)
        self.iterations.start()
        self.addCleanup(self.iterations.stop)
        self.secret = patch("fuwu_service.accounts.settings.SECRET_KEY", "recovery-cli-test-secret-2026")
        self.secret.start()
        self.addCleanup(self.secret.stop)
        self.store = AccountStore(self.database)
        self.addCleanup(self.store.close)
        self.admin = self.store.bootstrap_superadmin("owner@example.test", OLD_PASSWORD)
        self.session = self.store.login(self.admin["username"], OLD_PASSWORD)["accessToken"]
        self.challenge = self.store.create_password_reset_code(self.admin["username"])

    def snapshot(self):
        return {
            table: [tuple(row) for row in self.store.conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("account_users", "account_sessions", "account_password_reset_codes", "account_security_events")
        }

    def run_cli(self, *, username="owner@example.test", passwords=None, tty=True, inputs=None, database=None):
        output = io.StringIO()
        args = ["reset-password", "--database", str(database or self.database)]
        if username is not None:
            args += ["--username", username]
        with contextlib.redirect_stdout(output), patch("sys.stdin.isatty", return_value=tty), \
                patch("fuwu_service.accounts.getpass.getpass", side_effect=passwords or [NEW_PASSWORD, NEW_PASSWORD]) as hidden, \
                patch("builtins.input", side_effect=inputs or []):
            result = main(args)
        return result, output.getvalue(), hidden

    def test_cli_recovery_revokes_sessions_codes_and_records_audit(self) -> None:
        result, output, _ = self.run_cli(username="OWNER@EXAMPLE.TEST")
        self.assertEqual(result, 0)
        self.assertIn("密码已修改", output)
        self.assertNotIn(NEW_PASSWORD, output)
        self.assertNotIn(OLD_PASSWORD, output)
        with self.assertRaises(ReleaseError):
            self.store.authenticate(self.session)
        with self.assertRaises(ReleaseError):
            self.store.login(self.admin["username"], OLD_PASSWORD)
        self.store.login(self.admin["username"], NEW_PASSWORD)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM account_password_reset_codes").fetchone()[0], 0)
        self.assertIsNotNone(self.challenge)
        with self.assertRaises(ReleaseError):
            self.store.reset_password_with_code(self.admin["username"], self.challenge[1], OLD_PASSWORD)
        row = self.store.conn.execute("SELECT action,actor,user_id,outcome FROM account_security_events ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(tuple(row), ("password_reset_completed", "host-cli", self.admin["id"], "host_reset"))
        encoded = self.store.conn.execute("SELECT password_hash FROM account_users WHERE id=?", (self.admin["id"],)).fetchone()[0]
        self.assertTrue(encoded.startswith("pbkdf2_sha256$"))
        self.assertNotIn(NEW_PASSWORD, "\n".join(self.store.conn.iterdump()))

    def test_cli_recovery_can_prompt_for_username(self) -> None:
        result, _, _ = self.run_cli(username=None, inputs=[" OWNER@EXAMPLE.TEST "])
        self.assertEqual(result, 0)
        self.store.login(self.admin["username"], NEW_PASSWORD)

    def test_invalid_password_confirmation_or_cancellation_preserves_credentials(self) -> None:
        cases = [
            [NEW_PASSWORD, "different-password-2026"],
            ["short", "short"],
            [KeyboardInterrupt()],
            [EOFError()],
        ]
        for passwords in cases:
            with self.subTest(passwords=type(passwords[0]).__name__):
                before = self.snapshot()
                result, output, _ = self.run_cli(passwords=passwords)
                self.assertEqual(result, 1)
                self.assertEqual(self.snapshot(), before)
                self.assertNotIn(NEW_PASSWORD, output)
                self.assertNotIn(OLD_PASSWORD, output)
                self.store.authenticate(self.session)

    def test_noninteractive_input_is_rejected_before_password_prompt(self) -> None:
        before = self.snapshot()
        result, _, hidden = self.run_cli(tty=False)
        self.assertEqual(result, 1)
        hidden.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_getpass_fallback_warning_is_rejected_without_credential_changes(self) -> None:
        before = self.snapshot()

        def unsafe_prompt(_prompt):
            warnings.warn("terminal cannot hide input", getpass.GetPassWarning)
            return NEW_PASSWORD

        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("sys.stdin.isatty", return_value=True), \
                patch("fuwu_service.accounts.getpass.getpass", side_effect=unsafe_prompt):
            result = main(["reset-password", "--database", str(self.database), "--username", self.admin["username"]])
        self.assertEqual(result, 1)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(NEW_PASSWORD, output.getvalue())

    def test_unknown_and_disabled_accounts_do_not_prompt_or_change_credentials(self) -> None:
        disabled = self.store.create_user(self.admin["id"], {
            "username": "disabled@example.test", "password": OLD_PASSWORD,
            "active": False, "roles": ["viewer"], "project_ids": [],
        })
        for username in ("missing@example.test", disabled["username"]):
            with self.subTest(username=username):
                before = self.snapshot()
                result, _, hidden = self.run_cli(username=username)
                self.assertEqual(result, 1)
                hidden.assert_not_called()
                self.assertEqual(self.snapshot(), before)

    def test_missing_database_is_not_created(self) -> None:
        missing = Path(self.directory.name) / "missing.sqlite3"
        result, _, hidden = self.run_cli(database=missing)
        self.assertEqual(result, 1)
        self.assertFalse(missing.exists())
        hidden.assert_not_called()

    def test_uninitialized_database_is_not_given_account_tables(self) -> None:
        unrelated = Path(self.directory.name) / "unrelated.sqlite3"
        with sqlite3.connect(unrelated) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        result, _, hidden = self.run_cli(database=unrelated)
        self.assertEqual(result, 1)
        hidden.assert_not_called()
        with sqlite3.connect(unrelated) as connection:
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        self.assertEqual(tables, [("unrelated",)])

    def test_plaintext_password_argument_is_rejected_without_echo(self) -> None:
        before = self.snapshot()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main(["reset-password", "--database", str(self.database), "--password", NEW_PASSWORD])
        self.assertEqual(error.exception.code, 2)
        self.assertNotIn(NEW_PASSWORD, stderr.getvalue())
        self.assertEqual(self.snapshot(), before)

    def test_database_can_be_selected_from_environment(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"FUWU_DATABASE": str(self.database)}), \
                contextlib.redirect_stdout(output), patch("sys.stdin.isatty", return_value=True), \
                patch("fuwu_service.accounts.getpass.getpass", side_effect=[NEW_PASSWORD, NEW_PASSWORD]):
            result = main(["reset-password", "--username", self.admin["username"]])
        self.assertEqual(result, 0)
        self.store.login(self.admin["username"], NEW_PASSWORD)

    def test_host_reset_rejects_a_different_target_id(self) -> None:
        before = self.snapshot()
        with self.assertRaises(ReleaseError):
            self.store.reset_password_from_host(self.admin["username"], NEW_PASSWORD, expected_user_id="stale-target-id")
        self.assertEqual(self.snapshot(), before)

    def test_cli_does_not_reset_a_replacement_account_created_during_password_input(self) -> None:
        replacement = None

        def replace_target(_prompt):
            nonlocal replacement
            if replacement is None:
                self.store.conn.execute("UPDATE account_users SET username=? WHERE id=?", ("original-owner@example.test", self.admin["id"]))
                replacement = self.store.create_user(self.admin["id"], {
                    "username": self.admin["username"], "password": OLD_PASSWORD,
                    "roles": ["viewer"], "project_ids": [],
                })
            return NEW_PASSWORD

        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("sys.stdin.isatty", return_value=True), \
                patch("fuwu_service.accounts.getpass.getpass", side_effect=replace_target):
            result = main(["reset-password", "--database", str(self.database), "--username", self.admin["username"]])
        self.assertEqual(result, 1)
        self.assertIsNotNone(replacement)
        self.store.login(self.admin["username"], OLD_PASSWORD)
        self.store.login("original-owner@example.test", OLD_PASSWORD)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM account_security_events WHERE actor='host-cli'").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

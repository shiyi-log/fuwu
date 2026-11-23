from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fuwu_service.accounts import AccountStore
from fuwu_service.runtime_config import bootstrap_from_config, config_path, github_credentials_file, superadmin_bootstrap_config
from fuwu_service.core import ReleaseError
from fuwu_service.github_auth import github_credentials_path


class RuntimeConfigTests(unittest.TestCase):
    def test_checked_in_config_has_non_secret_superadmin_defaults(self):
        settings = superadmin_bootstrap_config(Path(__file__).parents[1] / "fuwu.config.json")
        self.assertEqual(settings["username"], "admin")
        self.assertEqual(settings["passwordEnv"], "FUWU_SUPERADMIN_PASSWORD")

    def test_bootstrap_config_reads_password_only_from_environment(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "fuwu.config.json"
            config.write_text(json.dumps({"superadmin": {"username": "rootadmin", "displayName": "Root", "passwordEnv": "TEST_ADMIN_PASSWORD"}}), encoding="utf-8")
            database = root / "accounts.sqlite3"
            with self.assertRaises(ReleaseError) as missing:
                bootstrap_from_config(database, config=config, env={})
            self.assertEqual(missing.exception.code, "superadmin_password_required")
            user = bootstrap_from_config(database, config=config, env={"TEST_ADMIN_PASSWORD": "Strong-Admin-Password-2026"})
            self.assertEqual(user["username"], "rootadmin")
            with AccountStore(database) as store:
                session = store.login("rootadmin", "Strong-Admin-Password-2026")
                self.assertEqual(store.authenticate(session["accessToken"])["roles"], ["superadmin"])

    def test_config_rejects_unknown_password_environment_name(self):
        with TemporaryDirectory() as directory:
            config = Path(directory) / "fuwu.config.json"
            config.write_text(json.dumps({"superadmin": {"username": "admin", "passwordEnv": "password"}}), encoding="utf-8")
            with self.assertRaises(ReleaseError) as error:
                superadmin_bootstrap_config(config)
            self.assertEqual(error.exception.code, "invalid_config")

    def test_github_credentials_env_relative_path_is_project_root_relative(self):
        path = github_credentials_path({"FUWU_GITHUB_CREDENTIALS_FILE": "secrets/custom-github.json"})
        self.assertEqual(path, Path(__file__).parents[1] / "secrets/custom-github.json")

    def test_default_config_and_credentials_use_stable_installation_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "fuwu.config.json").write_text(json.dumps({
                "github": {"credentialFile": "secrets/existing-github.json"},
            }), encoding="utf-8")
            env = {"FUWU_INSTALL_ROOT": str(root)}
            self.assertEqual(config_path(env=env), root / "fuwu.config.json")
            self.assertEqual(github_credentials_file(env=env), root / "secrets/existing-github.json")
            self.assertEqual(github_credentials_path(env), root / "secrets/existing-github.json")

    def test_config_overrides_keep_priority_under_installation_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            explicit = root / "custom/config.json"
            env = {"FUWU_INSTALL_ROOT": str(root), "FUWU_CONFIG_FILE": str(explicit)}
            self.assertEqual(config_path(env=env), explicit)
            self.assertEqual(config_path(root / "argument.json", env=env), root / "argument.json")
            env["FUWU_CONFIG_FILE"] = "config/custom.json"
            self.assertEqual(config_path(env=env), root / "config/custom.json")

    def test_credential_overrides_keep_priority_under_installation_root(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            env = {
                "FUWU_INSTALL_ROOT": str(root),
                "FUWU_GITHUB_CREDENTIALS_FILE": "private/override.json",
                "FUWU_CONFIG_FILE": str(root / "missing.json"),
            }
            expected = root / "private/override.json"
            self.assertEqual(github_credentials_file(env=env), expected)
            self.assertEqual(github_credentials_path(env), expected)
            env["FUWU_GITHUB_CREDENTIALS_FILE"] = str(root / "absolute.json")
            self.assertEqual(github_credentials_path(env), root / "absolute.json")

    def test_no_installation_root_preserves_source_relative_config_defaults(self):
        root = Path(__file__).parents[1]
        self.assertEqual(config_path(env={}), root / "fuwu.config.json")
        self.assertEqual(config_path("config/custom.json", env={}), root / "config/custom.json")
        self.assertEqual(github_credentials_file(env={}), root / "secrets/github_accounts.json")

    def test_relative_installation_root_is_rejected(self):
        env = {"FUWU_INSTALL_ROOT": "relative/root"}
        with self.assertRaisesRegex(ReleaseError, "installation root must be absolute"):
            config_path(env=env)
        env["FUWU_GITHUB_CREDENTIALS_FILE"] = "secrets/github.json"
        with self.assertRaisesRegex(ReleaseError, "installation root must be absolute"):
            github_credentials_path(env)

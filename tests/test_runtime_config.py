from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fuwu_service.accounts import AccountStore
from fuwu_service.runtime_config import bootstrap_from_config, superadmin_bootstrap_config
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

"""Offline contract tests for the public object-storage manifest shape."""
from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "fixtures"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^[0-9]\.[0-9]\.[0-9]$")


class ReleaseStorageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((FIXTURES / "release-v1-manifest.json").read_text())
        cls.latest = json.loads((FIXTURES / "release-v1-latest.json").read_text())

    def test_manifest_has_v1_identity_and_final_release_flags(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["project"], "sample-app")
        self.assertTrue(VERSION.fullmatch(manifest["version"]))
        self.assertEqual(manifest["tag"], f"v{manifest['version']}")
        self.assertTrue(REVISION.fullmatch(manifest["revision"]))
        self.assertIsInstance(manifest["releaseId"], int)
        self.assertFalse(manifest["draft"])
        self.assertFalse(manifest["prerelease"])
        self.assertEqual(manifest["channel"], "stable")
        self.assertEqual(manifest["verificationSource"], "release-storage")

    def test_assets_use_the_immutable_version_prefix(self) -> None:
        manifest = self.manifest
        prefix = f"releases/{manifest['project']}/v{manifest['version']}/"
        self.assertEqual(set(manifest["assets"]), {"docker", "python"})
        for asset in manifest["assets"].values():
            self.assertTrue(asset["key"].startswith(prefix))
            self.assertEqual(asset["key"].rsplit("/", 1)[-1], asset["name"])
            self.assertNotIn("..", asset["key"])
            self.assertNotIn("\\", asset["key"])
            self.assertGreater(asset["size"], 0)
            self.assertRegex(asset["sha256"], HEX64)

    def test_checksums_and_latest_reference_the_manifest(self) -> None:
        manifest, latest = self.manifest, self.latest
        self.assertEqual(manifest["checksums"]["name"], "SHA256SUMS")
        self.assertRegex(manifest["checksums"]["sha256"], HEX64)
        self.assertEqual(set(manifest["checksums"]["entries"]), {asset["name"] for asset in manifest["assets"].values()})
        for digest in manifest["checksums"]["entries"].values():
            self.assertRegex(digest, HEX64)
        self.assertEqual(latest["schemaVersion"], 1)
        self.assertEqual(latest["project"], manifest["project"])
        self.assertEqual(latest["channel"], manifest["channel"])
        self.assertEqual(latest["version"], manifest["version"])
        self.assertEqual(latest["tag"], manifest["tag"])
        self.assertEqual(latest["revision"], manifest["revision"])
        self.assertEqual(latest["manifestKey"], "releases/sample-app/v0.0.1/manifest.json")
        self.assertTrue(latest["immutable"])

    def test_fixture_contains_no_credentials_or_environment_paths(self) -> None:
        encoded = json.dumps({"manifest": self.manifest, "latest": self.latest}).lower()
        for forbidden in ("aws_secret_access_key", "secretaccesskey", "session_token", "authorization", "bearer ", "password", "private_key", "/opt/", "/users/"):
            self.assertNotIn(forbidden, encoded)

    def test_invalid_object_keys_are_rejected(self) -> None:
        for key in ("../manifest.json", "/tmp/asset.tar.gz", "releases/sample-app/v0.0.1/../other/a", "releases\\sample-app\\asset.tar.gz"):
            valid = key.startswith("releases/sample-app/v0.0.1/") and ".." not in key and "\\" not in key
            self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()

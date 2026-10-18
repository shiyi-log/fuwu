"""Offline contract tests for the fuwu -> Heyue object-storage v1 wire shape."""
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


class HeyueObjectStorageContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((FIXTURES / "heyue-v1-manifest.json").read_text())
        cls.latest = json.loads((FIXTURES / "heyue-v1-latest.json").read_text())

    def test_manifest_has_v1_identity_and_release_flags(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["project"], "heyue")
        self.assertEqual(manifest["repository"], "shiyi-log/heyue")
        self.assertTrue(VERSION.fullmatch(manifest["version"]))
        self.assertIsNone(VERSION.fullmatch("0.0.10"))
        self.assertEqual(manifest["tag"], f"v{manifest['version']}")
        self.assertTrue(REVISION.fullmatch(manifest["revision"]))
        self.assertIsInstance(manifest["releaseId"], int)
        self.assertGreater(manifest["releaseId"], 0)
        self.assertFalse(manifest["draft"])
        self.assertFalse(manifest["prerelease"])
        self.assertEqual(manifest["channel"], "stable")
        self.assertEqual(manifest["verificationSource"], "heyue-release-storage")

    def test_manifest_contains_platform_assets_under_immutable_prefix(self) -> None:
        manifest = self.manifest
        prefix = f"releases/{manifest['project']}/v{manifest['version']}/"
        self.assertEqual(set(manifest["assets"]), {"docker", "python"})
        for platform, asset in manifest["assets"].items():
            self.assertEqual(asset["key"].split("/")[:-1], prefix.rstrip("/").split("/"))
            self.assertEqual(asset["key"].rsplit("/", 1)[-1], asset["name"])
            self.assertNotIn("..", asset["key"])
            self.assertNotIn("\\", asset["key"])
            self.assertFalse(asset["name"].startswith("/"))
            self.assertGreater(asset["size"], 0)
            self.assertRegex(asset["sha256"], HEX64)
            expected_name = (
                f"heyue-docker-{manifest['version']}-linux-amd64.tar.gz"
                if platform == "docker" else f"heyue-python-{manifest['version']}.tar.gz"
            )
            self.assertEqual(asset["name"], expected_name)

    def test_checksums_and_latest_pointer_are_immutable_references(self) -> None:
        manifest = self.manifest
        latest = self.latest
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
        self.assertEqual(latest["manifestKey"], "releases/heyue/v0.0.1/manifest.json")
        self.assertTrue(latest["immutable"])
        self.assertEqual(latest["verificationSource"], "heyue-release-storage")

    def test_fixture_does_not_contain_secret_or_private_path_material(self) -> None:
        encoded = json.dumps({"manifest": self.manifest, "latest": self.latest}).lower()
        for forbidden in ("aws_secret_access_key", "secretaccesskey", "session_token", "authorization", "bearer ", "password", "private_key", "/root/"):
            self.assertNotIn(forbidden, encoded)

    def test_invalid_paths_are_rejected_by_contract_predicate(self) -> None:
        asset = self.manifest["assets"]["python"]
        for bad_key in ("../manifest.json", "/tmp/asset.tar.gz", "releases/heyue/v0.0.1/../v1.0.1/a", "releases\\heyue\\v0.0.1\\a"):
            with self.subTest(bad_key=bad_key):
                valid = bad_key.startswith("releases/heyue/v0.0.1/") and ".." not in bad_key and "\\" not in bad_key
                self.assertFalse(valid)
        self.assertEqual(asset["key"].split("/")[-1], asset["name"])


if __name__ == "__main__":
    unittest.main()

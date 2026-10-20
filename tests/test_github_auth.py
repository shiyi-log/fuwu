from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import urllib.error
from pathlib import Path

from fuwu_service.github_auth import GitHubAuthorizationStore, verify_github_token
from fuwu_service.core import ReleaseError


class _Response:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit=-1):
        return self.payload


class _Opener:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.request = None

    def open(self, request, timeout=0):
        self.request = request
        if self.error:
            raise self.error
        return self.response


class GitHubTokenVerificationTests(unittest.TestCase):
    def test_verification_returns_safe_identity_and_sends_bearer_header(self):
        opener = _Opener(_Response({"login": "release-bot", "node_id": "MDQ6VXNlcjE="}, {"X-OAuth-Scopes": "repo, read:org"}))
        identity = verify_github_token("github_pat_example", opener=opener)
        self.assertEqual(identity, {"login": "release-bot", "node_id": "MDQ6VXNlcjE=", "scopes": ["read:org", "repo"]})
        self.assertEqual(opener.request.full_url, "https://api.github.com/user")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer github_pat_example")

    def test_invalid_or_rejected_tokens_use_fixed_errors(self):
        with self.assertRaisesRegex(ReleaseError, "GitHub authorization is invalid"):
            verify_github_token("bad token")
        opener = _Opener(error=urllib.error.HTTPError("https://api.github.com/user", 401, "bad", {}, None))
        with self.assertRaisesRegex(ReleaseError, "GitHub authorization was rejected"):
            verify_github_token("github_pat_example", opener=opener)


class GitHubAuthorizationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "private" / "github.json"
        self.store = GitHubAuthorizationStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_file_is_empty_and_authorization_is_project_scoped(self):
        self.assertEqual(self.store.get_public("sample"), {"authorized": False, "login": None, "node_id": None, "scopes": [], "authorizedAt": None})
        result = self.store.authorize("sample", "github_pat_example", {"login": "release-bot", "node_id": "MDQ6VXNlcjE=", "scopes": ["repo"]})
        self.assertTrue(result["authorized"])
        self.assertEqual(result["login"], "release-bot")
        self.assertEqual(self.store.get_token("sample"), "github_pat_example")
        self.assertIsNone(self.store.get_token("other"))
        public = json.dumps(result)
        self.assertNotIn("github_pat_example", public)
        raw = self.path.read_text(encoding="utf-8")
        self.assertIn("github_pat_example", raw)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_revoke_removes_token_and_file_rejects_weak_permissions(self):
        self.store.authorize("sample", "github_pat_example", {"login": "release-bot", "node_id": "MDQ6VXNlcjE=", "scopes": []})
        self.store.revoke("sample")
        self.assertIsNone(self.store.get_token("sample"))
        self.assertFalse(self.store.get_public("sample")["authorized"])
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(ReleaseError, "GitHub credential storage is unavailable"):
            self.store.get_public("sample")

    def test_malformed_identity_does_not_write_secret(self):
        with self.assertRaisesRegex(ReleaseError, "GitHub identity is invalid"):
            self.store.authorize("sample", "github_pat_example", {"login": "bad user", "node_id": "id", "scopes": []})
        self.assertFalse(self.path.exists())

    def test_tampered_record_fails_closed_without_exposing_its_token(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text(json.dumps({"version": 1, "projects": {"sample": {"token": "github_pat_secret", "login": "bad user", "node_id": "id", "scopes": [], "authorizedAt": "now"}}}), encoding="utf-8")
        os.chmod(self.path, 0o600)
        with self.assertRaisesRegex(ReleaseError, "GitHub credential storage is unavailable"):
            self.store.get_public("sample")


if __name__ == "__main__":
    unittest.main()

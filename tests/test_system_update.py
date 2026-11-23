from __future__ import annotations

import json
import datetime as dt
import os
import sqlite3
import stat
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from fuwu_service.errors import ReleaseError
from fuwu_service.system_update import (
    SYSTEM_UPDATE_LATEST_URL,
    SYSTEM_UPDATE_RESPONSE_MAX_BYTES,
    SYSTEM_UPDATE_TIMEOUT,
    _NoRedirectHandler,
    _install_root,
    _preflight_install,
    _require_protected_tree,
    _reconcile_active_job_unlocked,
    _launch_worker,
    _sqlite_backup,
    __version__,
    check_system_update,
)


class _Response:
    def __init__(self, body: bytes, *, headers: dict[str, str] | None = None, status: int = 200):
        self.body = body
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int = -1) -> bytes:
        return self.body


class _Opener:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, request, timeout=0):
        self.requests.append((request, timeout))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _release(**overrides):
    value = {
        "tag_name": "v0.1.10",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-16T12:34:56Z",
        "body": "Release notes\n\x00safe",
        "html_url": "https://evil.example/release",
        "assets": [
            {"name": "fuwu-0.1.10.tar.gz", "size": 123, "digest": "sha256:" + "A" * 64,
             "browser_download_url": "https://evil.example/download"},
            {"name": "SHA256SUMS", "size": 64, "digest": None,
             "browser_download_url": "https://evil.example/checksums"},
        ],
    }
    value.update(overrides)
    return value


class SystemUpdateTests(unittest.TestCase):
    def _check(self, payload, *, current="0.1.9", headers=None):
        body = json.dumps(payload).encode()
        opener = _Opener(_Response(body, headers=headers or {"Content-Length": str(len(body))}))
        with patch("fuwu_service.system_update.urllib.request.build_opener", return_value=opener):
            result = check_system_update(current)
        return result, opener

    def test_returns_sanitized_fixed_release_metadata_and_numeric_comparison(self):
        result, opener = self._check(_release())
        self.assertEqual(result["currentVersion"], "0.1.9")
        self.assertEqual(result["latestVersion"], "0.1.10")
        self.assertTrue(result["updateAvailable"])
        self.assertEqual(result["tag"], "v0.1.10")
        self.assertEqual(result["publishedAt"], "2026-11-16T12:34:56Z")
        self.assertEqual(result["releaseUrl"], "https://github.com/shiyi-log/fuwu/releases/tag/v0.1.10")
        self.assertNotIn("evil.example", json.dumps(result))
        self.assertEqual(result["notes"], "Release notes\nsafe")
        self.assertEqual(result["assets"][0]["digest"], "a" * 64)
        self.assertNotIn("browser_download_url", result["assets"][0])
        self.assertIsNone(result["assets"][1]["digest"])
        self.assertEqual(opener.requests[0][0].full_url, SYSTEM_UPDATE_LATEST_URL)
        self.assertEqual(opener.requests[0][1], SYSTEM_UPDATE_TIMEOUT)

    def test_current_equal_or_newer_is_not_available(self):
        self.assertFalse(self._check(_release(), current="0.1.10")[0]["updateAvailable"])
        self.assertFalse(self._check(_release(), current="0.2.0")[0]["updateAvailable"])

    def test_rejects_non_stable_or_malformed_tag(self):
        for overrides, message in (
            ({"draft": True}, "not a stable"),
            ({"prerelease": True}, "not a stable"),
            ({"tag_name": "v01.1.0"}, "metadata is invalid"),
            ({"tag_name": "v1.0.0-rc1"}, "metadata is invalid"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ReleaseError, message):
                    self._check(_release(**overrides))

    def test_rejects_bad_payload_fields(self):
        cases = [
            {"published_at": "2026-11-16T12:34:56"},
            {"published_at": "not-a-date"},
            {"assets": "not-a-list"},
            {"assets": [{"name": "../secret", "size": 1}]},
            {"assets": [{"name": "ok.tar.gz", "size": True}]},
            {"assets": [{"name": "ok.tar.gz", "size": 1, "digest": "md5:" + "a" * 32}]},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ReleaseError, "metadata is invalid"):
                    self._check(_release(**overrides))

    def test_limits_response_size_and_notes(self):
        body = b"{" + b"x" * SYSTEM_UPDATE_RESPONSE_MAX_BYTES
        opener = _Opener(_Response(body))
        with patch("fuwu_service.system_update.urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(ReleaseError, "response is too large"):
                check_system_update("0.1.0")

        with self.assertRaisesRegex(ReleaseError, "notes are too large"):
            self._check(_release(body="x" * 8193))

    def test_network_failures_are_safe(self):
        for error in (urllib.error.URLError("offline"), TimeoutError("slow")):
            opener = _Opener(error)
            with patch("fuwu_service.system_update.urllib.request.build_opener", return_value=opener):
                with self.assertRaisesRegex(ReleaseError, "metadata is unavailable") as raised:
                    check_system_update("0.1.0")
            self.assertNotIn("offline", raised.exception.message)

    def test_rejects_redirects_http_errors_bad_json_and_inconsistent_lengths(self):
        handler = _NoRedirectHandler()
        with self.assertRaisesRegex(ReleaseError, "redirected unexpectedly"):
            handler.redirect_request(
                urllib.request.Request(SYSTEM_UPDATE_LATEST_URL),
                None,
                302,
                "Found",
                {},
                "https://example.invalid/releases/latest",
            )

        cases = [
            (_Response(b"{}", status=503), "metadata is unavailable"),
            (_Response(b"not json"), "metadata is unavailable"),
            (_Response(b"{}", headers={"Content-Length": "1"}), "metadata response is invalid"),
        ]
        for response, message in cases:
            with self.subTest(message=message):
                opener = _Opener(response)
                with patch("fuwu_service.system_update.urllib.request.build_opener", return_value=opener):
                    with self.assertRaisesRegex(ReleaseError, message):
                        check_system_update("0.1.0")

    def test_invalid_current_version_is_rejected_before_network(self):
        with patch("fuwu_service.system_update.urllib.request.build_opener") as build_opener:
            with self.assertRaisesRegex(ReleaseError, "version format is invalid"):
                check_system_update("v0.1.0")
        build_opener.assert_not_called()

    def test_worker_imports_active_release_and_runs_from_current_symlink(self):
        self.assertRegex(__version__, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        root = Path("/srv/fuwu")
        with (
            patch("fuwu_service.system_update._install_root", return_value=root),
            patch("fuwu_service.system_update.shutil.which", side_effect=[
                "/usr/bin/systemd-run", "/usr/bin/systemctl", "/usr/bin/uv",
            ]),
            patch.dict("os.environ", {"FUWU_DATABASE": "/srv/fuwu/fuwu.sqlite3", "FUWU_PORT": "8088"}, clear=False),
            patch("fuwu_service.system_update.subprocess.run") as run,
        ):
            _launch_worker("a" * 32, "v0.1.1")

        command = run.call_args.args[0]
        self.assertIn("--property=WorkingDirectory=/srv/fuwu/current", command)
        self.assertNotIn("--property=EnvironmentFile=-/srv/fuwu/.env", command)
        self.assertIn("--setenv=FUWU_INSTALL_ROOT=/srv/fuwu", command)
        self.assertIn("--setenv=FUWU_DATABASE=/srv/fuwu/fuwu.sqlite3", command)
        self.assertEqual(run.call_args.kwargs["cwd"], "/srv/fuwu/current")

    def test_install_root_must_be_absolute_when_configured(self):
        with patch.dict(os.environ, {"FUWU_INSTALL_ROOT": "relative/path"}):
            with self.assertRaisesRegex(ReleaseError, "install root must be absolute"):
                _install_root()

    def test_preflight_requires_static_dist_to_follow_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "releases" / "v0.1.0"
            dist = release / "frontend/apps/web-antd/dist"
            dist.mkdir(parents=True)
            (release / "fuwu-update.json").write_text(json.dumps({
                "project": "fuwu", "version": "0.1.0", "tag": "v0.1.0",
            }), encoding="utf-8")
            (dist / "index.html").write_text("ok", encoding="utf-8")
            (root / "current").symlink_to("releases/v0.1.0")
            public_dist = root / "frontend/apps/web-antd/dist"
            public_dist.parent.mkdir(parents=True)
            public_dist.symlink_to("../../../current/frontend/apps/web-antd/dist")

            with (
                patch("fuwu_service.system_update._is_root_owned", return_value=True),
                patch("fuwu_service.system_update._require_protected_tree"),
            ):
                self.assertEqual(_preflight_install(root), ("releases/v0.1.0", "0.1.0"))
                public_dist.unlink()
                public_dist.symlink_to(str(dist))
                with self.assertRaisesRegex(ReleaseError, "bootstrap is incomplete"):
                    _preflight_install(root)

    def test_reconciles_explicitly_stopped_worker(self):
        state = {
            "activeJobId": "a" * 32,
            "jobs": {
                "a" * 32: {
                    "jobId": "a" * 32,
                    "status": "queued",
                    "message": "update queued",
                "updatedAt": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                },
            },
        }
        completed = type("Completed", (), {"stdout": "inactive\n"})()
        with patch("fuwu_service.system_update.subprocess.run", return_value=completed):
            _reconcile_active_job_unlocked(state)
        self.assertIsNone(state["activeJobId"])
        self.assertEqual(state["jobs"]["a" * 32]["status"], "failed")
        self.assertEqual(state["jobs"]["a" * 32]["errorCode"], "system_update_worker_lost")

    def test_reconciler_keeps_recent_queued_worker_during_systemd_startup(self):
        state = {
            "activeJobId": "b" * 32,
            "jobs": {"b" * 32: {
                "status": "queued",
                "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            }},
        }
        completed = type("Completed", (), {"stdout": "inactive\n"})()
        with patch("fuwu_service.system_update.subprocess.run", return_value=completed):
            self.assertFalse(_reconcile_active_job_unlocked(state))
        self.assertEqual(state["activeJobId"], "b" * 32)
        self.assertEqual(state["jobs"]["b" * 32]["status"], "queued")

    def test_backup_is_integrity_checked_and_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "source.sqlite3"
            with sqlite3.connect(database) as conn:
                conn.execute("CREATE TABLE sample (value TEXT)")
                conn.execute("INSERT INTO sample VALUES ('ok')")
            with patch.dict(os.environ, {"FUWU_DATABASE": str(database)}):
                backup = _sqlite_backup(root, "c" * 32)
            self.assertEqual(stat.S_IMODE(backup.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            with sqlite3.connect(backup) as conn:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_active_release_tree_rejects_group_or_world_writable_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fuwu_service" / "module.py"
            source.parent.mkdir()
            source.write_text("pass\n", encoding="utf-8")
            _require_protected_tree(root)
            source.chmod(0o666)
            with self.assertRaisesRegex(OSError, "unprotected"):
                _require_protected_tree(root)

    def test_active_release_tree_allows_root_owned_runtime_symlinks(self):
        # Linux /tmp is world-writable. The success fixture must have protected
        # ancestors just like the runtime path accepted in production.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            target = root / "python"
            target.write_text("#!/bin/sh\n", encoding="utf-8")
            link = root / "bin"
            link.symlink_to(target)
            _require_protected_tree(root)

            target.chmod(0o666)
            with self.assertRaisesRegex(OSError, "symlink target is unprotected"):
                _require_protected_tree(root)

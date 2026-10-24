from __future__ import annotations

import hashlib
import http.client
import json
import os
import pty
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import Client  # noqa: E402
from django.urls import resolve  # noqa: E402

from fuwu_service.core import MemoryObjectStore, RBAC, ReleaseError, ReleaseService, SQLiteRepository, safe_digest  # noqa: E402
from fuwu_service.server import ApiHandler  # noqa: E402
from release_api import views  # noqa: E402


class PublicLatestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = MemoryObjectStore()
        self.service = ReleaseService(SQLiteRepository(root / "db.sqlite3"), self.store, staging_dir=root / "staging")
        self.addCleanup(self.service.repository.conn.close)
        self.addCleanup(self.directory.cleanup)

        body = b"latest asset"
        digest = hashlib.sha256(body).hexdigest()
        job = root / "staging" / "job-1"
        job.mkdir()
        (job / "heyue-python-1.0.2.tar.gz").write_bytes(body)
        manifest = {
            "schemaVersion": 1,
            "repository": "shiyi-log/heyue",
            "version": "1.0.2",
            "tag": "v1.0.2",
            "revision": "1" * 40,
            "releaseId": 1002,
            "draft": False,
            "prerelease": False,
            "assets": {"python": [{"path": "heyue-python-1.0.2.tar.gz", "size": len(body), "sha256": digest}]},
        }
        (job / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (job / "SHA256SUMS").write_text(f"{digest}  heyue-python-1.0.2.tar.gz\n", encoding="utf-8")
        status, response = self.service.publish(
            {"project": "heyue", "version": "1.0.2", "channel": "stable", "staging_ref": "job-1"},
            actor="test",
            request_id="publish-1",
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["status"], "published")

    def test_service_binds_latest_to_active_published_release(self) -> None:
        descriptor = self.service.public_latest_download("heyue", "stable")
        pointer = json.loads(descriptor["body"])
        self.assertEqual(pointer["project"], "heyue")
        self.assertEqual(pointer["channel"], "stable")
        self.assertEqual(pointer["version"], "1.0.2")
        self.assertEqual(pointer["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
        self.assertEqual(descriptor["sha256"], safe_digest(descriptor["body"]))

        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "beta")

        pointer["version"] = "0.0.1"
        pointer_body = json.dumps(pointer, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.store.put_object(descriptor["key"], pointer_body, sha256=safe_digest(pointer_body))
        with self.assertRaisesRegex(ReleaseError, "latest pointer verification failed"):
            self.service.public_latest_download("heyue", "stable")

        self.service.repository.update_project("heyue", {"active": False})
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "stable")

    def test_service_fails_closed_for_missing_storage_or_stale_database_pointer(self) -> None:
        descriptor = self.service.public_latest_download("heyue", "stable")
        self.store.objects.pop(descriptor["key"])
        with self.assertRaisesRegex(ReleaseError, "download object was not found"):
            self.service.public_latest_download("heyue", "stable")

        # 恢复有效指针，再让数据库摘要与已发布发行不一致；路由不得提供旧指针。
        self.store.put_object(descriptor["key"], descriptor["body"], sha256=descriptor["sha256"])
        self.service.repository.conn.execute("UPDATE latest SET digest=? WHERE project_id=? AND channel=?", ("0" * 64, "heyue", "stable"))
        self.service.repository.conn.commit()
        with self.assertRaisesRegex(ReleaseError, "latest pointer verification failed"):
            self.service.public_latest_download("heyue", "stable")

    def test_django_latest_route_is_anonymous_and_non_cacheable(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            response = client.get("/api/downloads/heyue/stable/latest.json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(json.loads(response.content)["version"], "1.0.2")
            self.assertEqual(client.get("/api/downloads/heyue/beta/latest.json").status_code, 404)
            self.assertEqual(client.get("/api/downloads/heyue/1.0.2/latest.json").status_code, 404)
            versioned_asset = client.get("/api/downloads/heyue/1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(versioned_asset.status_code, 200)
            self.assertEqual(b"".join(versioned_asset.streaming_content), b"latest asset")
            self.assertEqual(versioned_asset["Cache-Control"], "public, max-age=31536000, immutable")
            v_versioned_asset = client.get("/api/downloads/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(v_versioned_asset.status_code, 200)
            self.assertEqual(b"".join(v_versioned_asset.streaming_content), b"latest asset")
            self.assertIs(resolve("/api/downloads/heyue/1.0.2/latest.json").func, views.download_view)
            self.assertIs(resolve("/api/downloads/heyue/stable/latest.json").func, views.latest_download_view)
            self.assertEqual(client.post("/api/downloads/heyue/stable/latest.json").status_code, 405)

    def test_django_heyue_bootstrap_route_serves_fixed_anonymous_script(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            response = client.get("/api/downloads/heyue/bootstrap/install.sh")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response["Content-Type"].startswith("text/x-shellscript"))
            self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
            self.assertEqual(response["Content-Disposition"], "attachment; filename=install.sh")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response["X-Fuwu-SHA256"], hashlib.sha256(response.content).hexdigest())
            self.assertEqual(int(response["Content-Length"]), len(response.content))
            self.assertTrue(response.content.startswith(b"#!/usr/bin/env bash\n"))
            self.assertIn(b'https://fuwu.1213.cc/api/downloads/heyue', response.content)
            self.assertIn(b"shiyi-log/heyue", response.content)
            self.assertIn(b"heyue-python-", response.content)
            self.assertIn(b'PROJECT = "heyue"', response.content)
            self.assertIn(b'REPOSITORY = "shiyi-log/heyue"', response.content)
            self.assertNotIn(b"Authorization", response.content)
            self.assertNotIn(b"AWS_ACCESS_KEY", response.content)
            self.assertNotIn(b"AWS_SECRET_ACCESS_KEY", response.content)
            self.assertNotIn(b"AWS_SESSION_TOKEN", response.content)
            self.assertNotIn(b"github.com", response.content.lower())
            syntax = subprocess.run(["bash", "-n"], input=response.content, capture_output=True, check=False)
            self.assertEqual(syntax.returncode, 0, syntax.stderr.decode())

            head = client.head("/api/downloads/heyue/bootstrap/install.sh")
            self.assertEqual(head.status_code, 200)
            self.assertEqual(int(head["Content-Length"]), len(response.content))
            self.assertEqual(head["X-Fuwu-SHA256"], response["X-Fuwu-SHA256"])
            self.assertEqual(head.content, b"")
            self.assertEqual(client.post("/api/downloads/heyue/bootstrap/install.sh").status_code, 405)
            self.assertIs(resolve("/api/downloads/heyue/bootstrap/install.sh").func, views.heyue_bootstrap_view)

            python_response = client.get("/api/downloads/heyue/bootstrap.py")
            self.assertEqual(python_response.status_code, 200)
            self.assertTrue(python_response["Content-Type"].startswith("text/x-python"))
            self.assertEqual(python_response["Content-Disposition"], "attachment; filename=heyue-bootstrap.py")
            self.assertEqual(python_response["X-Fuwu-SHA256"], hashlib.sha256(python_response.content).hexdigest())
            self.assertTrue(python_response.content.startswith(b"#!/usr/bin/env python3\n"))
            self.assertIn(b"https://fuwu.1213.cc/api/downloads/heyue/bootstrap/install.sh", python_response.content)
            self.assertNotIn(b"github_pat_", python_response.content)
            self.assertNotIn(b"AKIA", python_response.content)
            python_head = client.head("/api/downloads/heyue/bootstrap.py")
            self.assertEqual(python_head.status_code, 200)
            self.assertEqual(python_head["X-Fuwu-SHA256"], python_response["X-Fuwu-SHA256"])
            self.assertEqual(client.post("/api/downloads/heyue/bootstrap.py").status_code, 405)
            self.assertIs(resolve("/api/downloads/heyue/bootstrap.py").func, views.heyue_python_bootstrap_view)

    def test_bootstrap_binds_latest_identity_and_forwards_fixed_interactive_argv(self) -> None:
        source = (Path(__file__).parents[1] / "fuwu_service" / "heyue_bootstrap.sh").read_text(encoding="utf-8")
        python_source = source.split("<<'PY'\n", 1)[1].rsplit("\nPY\n", 1)[0]
        namespace: dict[str, object] = {}
        exec(compile(python_source, "heyue_bootstrap.sh", "exec"), namespace)

        revision = "a" * 40
        python_name = "heyue-python-1.0.4.tar.gz"
        pointer = {
            "version": "1.0.4", "tag": "v1.0.4", "revision": revision,
            "releaseId": 104, "publishedAt": "2026-10-23T18:42:54Z",
            "manifestKey": "releases/heyue/v1.0.4/manifest.json",
            "manifestSha256": hashlib.sha256(b"").hexdigest(),
        }
        manifest = {"version": "1.0.4", "tag": "v1.0.4", "revision": revision,
                    "releaseId": 104, "publishedAt": "2026-10-23T18:42:54Z"}
        asset = {"name": python_name, "key": f"releases/heyue/v1.0.4/{python_name}",
                 "size": 1, "sha256": "b" * 64}
        checksums = {"key": "releases/heyue/v1.0.4/SHA256SUMS", "sha256": "c" * 64,
                     "entries": {python_name: asset["sha256"]}}
        captured: dict[str, object] = {}

        namespace["base_url"] = lambda: "https://fuwu.1213.cc/api/downloads/heyue"
        namespace["get_json"] = lambda _source: (pointer, b"")
        namespace["validate_latest"] = lambda _pointer: "1.0.4"
        namespace["validate_manifest"] = lambda _manifest, *, current: (asset, checksums)
        namespace["object_url"] = lambda *_args, **_kwargs: "https://fuwu.1213.cc/object"
        namespace["get_bytes"] = lambda *_args, **_kwargs: b""
        namespace["parse_checksums"] = lambda *_args, **_kwargs: None
        namespace["download_asset"] = lambda *_args, **_kwargs: None
        namespace["extract_archive"] = lambda *_args, **_kwargs: Path("/tmp/fixed-installer.sh")

        def fake_run(command, **_kwargs):
            captured["command"] = command
            return SimpleNamespace(returncode=17)

        binding = namespace["validate_pointer_binding"]
        with patch.object(namespace["subprocess"], "run", side_effect=fake_run), patch.object(
            sys, "argv", ["heyue_bootstrap.sh", "--mode", "python"]
        ):
            result = namespace["main"]()  # type: ignore[operator]

        self.assertEqual(result, 17)
        command = captured["command"]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0:2], ["/bin/bash", "/tmp/fixed-installer.sh"])
        self.assertIn("--mode", command)
        self.assertIn("--interactive", command)
        self.assertEqual(command[command.index("--tag") + 1], "v1.0.4")
        self.assertEqual(command[command.index("--revision") + 1], revision)
        self.assertEqual(command[command.index("--release-source") + 1], "object-storage")

        mismatched = dict(manifest, releaseId=105)
        with self.assertRaises(namespace["BootstrapError"]):  # type: ignore[arg-type]
            binding(pointer, mismatched, current="1.0.4")  # type: ignore[operator]

    def test_shell_bootstrap_preserves_tty_for_python_child(self) -> None:
        script = Path(__file__).parents[1] / "fuwu_service" / "heyue_bootstrap.sh"
        with tempfile.TemporaryDirectory() as directory:
            fake_python = Path(directory) / "python3"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ -t 0 ]]; then printf 'TTY\\n'; else printf 'NO_TTY\\n'; fi\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o700)
            output = bytearray()

            def read_from_pty(fd: int) -> bytes:
                data = os.read(fd, 4096)
                output.extend(data)
                return data

            environment = os.environ.copy()
            environment["PATH"] = f"{directory}:{environment.get('PATH', '')}"
            with patch.dict(os.environ, environment, clear=True):
                status = pty.spawn(["bash", str(script), "--interactive"], read_from_pty)

        self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode(errors="replace"))
        self.assertIn(b"TTY", output)
        self.assertNotIn(b"NO_TTY", output)

    def test_anonymous_python_source_chain_uses_latest_manifest_checksums_and_asset(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            latest_response = client.get("/api/downloads/heyue/stable/latest.json")
            latest = json.loads(latest_response.content)
            version = latest["version"]
            manifest_response = client.get(f"/api/downloads/heyue/{version}/manifest.json")
            manifest = json.loads(b"".join(manifest_response.streaming_content))
            self.assertEqual(manifest["project"], "heyue")
            self.assertEqual(manifest["repository"], "shiyi-log/heyue")
            self.assertEqual(manifest["tag"], latest["tag"])
            self.assertEqual(manifest["revision"], latest["revision"])
            self.assertEqual(manifest["verificationSource"], "heyue-release-storage")
            python_asset = manifest["assets"]["python"]
            if isinstance(python_asset, list):
                self.assertEqual(len(python_asset), 1)
                python_asset = python_asset[0]
            python_name = python_asset.get("path") or python_asset["name"]
            sums_response = client.get(f"/api/downloads/heyue/{version}/SHA256SUMS")
            sums = b"".join(sums_response.streaming_content).decode("utf-8")
            self.assertIn(f"{python_asset['sha256']}  {python_name}", sums)
            asset_response = client.get(f"/api/downloads/heyue/{version}/{python_name}")
            asset = b"".join(asset_response.streaming_content)
            self.assertEqual(len(asset), python_asset["size"])
            self.assertEqual(hashlib.sha256(asset).hexdigest(), python_asset["sha256"])
            self.assertEqual(asset_response["Cache-Control"], "public, max-age=31536000, immutable")
            self.assertEqual(asset_response["X-Fuwu-SHA256"], python_asset["sha256"])

    def test_django_object_storage_key_routes_use_published_whitelist(self) -> None:
        client = Client()
        with patch("release_api.views.get_service", return_value=self.service):
            latest = client.get("/releases/heyue/stable/latest.json")
            self.assertEqual(latest.status_code, 200)
            self.assertEqual(latest["Cache-Control"], "no-store, max-age=0")
            latest_body = b"".join(latest.streaming_content)
            self.assertEqual(json.loads(latest_body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            manifest = client.get("/releases/heyue/v1.0.2/manifest.json")
            self.assertEqual(manifest.status_code, 200)
            self.assertEqual(manifest["Cache-Control"], "public, max-age=31536000, immutable")
            manifest_body = b"".join(manifest.streaming_content)
            self.assertEqual(json.loads(manifest_body)["version"], "1.0.2")
            asset = client.get("/releases/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            self.assertEqual(asset.status_code, 200)
            asset_body = b"".join(asset.streaming_content)
            self.assertEqual(asset_body, b"latest asset")
            self.assertEqual(asset["X-Fuwu-SHA256"], safe_digest(asset_body))
            self.assertEqual(client.get("/releases/heyue/v1.0.2/secret.txt").status_code, 404)
            self.assertEqual(client.get("/releases/heyue/v1.0.2/%2e%2e/secret.txt").status_code, 404)

    def test_lightweight_latest_route_is_anonymous(self) -> None:
        handler = type("LatestTestHandler", (ApiHandler,), {"service": self.service, "rbac": RBAC({})})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        thread.join(timeout=0)
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request("GET", "/api/downloads/heyue/stable/latest.json")
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "application/json")
            self.assertEqual(response.getheader("Cache-Control"), "no-store, max-age=0")
            self.assertEqual(json.loads(body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            connection.request("GET", "/api/downloads/heyue/bootstrap/install.sh")
            bootstrap = connection.getresponse()
            bootstrap_body = bootstrap.read()
            self.assertEqual(bootstrap.status, 200)
            self.assertEqual(bootstrap.getheader("Content-Type"), "text/x-shellscript; charset=utf-8")
            self.assertEqual(bootstrap.getheader("Cache-Control"), "no-store, max-age=0")
            self.assertEqual(bootstrap.getheader("Content-Disposition"), "attachment; filename=install.sh")
            self.assertEqual(bootstrap.getheader("X-Fuwu-SHA256"), hashlib.sha256(bootstrap_body).hexdigest())
            self.assertTrue(bootstrap_body.startswith(b"#!/usr/bin/env bash\n"))
            connection.request("HEAD", "/api/downloads/heyue/bootstrap/install.sh")
            bootstrap_head = connection.getresponse()
            self.assertEqual(bootstrap_head.status, 200)
            self.assertEqual(bootstrap_head.getheader("Content-Length"), str(len(bootstrap_body)))
            self.assertEqual(bootstrap_head.getheader("X-Fuwu-SHA256"), bootstrap.getheader("X-Fuwu-SHA256"))
            self.assertEqual(bootstrap_head.read(), b"")
            connection.request("POST", "/api/downloads/heyue/bootstrap/install.sh")
            bootstrap_post = connection.getresponse()
            bootstrap_post.read()
            self.assertEqual(bootstrap_post.status, 405)
            connection.request("GET", "/api/downloads/heyue/bootstrap.py")
            python_bootstrap = connection.getresponse()
            python_bootstrap_body = python_bootstrap.read()
            self.assertEqual(python_bootstrap.status, 200)
            self.assertEqual(python_bootstrap.getheader("Content-Type"), "text/x-python; charset=utf-8")
            self.assertEqual(python_bootstrap.getheader("X-Fuwu-SHA256"), hashlib.sha256(python_bootstrap_body).hexdigest())
            connection.request("GET", "/api/downloads/heyue/1.0.2/latest.json")
            versioned = connection.getresponse()
            versioned.read()
            self.assertEqual(versioned.status, 404)
            connection.request("GET", "/api/downloads/heyue/v1.0.2/heyue-python-1.0.2.tar.gz")
            v_versioned = connection.getresponse()
            v_versioned.read()
            self.assertEqual(v_versioned.status, 200)
            connection.request("GET", "/releases/heyue/stable/latest.json")
            storage_latest = connection.getresponse()
            storage_body = storage_latest.read()
            self.assertEqual(storage_latest.status, 200)
            self.assertEqual(storage_latest.getheader("Cache-Control"), "no-store, max-age=0")
            self.assertEqual(json.loads(storage_body)["manifestKey"], "releases/heyue/v1.0.2/manifest.json")
            connection.request("GET", "/releases/heyue/v1.0.2/manifest.json")
            storage_manifest = connection.getresponse()
            manifest_body = storage_manifest.read()
            self.assertEqual(storage_manifest.status, 200)
            self.assertEqual(json.loads(manifest_body)["version"], "1.0.2")
            self.assertEqual(storage_manifest.getheader("X-Fuwu-SHA256"), safe_digest(manifest_body))
            connection.request("GET", "/releases/heyue/v1.0.2/%2e%2e/secret.txt")
            traversal = connection.getresponse()
            traversal.read()
            self.assertEqual(traversal.status, 404)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()

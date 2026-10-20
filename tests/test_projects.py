"""Focused registry and cross-project isolation checks without cloud resources."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from fuwu_service.core import MemoryObjectStore, ReleaseError, ReleaseService, SQLiteRepository, json_bytes, safe_digest


class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repository = SQLiteRepository(":memory:")
        self.store = MemoryObjectStore()
        self.service = ReleaseService(self.repository, self.store, staging_dir=self.root)

    def tearDown(self):
        self.repository.conn.close()
        self.directory.cleanup()

    def register(self, project="alpha", repository=None):
        return self.service.create_project({"id": project, "name": project.title(), "repository": repository or "owner/" + project, "description": "Synthetic registry fixture"}, actor="registry-admin", request_id="register-" + project)

    def job(self, project="alpha", version="0.0.1", *, repository=None, channel="stable", name=None):
        name = name or project + "-" + channel + "-" + version
        directory = self.root / name
        directory.mkdir(exist_ok=True)
        body = (project + " " + version).encode()
        digest = safe_digest(body)
        manifest = {
            "schemaVersion": 1, "repository": repository or "owner/" + project,
            "version": version, "tag": "v" + version, "revision": "a" * 40,
            "releaseId": name, "publishedAt": "2026-10-20T00:00:00Z", "verifiedAt": "2026-10-20T00:00:00Z",
            "draft": False, "prerelease": False,
            "assets": {"python": {"path": "fixture.tgz", "size": len(body), "sha256": digest}},
        }
        (directory / "fixture.tgz").write_bytes(body)
        (directory / "manifest.json").write_bytes(json_bytes(manifest))
        (directory / "SHA256SUMS").write_text(digest + "  fixture.tgz\n")
        return {"project": project, "channel": channel, "version": version, "staging_ref": name}

    def publish(self, project="alpha", version="0.0.1", *, channel="stable", request_id=None):
        request = self.job(project, version, channel=channel)
        return self.service.publish(request, actor="publisher", request_id=request_id or "publish-" + project + "-" + channel + "-" + version)

    def test_registry_creation_metadata_update_binding_and_safe_duplicates(self):
        status, project = self.register(repository="https://github.com/Owner/Alpha.git")
        self.assertEqual(status, 201)
        self.assertEqual(set(project), {"id", "name", "repository", "description", "public_api", "build_instructions", "active", "created_at"})
        self.assertEqual(project["repository"], "owner/alpha")
        self.assertIs(project["active"], True)
        status, updated = self.service.update_project("alpha", {"name": "项目甲", "description": "Updated", "public_api": "GET /v1/items", "build_instructions": "make build", "active": False}, actor="registry-admin", request_id="update-alpha")
        self.assertEqual(status, 200)
        self.assertEqual(updated["name"], "项目甲")
        self.assertFalse(updated["active"])
        self.assertEqual(updated["repository"], "owner/alpha")
        self.assertEqual(updated["public_api"], "GET /v1/items")
        self.assertEqual(updated["build_instructions"], "make build")
        with self.assertRaises(ReleaseError) as duplicate:
            self.register()
        self.assertEqual(duplicate.exception.code, "project_conflict")
        with self.assertRaises(ReleaseError):
            self.service.update_project("alpha", {"repository": "owner/swapped"}, actor="registry-admin", request_id="swap-alpha")
        self.assertEqual(self.repository.get_project("alpha")["repository"], "owner/alpha")
        self.assertEqual(self.repository.list_projects([]), [])
        self.assertEqual([value["id"] for value in self.repository.list_projects(["alpha"])], ["alpha"])

    def test_registry_rejects_malicious_ids_repositories_and_unknown_fields(self):
        valid = {"id": "alpha", "name": "Alpha", "repository": "owner/alpha"}
        bad_requests = [
            {**valid, "id": value} for value in ("../other", "UPPER", "a..b", "a/b", "a%2fb", "a.", "a\x00b")
        ] + [
            {**valid, "repository": value} for value in ("../secrets", "file:///tmp/secret", "https://user:password@github.com/owner/repo", "https://github.com/owner/repo?secret=value", "https://github.com/owner/%2e%2e", "https://127.0.0.1/owner/repo", "git@github.com:owner/repo", "https://[invalid", "owner/../repo")
        ] + [{**valid, "credentials": "sensitive-placeholder"}]
        for request in bad_requests:
            with self.subTest(request=request), self.assertRaises(ReleaseError) as error:
                self.service.create_project(request, actor="registry-admin", request_id="bad-registry")
            self.assertEqual(error.exception.status, 422)
            self.assertNotIn("sensitive-placeholder", error.exception.message)
            self.assertNotIn("password", error.exception.message)
        self.assertEqual(self.repository.list_projects(), [])

    def test_inactive_or_repository_mismatch_cannot_publish_even_with_cached_id(self):
        self.register()
        invalid = self.job(repository="other/repository")
        with self.assertRaises(ReleaseError) as mismatch:
            self.service.publish(invalid, actor="publisher", request_id="mismatched-repository")
        self.assertEqual(mismatch.exception.code, "project_repository_mismatch")
        self.assertEqual(self.store.objects, {})
        valid = self.job(repository="https://github.com/owner/alpha")
        self.service.publish(valid, actor="publisher", request_id="publish-alpha")
        self.service.update_project("alpha", {"active": False}, actor="registry-admin", request_id="disable-alpha")
        objects_before = self.store.objects.copy()
        for request_id in ("publish-alpha", "new-publish-alpha"):
            with self.subTest(request_id=request_id), self.assertRaises(ReleaseError) as inactive:
                self.service.publish(valid, actor="publisher", request_id=request_id)
            self.assertEqual(inactive.exception.code, "project_inactive")
        self.assertEqual(self.store.objects, objects_before)

    def test_project_channel_counts_jobs_releases_and_audit_are_isolated(self):
        self.publish("alpha", "0.0.1", channel="stable", request_id="alpha-stable-job")
        self.publish("alpha", "0.0.2", channel="beta", request_id="alpha-beta-job")
        self.publish("bravo", "0.0.1", channel="stable", request_id="bravo-stable-job")
        alpha = self.service.status("alpha", "stable")
        self.assertEqual((alpha["projects"], alpha["releases"]), (1, 1))
        self.assertEqual(alpha["jobs"]["publish"]["jobId"], "alpha-stable-job")
        self.assertEqual(alpha["jobs"]["validation"]["jobId"], "alpha-stable-job")
        self.assertEqual(self.service.status("alpha", "beta")["jobs"]["publish"]["jobId"], "alpha-beta-job")
        self.assertEqual(self.service.status("bravo", "beta")["jobs"]["publish"]["status"], "idle")
        self.assertEqual(self.service.status(channel="beta")["releases"], 1)
        self.assertEqual(self.service.status()["releases"], 3)
        self.assertTrue(all(release["project_id"] == "alpha" and release["channel"] == "stable" for release in self.service.releases("alpha", "stable")))
        scoped_audit = self.repository.list_audit("alpha", channel="stable")
        self.assertTrue(scoped_audit)
        self.assertTrue(all(event["project_id"] == "alpha" and event["channel"] == "stable" for event in scoped_audit))
        self.assertEqual(self.repository.list_audit("bravo", channel="beta"), [])

    def test_idempotency_rejects_cross_project_channel_operation_actor_and_payload(self):
        request = self.job()
        self.service.publish(request, actor="publisher", request_id="scoped-request")
        first = self.store.get_object("releases/alpha/stable/latest.json")
        attempts = [
            ("publish", {**request, "project": "bravo"}, "publisher"),
            ("publish", {**request, "channel": "beta"}, "publisher"),
            ("publish", request, "other-publisher"),
            ("publish", {**request, "version": "0.0.2"}, "publisher"),
            ("rollback", {"project": "alpha", "channel": "stable", "version": "0.0.1", "confirm": True, "reason": "test"}, "publisher"),
        ]
        for operation, payload, actor in attempts:
            with self.subTest(operation=operation, payload=payload, actor=actor), self.assertRaises(ReleaseError) as conflict:
                getattr(self.service, operation)(payload, actor=actor, request_id="scoped-request")
            self.assertEqual(conflict.exception.code, "idempotency_conflict")
            self.assertEqual(conflict.exception.status, 409)
        status, cached = self.service.publish(request, actor="publisher", request_id="scoped-request")
        self.assertEqual(status, 200)
        self.assertEqual(cached["release"]["project_id"], "alpha")
        self.assertEqual(self.store.get_object("releases/alpha/stable/latest.json"), first)
        self.assertIsNone(self.repository.get_project("bravo"))

    def test_rollback_idempotency_is_scoped_and_repeated_request_is_safe(self):
        self.publish("alpha", "0.0.1")
        self.publish("alpha", "0.0.2")
        request = {"project": "alpha", "channel": "stable", "version": "0.0.1", "confirm": True, "reason": "rollback isolation"}
        self.service.rollback(request, actor="publisher", request_id="rollback-scoped")
        status, response = self.service.rollback(request, actor="publisher", request_id="rollback-scoped")
        self.assertEqual(status, 200)
        self.assertEqual(response["after"]["version"], "0.0.1")
        with self.assertRaises(ReleaseError) as conflict:
            self.service.rollback({**request, "project": "bravo"}, actor="publisher", request_id="rollback-scoped")
        self.assertEqual(conflict.exception.code, "idempotency_conflict")

    def test_legacy_schema_migrates_without_unsafe_idempotency_replay(self):
        path = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, repository TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO projects VALUES ('legacy', 'Legacy', 'owner/legacy', '2026-10-19T00:00:00Z');
            CREATE TABLE idempotency (request_id TEXT PRIMARY KEY, operation TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO idempotency VALUES ('old-request', 'publish', 201, '{"status":"published"}', '2026-10-19T00:00:00Z');
        """)
        connection.commit()
        connection.close()
        repository = SQLiteRepository(path)
        try:
            self.assertEqual(repository.get_project("legacy")["description"], "")
            self.assertEqual(repository.get_project("legacy")["public_api"], "")
            self.assertEqual(repository.get_project("legacy")["build_instructions"], "")
            self.assertTrue(repository.get_project("legacy")["active"])
            with self.assertRaises(ReleaseError) as conflict:
                repository.idempotency_get("old-request", operation="publish", project_id="legacy", channel="stable", actor="publisher", fingerprint="test")
            self.assertEqual(conflict.exception.code, "idempotency_conflict")
            repository.create_project({"id": "new", "name": "New", "repository": "owner/new"})
            self.assertEqual(repository.get_project("new")["repository"], "owner/new")
        finally:
            repository.conn.close()


if __name__ == "__main__":
    unittest.main()

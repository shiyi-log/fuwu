import tempfile
import unittest
from pathlib import Path

from fuwu_service.administration import AdministrationStore
from fuwu_service.core import MemoryObjectStore, ReleaseError, ReleaseService, SQLiteRepository


class AdministrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = AdministrationStore(self.root / "state.sqlite3")
        self.service = ReleaseService(SQLiteRepository(), MemoryObjectStore(), staging_dir=self.root / "staging")
        self.addCleanup(self.store.conn.close)
        self.addCleanup(self.service.repository.conn.close)

    def test_settings_persist_and_reject_secret_configuration(self):
        result = self.store.update_settings({"appName": "发行中心", "timezone": "UTC", "defaultChannel": "beta", "logRetentionDays": 7}, self.service)
        self.assertEqual(result["appName"], "发行中心")
        reopened = AdministrationStore(self.root / "state.sqlite3")
        self.addCleanup(reopened.conn.close)
        self.assertEqual(reopened.settings(self.service)["defaultChannel"], "beta")
        for payload in ({"AWS_SECRET_ACCESS_KEY": "hidden"}, {"logRetentionDays": 0}, {"timezone": "../../private"}, {"defaultChannel": "fake"}, {"readOnly": {}}):
            with self.subTest(payload=list(payload)):
                with self.assertRaises(ReleaseError) as caught:
                    self.store.update_settings(payload, self.service)
                self.assertNotIn("hidden", caught.exception.message)
        self.assertNotIn("database", result["readOnly"])

    def test_logs_filter_projects_channels_and_authorized_scope(self):
        for project, channel, status in (("sample-app", "stable", 201), ("other", "beta", 409), (None, None, 401)):
            self.store.record(category="release", action="publish", actor="user:test", request_id="safe-id", project_id=project,
                channel=channel, status_code=status, method="POST", route="/api/release-storage/publish", duration_ms=4)
        self.assertEqual(len(self.store.logs()), 3)
        rows = self.store.logs(project_scope=["sample-app"])
        self.assertEqual([r["project_id"] for r in rows], ["sample-app"])
        self.assertEqual(self.store.logs(project_scope=[]), [])
        self.assertEqual(len(self.store.logs(project_id="other", channel="beta", level="warning")), 1)
        self.assertEqual(self.store.logs(project_id="sample-app", channel="beta"), [])
        self.assertNotIn("body", rows[0])
        self.assertNotIn("headers", rows[0])

    def test_operational_retention_does_not_delete_domain_audit(self):
        self.service.repository.audit_event("publish", project_id="sample-app", version="0.0.1", channel="stable", actor="test", request_id="audit", details={"status": "published"})
        self.store.conn.execute("INSERT INTO operation_logs VALUES (1,'2000-01-01T00:00:00Z','info','release','publish','test','old','sample-app','stable',200,'POST','/api/release-storage/publish',1)")
        self.store.conn.commit()
        self.store.record(category="auth", action="login", actor="anonymous", request_id="new", project_id=None,
            channel=None, status_code=401, method="POST", route="/api/auth/login", duration_ms=1)
        self.assertEqual(len(self.store.logs()), 1)
        self.assertEqual(len(self.service.repository.list_audit()), 1)

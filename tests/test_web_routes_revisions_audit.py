"""Web admin revision and audit route tests (Phase 2 Task 10)."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import (
        ArchiveRecord,
        AuditLog,
        Base,
        MetadataRevision,
        Organization,
        ProcessingBatch,
        Project,
    )
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
ORG_ADMIN_USERNAME = "org-admin-a"
ORG_ADMIN_PASSWORD = "org-admin-strong-pw"
OPERATOR_USERNAME = "operator-a"
OPERATOR_PASSWORD = "operator-strong-pw"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestRevisionAuditRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲")
            org_b = Organization(name="档案室乙")
            session.add_all([org_a, org_b])
            session.flush()

            accounts.create_user(
                session,
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                display_name="平台管理员",
                role_codes=["platform_admin"],
            )
            accounts.create_user(
                session,
                username=ORG_ADMIN_USERNAME,
                password=ORG_ADMIN_PASSWORD,
                display_name="甲单位管理员",
                organization_id=org_a.id,
                role_codes=["org_admin"],
            )
            accounts.create_user(
                session,
                username=OPERATOR_USERNAME,
                password=OPERATOR_PASSWORD,
                display_name="甲单位操作员",
                organization_id=org_a.id,
                role_codes=["org_operator"],
            )

            project_a = Project(project_key="proj_a", organization_id=org_a.id)
            project_b = Project(project_key="proj_b", organization_id=org_b.id)
            session.add_all([project_a, project_b])
            session.flush()

            batch_a = ProcessingBatch(
                project_id=project_a.id,
                batch_key="batch_a",
                batch_status="completed",
                organization_id=org_a.id,
            )
            batch_b = ProcessingBatch(
                project_id=project_b.id,
                batch_key="batch_b",
                batch_status="completed",
                organization_id=org_b.id,
            )
            session.add_all([batch_a, batch_b])
            session.flush()

            archive_a = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_a",
                archive_name="甲单位档案",
                organization_id=org_a.id,
            )
            archive_b = ArchiveRecord(
                project_id=project_b.id,
                batch_id=batch_b.id,
                archive_key="arc_b",
                archive_name="乙单位档案",
                organization_id=org_b.id,
            )
            session.add_all([archive_a, archive_b])
            session.flush()
            self.archive_a_id = archive_a.id
            self.archive_b_id = archive_b.id
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id

            session.add(
                MetadataRevision(
                    archive_id=archive_a.id,
                    revision_no=1,
                    field_key="title",
                    field_column="title",
                    old_value="旧题名",
                    new_value="新题名",
                    reason="人工校对修正题名",
                )
            )
            session.add(
                AuditLog(
                    action="archive.correct",
                    target_type="archive",
                    target_id=archive_a.id,
                    actor_user_id=1,
                )
            )
            session.commit()

        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client, username: str, password: str) -> None:
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def test_revisions_list_shows_revision_rows(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_a_id}/revisions")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("人工校对修正题名", resp.text)

    def test_audit_list_shows_audit_rows(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_a_id}/audit")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("archive.correct", resp.text)

    def test_audit_route_requires_audit_view_permission(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_a_id}/audit",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)

    def test_unknown_archive_revisions_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives/999999/revisions", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    def test_unknown_archive_audit_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives/999999/audit", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    def test_org_scope_enforced_on_revisions(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_b_id}/revisions",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_org_scope_enforced_on_audit(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_b_id}/audit",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    # ── 全局审计页 /admin/audit ──────────────────────────────────────────────
    def _seed_org_audit(self):
        with self.Session() as session:
            session.add(
                AuditLog(
                    action="manual_correction",
                    target_type="archive",
                    target_id=self.archive_a_id,
                    actor_user_id=1,
                    organization_id=self.org_a_id,
                )
            )
            session.add(
                AuditLog(
                    action="archive_deleted",
                    target_type="archive",
                    target_id=self.archive_b_id,
                    actor_user_id=1,
                    organization_id=self.org_b_id,
                )
            )
            session.commit()

    def test_global_audit_renders_for_platform_admin(self):
        self._seed_org_audit()
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/audit")
        self.assertEqual(resp.status_code, 200)
        # 平台管理员看全部:两个单位的动作都在,且动作中文化。
        self.assertIn("人工修正", resp.text)
        self.assertIn("删除档案", resp.text)

    def test_global_audit_requires_audit_view(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/admin/audit", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_global_audit_org_scope_isolates(self):
        self._seed_org_audit()
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/audit")
        self.assertEqual(resp.status_code, 200)
        # 甲单位管理员只看本单位:本单位档案在,乙单位档案的行不在。
        self.assertIn(f"/archives/{self.archive_a_id}", resp.text)
        self.assertNotIn(f"/archives/{self.archive_b_id}", resp.text)

    def test_global_audit_action_filter(self):
        self._seed_org_audit()
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/audit?action=manual_correction")
        self.assertEqual(resp.status_code, 200)
        # 只筛 manual_correction:archive_a 的行在,archive_b 的删除行不在。
        self.assertIn(f"/archives/{self.archive_a_id}", resp.text)
        self.assertNotIn(f"/archives/{self.archive_b_id}", resp.text)

    def test_global_audit_invalid_page_returns_400(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/audit?page=0", follow_redirects=False)
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()

"""Web admin batch query route tests (Phase 2 Task 8)."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import (
        Base,
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
class TestBatchQueryRoutes(unittest.TestCase):
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
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id

            accounts.create_user(
                session,
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                display_name="平台管理员",
                role_codes=["platform_admin"],
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
                batch_name="甲批次",
                batch_status="completed",
                organization_id=org_a.id,
                total_archives=3,
                total_pages=9,
                success_count=1,
                fail_count=2,
                failure_breakdown={"OCR_FAILED": 2},
                summary_schema_version="1.0.0",
                summary_schema_ref="schema/batch_summary.v1.json",
                summary_changelog_ref="schema/CHANGELOG.md",
            )
            batch_b = ProcessingBatch(
                project_id=project_b.id,
                batch_key="batch_b",
                batch_name="乙批次",
                batch_status="completed",
                organization_id=org_b.id,
            )
            session.add_all([batch_a, batch_b])
            session.flush()
            self.batch_a_id = batch_a.id
            self.batch_b_id = batch_b.id
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

    def test_platform_admin_can_list_batches_by_project_key(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/batches", params={"project_key": "proj_a"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("batch_a", resp.text)

    def test_org_user_can_list_own_organization_batches(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/batches", params={"project_key": "proj_a"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("batch_a", resp.text)

    def test_batches_page_lists_accessible_projects(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/batches")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertNotIn("proj_b", resp.text)

    def test_batches_page_ignores_blank_filter_values(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                "/batches",
                params={
                    "project_key": "proj_a",
                    "status_filter": "",
                    "page": "",
                    "page_size": "",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("batch_a", resp.text)

    def test_org_user_cannot_list_other_organization_batches(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                "/batches",
                params={"project_key": "proj_b"},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_batch_detail_shows_failure_breakdown_and_schema_refs(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/batches/{self.batch_a_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("OCR_FAILED", body)
        self.assertIn("schema/batch_summary.v1.json", body)

    def test_org_user_cannot_open_other_organization_batch_detail(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_b_id}",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_invalid_page_size_returns_400(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                "/batches",
                params={"project_key": "proj_a", "page_size": 500},
            )
        self.assertEqual(resp.status_code, 400)

    def test_unauthenticated_batches_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/batches", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertIn("/login", resp.headers.get("location", ""))


if __name__ == "__main__":
    unittest.main()

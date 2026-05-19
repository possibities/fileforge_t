"""Web admin archive query route tests (Phase 2 Task 9)."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import (
        ArchivePage,
        ArchiveRecord,
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
class TestArchiveQueryRoutes(unittest.TestCase):
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
            self.batch_a_id = batch_a.id

            archive_spring = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_spring",
                archive_name="春风行动档案",
                title="春风行动简报",
                responsible_party="县档案室",
                archive_year="2021",
                classification_code="DQL",
                retention_period="永久",
                processing_status="success",
                openness_status="开放",
                llm_parse_strategy="repaired",
                final_metadata={"归档说明": "整编完成-FM标记"},
            )
            archive_daily = ArchiveRecord(
                project_id=project_a.id,
                batch_id=batch_a.id,
                archive_key="arc_daily",
                archive_name="日常公文档案",
                title="日常公文汇编",
                responsible_party="办公室",
                archive_year="2020",
                classification_code="ZHL",
                retention_period="30年",
                processing_status="failed",
            )
            archive_other_org = ArchiveRecord(
                project_id=project_b.id,
                batch_id=batch_b.id,
                archive_key="arc_b",
                archive_name="乙单位档案",
                title="乙单位文件",
                organization_id=org_b.id,
            )
            session.add_all([archive_spring, archive_daily, archive_other_org])
            session.flush()
            self.archive_spring_id = archive_spring.id
            self.archive_other_org_id = archive_other_org.id

            session.add(
                ArchivePage(
                    archive_id=archive_spring.id,
                    page_no=1,
                    image_path="input/arc_spring/page_001.jpg",
                    image_name="page_001.jpg",
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

    def test_archive_list_filters_by_title_like(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"title_like": "春风"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("日常公文汇编", resp.text)

    def test_archive_list_filters_by_year(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"archive_year": 2020},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("日常公文汇编", resp.text)
        self.assertNotIn("春风行动简报", resp.text)

    def test_archive_list_filters_by_classification_retention_and_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={
                    "classification_code": "DQL",
                    "retention_period": "永久",
                    "processing_status": "success",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("春风行动简报", resp.text)
        self.assertNotIn("日常公文汇编", resp.text)

    def test_archive_list_filters_by_responsible_party_like(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(
                f"/batches/{self.batch_a_id}/archives",
                params={"responsible_party_like": "办公室"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("日常公文汇编", resp.text)
        self.assertNotIn("春风行动简报", resp.text)

    def test_archive_detail_shows_metadata_strategy_and_pages(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/archives/{self.archive_spring_id}")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn("整编完成-FM标记", body)
        self.assertIn("repaired", body)
        self.assertIn("page_001.jpg", body)

    def test_archive_not_found_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/archives/999999", follow_redirects=False)
        self.assertEqual(resp.status_code, 404)

    def test_org_scope_violation_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get(
                f"/archives/{self.archive_other_org_id}",
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()

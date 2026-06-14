"""Web upload page route tests."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import Base, Organization, Project
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


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
class TestUploadRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org = Organization(name="档案室甲", status="active")
            session.add(org)
            session.flush()
            accounts.create_user(
                session,
                username=OPERATOR_USERNAME,
                password=OPERATOR_PASSWORD,
                display_name="甲单位操作员",
                organization_id=org.id,
                role_codes=["org_operator"],
            )
            project = Project(
                project_key="proj_a",
                project_name="甲项目",
                organization_id=org.id,
                status="active",
            )
            session.add(project)
            session.commit()
        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client) -> None:
        resp = client.post(
            "/login",
            data={"username": OPERATOR_USERNAME, "password": OPERATOR_PASSWORD},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def test_upload_page_renders_folder_picker_and_drop_zone(self):
        with TestClient(self.app) as client:
            self._login(client)
            resp = client.get("/uploads")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertIn("data-folder-input", resp.text)
        self.assertIn("webkitdirectory", resp.text)
        self.assertIn("data-dropzone", resp.text)

    def test_upload_page_ignores_blank_filter_values(self):
        with TestClient(self.app) as client:
            self._login(client)
            resp = client.get(
                "/uploads",
                params={"project_id": "", "page": "", "page_size": ""},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)


if __name__ == "__main__":
    unittest.main()

"""单位管理路由测试。"""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import Base, Organization
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
ORG_ADMIN_USERNAME = "orgadmin-a"
ORG_ADMIN_PASSWORD = "orgadmin-strong-pw"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestOrganizationRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲", status="active")
            org_b = Organization(name="档案室乙", status="disabled")
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
                username=ORG_ADMIN_USERNAME,
                password=ORG_ADMIN_PASSWORD,
                display_name="单位管理员",
                organization_id=org_a.id,
                role_codes=["org_admin"],
            )
            session.commit()
        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _login(self, client, username, password):
        resp = client.post(
            "/login",
            data={"username": username, "password": password},
            follow_redirects=False,
        )
        self.assertIn(resp.status_code, {302, 303})

    def _csrf(self, client):
        return client.cookies.get("fileforge_csrf") or ""

    def test_get_list_unauthenticated_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/admin/organizations", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertTrue(resp.headers["location"].endswith("/login"))

    def test_get_list_org_admin_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/organizations", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_platform_admin_shows_all_orgs(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/organizations")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertIn("档案室乙", resp.text)

    def test_get_new_form_renders_with_csrf(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/organizations/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('name="csrf_token"', resp.text)
        self.assertIn('name="name"', resp.text)

    def test_post_new_csrf_missing_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "新单位", "csrf_token": ""},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)

    def test_post_new_success_redirects_to_list(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "新单位", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(resp.headers["location"], "/admin/organizations")
        with self.Session() as session:
            orgs = session.scalars(
                select(Organization).where(Organization.name == "新单位")
            ).all()
        self.assertEqual(len(orgs), 1)
        self.assertEqual(orgs[0].status, "active")

    def test_post_new_duplicate_name_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "档案室甲", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already exists", resp.text)

    def test_post_new_blank_name_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/new",
                data={"name": "   ", "csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("不能为空", resp.text)

    def test_post_disable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/organizations/{self.org_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Organization, self.org_a_id).status, "disabled"
            )

    def test_post_enable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/organizations/{self.org_b_id}/enable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Organization, self.org_b_id).status, "active"
            )

    def test_post_disable_unknown_id_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/organizations/99999/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()

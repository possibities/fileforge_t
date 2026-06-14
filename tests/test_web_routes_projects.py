"""项目管理路由测试。"""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
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


ADMIN_USERNAME = "padmin"
ADMIN_PASSWORD = "platform-strong-pw"
ORG_ADMIN_USERNAME = "orgadmin-a"
ORG_ADMIN_PASSWORD = "orgadmin-strong-pw"
ORG_ADMIN_NO_ORG_USERNAME = "orgadmin-noorg"
ORG_ADMIN_NO_ORG_PASSWORD = "orgadmin-strong-pw"
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
class TestProjectRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            org_a = Organization(name="档案室甲", status="active")
            org_b = Organization(name="档案室乙", status="active")
            org_c = Organization(name="档案室丙", status="disabled")
            session.add_all([org_a, org_b, org_c])
            session.flush()
            self.org_a_id = org_a.id
            self.org_b_id = org_b.id
            self.org_c_id = org_c.id

            accounts.create_user(
                session, username=ADMIN_USERNAME, password=ADMIN_PASSWORD,
                display_name="平台管理员", role_codes=["platform_admin"],
            )
            accounts.create_user(
                session, username=ORG_ADMIN_USERNAME, password=ORG_ADMIN_PASSWORD,
                display_name="甲单位管理员", organization_id=org_a.id,
                role_codes=["org_admin"],
            )
            accounts.create_user(
                session, username=ORG_ADMIN_NO_ORG_USERNAME,
                password=ORG_ADMIN_NO_ORG_PASSWORD,
                display_name="无单位管理员",
                role_codes=["org_admin"],
            )
            accounts.create_user(
                session, username=OPERATOR_USERNAME, password=OPERATOR_PASSWORD,
                display_name="甲单位操作员", organization_id=org_a.id,
                role_codes=["org_operator"],
            )

            proj_a = Project(
                project_key="proj_a", project_name="甲项目",
                organization_id=org_a.id, status="active",
            )
            proj_b = Project(
                project_key="proj_b", project_name="乙项目",
                organization_id=org_b.id, status="active",
            )
            session.add_all([proj_a, proj_b])
            session.flush()
            self.proj_a_id = proj_a.id
            self.proj_b_id = proj_b.id
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

    def _post_new(self, client, csrf, **fields):
        form = {
            "project_name": "新项目",
            "description": "",
            "organization_id": str(self.org_a_id),
            "csrf_token": csrf,
        }
        form.update(fields)
        return client.post(
            "/admin/projects/new", data=form, follow_redirects=False
        )

    def test_get_list_unauthenticated_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertTrue(resp.headers["location"].endswith("/login"))

    def test_get_list_org_operator_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_platform_admin_sees_all_projects(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertIn("proj_b", resp.text)

    def test_get_list_org_admin_sees_only_own_org_projects(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/projects")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertNotIn("proj_b", resp.text)

    def test_get_list_org_admin_without_organization_id_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_NO_ORG_USERNAME, ORG_ADMIN_NO_ORG_PASSWORD)
            resp = client.get("/admin/projects", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_get_list_filters_by_query_organization_id(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get(f"/admin/projects?organization_id={self.org_a_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("proj_a", resp.text)
        self.assertNotIn("proj_b", resp.text)

    def test_get_new_form_platform_admin_lists_all_active_orgs(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/projects/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertIn("档案室乙", resp.text)
        self.assertNotIn("档案室丙", resp.text)

    def test_get_new_form_org_admin_dropdown_locked_to_own_org(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.get("/admin/projects/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("档案室甲", resp.text)
        self.assertNotIn("档案室乙", resp.text)

    def test_post_new_csrf_missing_returns_403(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(client, csrf="")
        self.assertEqual(resp.status_code, 403)

    def test_post_new_success_redirects_to_list(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(client, csrf=self._csrf(client))
        self.assertIn(resp.status_code, {302, 303})
        self.assertEqual(resp.headers["location"], "/admin/projects")
        with self.Session() as session:
            rows = session.scalars(
                select(Project).where(Project.project_name == "新项目")
            ).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "active")
        self.assertRegex(rows[0].project_key, r"^prj_\d{8}_[0-9a-f]{8}$")

    def test_post_new_org_admin_other_org_id_silently_locked(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = self._post_new(
                client,
                csrf=self._csrf(client),
                project_key="cross_attempt",
                organization_id=str(self.org_b_id),
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            row = session.scalar(
                select(Project).where(Project.project_key == "cross_attempt")
            )
        self.assertEqual(row.organization_id, self.org_a_id)

    def test_post_new_duplicate_project_key_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="proj_a"
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("已存在", resp.text)

    def test_post_new_invalid_project_key_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="bad key!",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("项目标识", resp.text)

    def test_post_new_blank_project_key_generates_key(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client), project_key="   ",
            )
        self.assertIn(resp.status_code, {302, 303})

    def test_post_new_disabled_org_re_renders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = self._post_new(
                client, csrf=self._csrf(client),
                project_key="proj_for_disabled",
                organization_id=str(self.org_c_id),
            )
        self.assertEqual(resp.status_code, 200)

    def test_post_disable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/projects/{self.proj_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Project, self.proj_a_id).status, "disabled"
            )

    def test_post_enable_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            client.post(
                f"/admin/projects/{self.proj_a_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
            resp = client.post(
                f"/admin/projects/{self.proj_a_id}/enable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            self.assertEqual(
                session.get(Project, self.proj_a_id).status, "active"
            )

    def test_post_disable_cross_org_org_admin_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ORG_ADMIN_USERNAME, ORG_ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/projects/{self.proj_b_id}/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)

    def test_post_disable_unknown_id_returns_404(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                "/admin/projects/99999/disable",
                data={"csrf_token": self._csrf(client)},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()

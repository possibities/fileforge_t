"""Web admin user management route tests."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import AppUser, Base
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "very-strong-password"
OPERATOR_USERNAME = "operator"
OPERATOR_PASSWORD = "another-strong-pw"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestUserManagementRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            admin = accounts.create_user(
                session,
                username=ADMIN_USERNAME,
                password=ADMIN_PASSWORD,
                display_name="平台管理员",
                role_codes=["platform_admin"],
            )
            operator = accounts.create_user(
                session,
                username=OPERATOR_USERNAME,
                password=OPERATOR_PASSWORD,
                display_name="操作员",
                role_codes=["org_operator"],
            )
            session.commit()
            self.admin_id = admin.id
            self.operator_id = operator.id

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

    def _csrf(self, client) -> str:
        token = client.cookies.get("fileforge_csrf")
        self.assertIsNotNone(token, "csrf cookie missing after login")
        return token

    def test_platform_admin_can_list_users(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/users")
        self.assertEqual(resp.status_code, 200)
        body = resp.text
        self.assertIn(ADMIN_USERNAME, body)
        self.assertIn(OPERATOR_USERNAME, body)
        self.assertIn("platform_admin", body)
        self.assertIn("org_operator", body)
        self.assertIn("active", body)

    def test_org_operator_cannot_access_user_management(self):
        with TestClient(self.app) as client:
            self._login(client, OPERATOR_USERNAME, OPERATOR_PASSWORD)
            resp = client.get("/admin/users", follow_redirects=False)
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_user_management_redirects_to_login(self):
        with TestClient(self.app) as client:
            resp = client.get("/admin/users", follow_redirects=False)
        self.assertIn(resp.status_code, {302, 303})
        self.assertIn("/login", resp.headers.get("location", ""))

    def test_get_new_user_form(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.get("/admin/users/new")
        self.assertEqual(resp.status_code, 200)
        self.assertIn('name="username"', resp.text)
        self.assertIn('name="password"', resp.text)
        self.assertIn('name="role_codes"', resp.text)
        self.assertIn('name="csrf_token"', resp.text)

    def test_create_user_creates_and_redirects(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            csrf = self._csrf(client)
            resp = client.post(
                "/admin/users/new",
                data={
                    "username": "newuser",
                    "password": "fresh-password-pw",
                    "display_name": "新用户",
                    "role_codes": "org_operator",
                    "csrf_token": csrf,
                },
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        self.assertIn("/admin/users", resp.headers.get("location", ""))

        with self.Session() as session:
            user = session.scalar(select(AppUser).where(AppUser.username == "newuser"))
            self.assertIsNotNone(user)
            self.assertEqual(user.status, "active")

    def test_create_user_with_duplicate_username_rerenders_with_error(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            csrf = self._csrf(client)
            resp = client.post(
                "/admin/users/new",
                data={
                    "username": ADMIN_USERNAME,
                    "password": "fresh-password-pw",
                    "display_name": "重复",
                    "role_codes": "org_operator",
                    "csrf_token": csrf,
                },
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("already exists", resp.text)

    def test_disable_user_changes_status(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            csrf = self._csrf(client)
            resp = client.post(
                f"/admin/users/{self.operator_id}/disable",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            operator = session.scalar(select(AppUser).where(AppUser.id == self.operator_id))
            self.assertEqual(operator.status, "disabled")

    def test_disable_self_is_rejected(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            csrf = self._csrf(client)
            resp = client.post(
                f"/admin/users/{self.admin_id}/disable",
                data={"csrf_token": csrf},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 400)
        with self.Session() as session:
            admin = session.scalar(select(AppUser).where(AppUser.id == self.admin_id))
            self.assertEqual(admin.status, "active")

    def test_reset_password_updates_hash(self):
        new_password = "new-strong-password"
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            csrf = self._csrf(client)
            resp = client.post(
                f"/admin/users/{self.operator_id}/reset-password",
                data={"new_password": new_password, "csrf_token": csrf},
                follow_redirects=False,
            )
        self.assertIn(resp.status_code, {302, 303})
        with self.Session() as session:
            operator = session.scalar(select(AppUser).where(AppUser.id == self.operator_id))
            self.assertTrue(accounts.verify_password(new_password, operator.password_hash))

    def test_disable_without_csrf_token_is_rejected(self):
        with TestClient(self.app) as client:
            self._login(client, ADMIN_USERNAME, ADMIN_PASSWORD)
            resp = client.post(
                f"/admin/users/{self.operator_id}/disable",
                data={},
                follow_redirects=False,
            )
        self.assertEqual(resp.status_code, 403)
        with self.Session() as session:
            operator = session.scalar(select(AppUser).where(AppUser.id == self.operator_id))
            self.assertEqual(operator.status, "active")


if __name__ == "__main__":
    unittest.main()

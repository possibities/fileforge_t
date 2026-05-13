"""Web admin login/logout route tests."""

from __future__ import annotations

import unittest


try:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import accounts
    from infrastructure.db.models import Base, WebSession
    from web_admin.app import create_app
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


USERNAME = "admin"
PASSWORD = "very-strong-password"


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"web deps missing: {_IMPORT_ERROR}")
class TestLoginLogoutRoutes(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            accounts.ensure_builtin_roles(session)
            accounts.create_user(
                session,
                username=USERNAME,
                password=PASSWORD,
                display_name="管理员",
                role_codes=["platform_admin"],
            )
            session.commit()

        self.app = create_app(database_url="sqlite://")
        self.app.state.session_factory = self.Session

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_get_login_returns_form_html(self):
        with TestClient(self.app) as client:
            response = client.get("/login")
        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("<form", body)
        self.assertIn('name="username"', body)
        self.assertIn('name="password"', body)
        self.assertIn("post", body.lower())

    def test_post_login_with_valid_credentials_sets_cookie_and_redirects(self):
        with TestClient(self.app) as client:
            response = client.post(
                "/login",
                data={"username": USERNAME, "password": PASSWORD},
                follow_redirects=False,
            )
        self.assertIn(response.status_code, {302, 303})
        set_cookie = response.headers.get("set-cookie", "").lower()
        self.assertIn("fileforge_session=", set_cookie)

    def test_post_login_with_invalid_credentials_rerenders_without_session_cookie(self):
        with TestClient(self.app) as client:
            response = client.post(
                "/login",
                data={"username": USERNAME, "password": "wrong-password-x"},
                follow_redirects=False,
            )
        self.assertIn(response.status_code, {200, 401})
        self.assertIsNone(response.cookies.get("fileforge_session"))

    def test_logout_revokes_session_and_clears_cookie(self):
        with TestClient(self.app) as client:
            login_resp = client.post(
                "/login",
                data={"username": USERNAME, "password": PASSWORD},
                follow_redirects=False,
            )
            self.assertIn(login_resp.status_code, {302, 303})

            csrf_token = client.cookies.get("fileforge_csrf")
            self.assertIsNotNone(csrf_token, "login must set readable fileforge_csrf cookie")

            logout_resp = client.post(
                "/logout",
                data={"csrf_token": csrf_token},
                follow_redirects=False,
            )

        self.assertIn(logout_resp.status_code, {302, 303})
        set_cookie = logout_resp.headers.get("set-cookie", "").lower()
        self.assertIn("fileforge_session=", set_cookie)

        with self.Session() as session:
            stored = session.scalar(select(WebSession))
            self.assertIsNotNone(stored)
            self.assertIsNotNone(stored.revoked_at)

    def test_logout_without_csrf_token_is_rejected(self):
        with TestClient(self.app) as client:
            login_resp = client.post(
                "/login",
                data={"username": USERNAME, "password": PASSWORD},
                follow_redirects=False,
            )
            self.assertIn(login_resp.status_code, {302, 303})

            logout_resp = client.post("/logout", follow_redirects=False)

        self.assertEqual(logout_resp.status_code, 403)
        with self.Session() as session:
            stored = session.scalar(select(WebSession))
            self.assertIsNotNone(stored)
            self.assertIsNone(stored.revoked_at)

    def test_unauthenticated_protected_route_redirects_to_login(self):
        with TestClient(self.app) as client:
            response = client.get("/", follow_redirects=False)
        self.assertIn(response.status_code, {302, 303})
        location = response.headers.get("location", "")
        self.assertIn("/login", location)


if __name__ == "__main__":
    unittest.main()

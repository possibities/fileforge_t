"""Web admin auth/session service tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import Base, WebSession
except ImportError as _exc:  # pragma: no cover
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestWebAuthSessions(unittest.TestCase):
    def setUp(self):
        from infrastructure.db import accounts
        from web_admin import auth, security

        self.accounts = accounts
        self.auth = auth
        self.security = security
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_user(self):
        with self.Session() as session:
            self.accounts.ensure_builtin_roles(session)
            user = self.accounts.create_user(
                session,
                username="admin",
                password="very-strong-password",
                display_name="管理员",
                role_codes=["platform_admin"],
            )
            session.commit()
            return user.id

    def test_web_session_table_is_registered(self):
        self.assertIn("web_sessions", Base.metadata.tables)
        table = Base.metadata.tables["web_sessions"]
        constraint_names = {c.name for c in table.constraints if c.name}
        self.assertIn("uq_web_sessions_token_hash", constraint_names)

    def test_token_hash_is_stable_and_does_not_store_plain_token(self):
        token = self.security.generate_token()
        token_hash = self.security.hash_token(token)

        self.assertNotEqual(token, token_hash)
        self.assertEqual(token_hash, self.security.hash_token(token))
        self.assertTrue(self.security.verify_token_hash(token, token_hash))
        self.assertFalse(self.security.verify_token_hash("wrong-token", token_hash))

    def test_login_creates_hashed_session_and_loads_current_user(self):
        self._create_user()
        now = datetime(2026, 5, 12, 9, 0, tzinfo=timezone.utc)

        with self.Session() as session:
            tokens = self.auth.login_user(
                session,
                username="admin",
                password="very-strong-password",
                ttl_seconds=3600,
                now=now,
            )
            session.commit()

        self.assertIsNotNone(tokens)
        with self.Session() as session:
            stored = session.scalar(select(WebSession))
            self.assertIsNotNone(stored)
            self.assertNotEqual(stored.token_hash, tokens.session_token)
            self.assertNotEqual(stored.csrf_token_hash, tokens.csrf_token)
            self.assertEqual(_as_utc(stored.expires_at), now + timedelta(seconds=3600))

            current = self.auth.load_current_user(
                session,
                session_token=tokens.session_token,
                now=now + timedelta(minutes=10),
            )
            self.assertIsNotNone(current)
            self.assertEqual(current.username, "admin")
            self.assertIn("platform_admin", current.roles)
            self.assertIn("archive:view", current.permissions)

    def test_logout_revokes_session(self):
        self._create_user()
        now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)

        with self.Session() as session:
            tokens = self.auth.login_user(
                session,
                username="admin",
                password="very-strong-password",
                now=now,
            )
            session.commit()

        with self.Session() as session:
            revoked = self.auth.logout_session(
                session,
                session_token=tokens.session_token,
                now=now + timedelta(minutes=1),
            )
            self.assertTrue(revoked)
            self.assertIsNone(
                self.auth.load_current_user(
                    session,
                    session_token=tokens.session_token,
                    now=now + timedelta(minutes=2),
                )
            )

    def test_expired_session_is_rejected(self):
        self._create_user()
        now = datetime(2026, 5, 12, 11, 0, tzinfo=timezone.utc)

        with self.Session() as session:
            tokens = self.auth.login_user(
                session,
                username="admin",
                password="very-strong-password",
                ttl_seconds=1,
                now=now,
            )
            session.commit()

        with self.Session() as session:
            current = self.auth.load_current_user(
                session,
                session_token=tokens.session_token,
                now=now + timedelta(seconds=2),
            )
            self.assertIsNone(current)

    def test_require_permission_raises_for_missing_permission(self):
        current = self.auth.CurrentUser(
            id=1,
            username="operator",
            display_name=None,
            organization_id=None,
            roles=["org_operator"],
            permissions=["archive:view"],
        )

        self.auth.require_permission(current, "archive:view")
        with self.assertRaises(PermissionError):
            self.auth.require_permission(current, "user:manage")


if __name__ == "__main__":
    unittest.main()

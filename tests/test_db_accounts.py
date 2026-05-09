"""Account/personnel service tests."""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import Base
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


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestAccountServices(unittest.TestCase):
    def setUp(self):
        from infrastructure.db import accounts

        self.accounts = accounts
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_password_hash_verification(self):
        hashed = self.accounts.hash_password("very-strong-password")
        self.assertTrue(hashed.startswith("pbkdf2_sha256$"))
        self.assertTrue(self.accounts.verify_password("very-strong-password", hashed))
        self.assertFalse(self.accounts.verify_password("wrong-password", hashed))
        self.assertNotEqual(hashed, self.accounts.hash_password("very-strong-password"))

    def test_builtin_roles_are_idempotent(self):
        with self.Session() as session:
            self.accounts.ensure_builtin_roles(session)
            self.accounts.ensure_builtin_roles(session)
            session.commit()

        with self.Session() as session:
            roles = self.accounts.list_roles(session)
            self.assertEqual(
                [r.code for r in roles],
                ["org_admin", "org_operator", "platform_admin"],
            )

    def test_create_list_authenticate_disable_and_reset_user(self):
        with self.Session() as session:
            self.accounts.ensure_builtin_roles(session)
            org = self.accounts.create_organization(session, name="档案室")
            user = self.accounts.create_user(
                session,
                username="alice",
                password="very-strong-password",
                display_name="Alice",
                organization_id=org.id,
                role_codes=["org_operator"],
            )
            session.commit()
            self.assertEqual(user.username, "alice")

        with self.Session() as session:
            users = self.accounts.list_users(session)
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].roles, ["org_operator"])
            hit = self.accounts.authenticate_user(
                session, username="alice", password="very-strong-password"
            )
            self.assertIsNotNone(hit)
            session.commit()

        with self.Session() as session:
            self.accounts.reset_password(
                session, username="alice", new_password="new-strong-password"
            )
            session.commit()

        with self.Session() as session:
            self.assertIsNone(
                self.accounts.authenticate_user(
                    session, username="alice", password="very-strong-password"
                )
            )
            self.assertIsNotNone(
                self.accounts.authenticate_user(
                    session, username="alice", password="new-strong-password"
                )
            )
            self.accounts.disable_user(session, username="alice")
            session.commit()

        with self.Session() as session:
            self.assertIsNone(
                self.accounts.authenticate_user(
                    session, username="alice", password="new-strong-password"
                )
            )

    def test_duplicate_username_is_rejected(self):
        with self.Session() as session:
            self.accounts.ensure_builtin_roles(session)
            self.accounts.create_user(
                session,
                username="bob",
                password="very-strong-password",
                role_codes=["platform_admin"],
            )
            with self.assertRaises(ValueError):
                self.accounts.create_user(
                    session,
                    username="bob",
                    password="another-strong-password",
                    role_codes=["platform_admin"],
                )


if __name__ == "__main__":
    unittest.main()

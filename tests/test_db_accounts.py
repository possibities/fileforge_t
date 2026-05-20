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


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestOrganizationManagement(unittest.TestCase):
    def setUp(self):
        from infrastructure.db import accounts
        from infrastructure.db.models import Organization

        self.accounts = accounts
        self.Organization = Organization
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_orgs(self):
        with self.Session() as session:
            a = self.Organization(name="档案室甲", status="active")
            b = self.Organization(name="档案室乙", status="disabled")
            c = self.Organization(name="档案室丙", status="active")
            session.add_all([a, b, c])
            session.commit()
            return a.id, b.id, c.id

    def test_list_organizations_returns_all_sorted_by_name(self):
        self._seed_orgs()
        with self.Session() as session:
            rows = self.accounts.list_organizations(session)
        self.assertEqual(len(rows), 3)
        names_sorted = sorted([r.name for r in rows])
        self.assertEqual([r.name for r in rows], names_sorted)

    def test_list_organizations_status_filter(self):
        self._seed_orgs()
        with self.Session() as session:
            rows = self.accounts.list_organizations(
                session, status_filter=("active",)
            )
        self.assertEqual({r.name for r in rows}, {"档案室甲", "档案室丙"})

    def test_set_organization_status_to_disabled(self):
        a_id, _, _ = self._seed_orgs()
        with self.Session() as session:
            self.accounts.set_organization_status(
                session, organization_id=a_id, status="disabled"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(
                session.get(self.Organization, a_id).status, "disabled"
            )

    def test_set_organization_status_to_active_reenables(self):
        _, b_id, _ = self._seed_orgs()
        with self.Session() as session:
            self.accounts.set_organization_status(
                session, organization_id=b_id, status="active"
            )
            session.commit()
        with self.Session() as session:
            self.assertEqual(
                session.get(self.Organization, b_id).status, "active"
            )

    def test_set_organization_status_invalid_status_raises_value_error(self):
        a_id, _, _ = self._seed_orgs()
        with self.Session() as session:
            with self.assertRaises(ValueError):
                self.accounts.set_organization_status(
                    session, organization_id=a_id, status="archived"
                )

    def test_set_organization_status_unknown_id_raises(self):
        with self.Session() as session:
            with self.assertRaises(ValueError):
                self.accounts.set_organization_status(
                    session, organization_id=99999, status="disabled"
                )


if __name__ == "__main__":
    unittest.main()

"""PostgreSQL account/personnel schema tests."""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine
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
class TestAccountModels(unittest.TestCase):
    def test_account_tables_are_registered(self):
        expected = {
            "organizations",
            "app_users",
            "roles",
            "permissions",
            "user_roles",
            "role_permissions",
        }
        self.assertTrue(expected.issubset(set(Base.metadata.tables)))

    def test_schema_can_create_on_sqlite(self):
        engine = _make_engine()
        try:
            Base.metadata.create_all(engine)
            table_names = set(Base.metadata.tables)
            self.assertIn("app_users", table_names)
            self.assertIn("organizations", table_names)
        finally:
            Base.metadata.drop_all(engine)
            engine.dispose()

    def test_unique_constraints_exist_for_key_account_fields(self):
        users = Base.metadata.tables["app_users"]
        roles = Base.metadata.tables["roles"]
        permissions = Base.metadata.tables["permissions"]
        constraints = {
            c.name
            for table in (users, roles, permissions)
            for c in table.constraints
            if c.name
        }
        self.assertIn("uq_app_users_username", constraints)
        self.assertIn("uq_roles_code", constraints)
        self.assertIn("uq_permissions_code", constraints)


if __name__ == "__main__":
    unittest.main()

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
            "web_sessions",
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
        constraints = {
            c.name
            for table in (users,)
            for c in table.constraints
            if c.name
        }
        self.assertIn("uq_app_users_username", constraints)
        self.assertIn("role", users.c)


if __name__ == "__main__":
    unittest.main()

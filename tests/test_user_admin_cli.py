"""Personnel management CLI tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


try:
    from sqlalchemy import create_engine

    from infrastructure.db.models import Base
except ImportError as _exc:  # pragma: no cover
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestUserAdminCli(unittest.TestCase):
    def setUp(self):
        from utils import user_admin

        self.user_admin = user_admin
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "accounts.sqlite"
        self.database_url = f"sqlite:///{self.db_path.as_posix()}"
        engine = create_engine(self.database_url, future=True)
        Base.metadata.create_all(engine)
        engine.dispose()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = self.user_admin.run(["--database-url", self.database_url, *argv])
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_create_list_login_disable_and_reset_user(self):
        rc, _, err = self._run(["roles", "init"])
        self.assertEqual(rc, 0, err)

        rc, _, err = self._run(["orgs", "create", "--name", "档案室"])
        self.assertEqual(rc, 0, err)

        rc, out, err = self._run(
            [
                "users",
                "create",
                "--username",
                "admin",
                "--password",
                "very-strong-password",
                "--role",
                "platform_admin",
                "--display-name",
                "管理员",
            ]
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["username"], "admin")

        rc, out, err = self._run(["users", "list"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["items"][0]["roles"], ["platform_admin"])

        rc, out, err = self._run(
            ["login", "--username", "admin", "--password", "very-strong-password"]
        )
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["authenticated"])

        rc, _, err = self._run(
            [
                "users",
                "reset-password",
                "--username",
                "admin",
                "--password",
                "new-strong-password",
            ]
        )
        self.assertEqual(rc, 0, err)

        rc, out, err = self._run(
            ["login", "--username", "admin", "--password", "new-strong-password"]
        )
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["authenticated"])

        rc, _, err = self._run(["users", "disable", "--username", "admin"])
        self.assertEqual(rc, 0, err)

        rc, out, err = self._run(
            ["login", "--username", "admin", "--password", "new-strong-password"]
        )
        self.assertEqual(rc, 4, err)
        self.assertFalse(json.loads(out)["authenticated"])

    def test_missing_database_url_returns_2(self):
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = self.user_admin.run(["users", "list"])
        self.assertEqual(rc, 2)
        self.assertIn("DATABASE_URL", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()

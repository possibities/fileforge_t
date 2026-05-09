"""Runtime documentation smoke tests for PostgreSQL basic admin."""

from __future__ import annotations

import unittest
from pathlib import Path


class TestPostgreSQLBasicAdminDocs(unittest.TestCase):
    def test_runtime_doc_contains_required_commands(self):
        path = Path("docs/postgresql_basic_admin_runtime.md")
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        required = [
            "pip install -r requirements/db.txt",
            "alembic upgrade head",
            "python -m utils.user_admin roles init",
            "python -m utils.user_admin users create",
            "python -m utils.archive_query batches list",
            "python -m unittest discover -s tests -p \"test_*.py\"",
        ]
        for item in required:
            self.assertIn(item, text)


if __name__ == "__main__":
    unittest.main()

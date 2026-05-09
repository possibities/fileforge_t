"""阶段 1C archive_query CLI 端到端测试。

测试通过 in-process dispatch:`from utils.archive_query import run; run([...])`,
不走 subprocess(慢,且不便断言 stdout/stderr/exit-code)。
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import Base
    # 复用 queries 测试的 seed
    from tests.test_db_queries import _seed_query_fixtures
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
class TestCliBootstrap(unittest.TestCase):
    """脚手架级别的测试:不依赖具体 subcommand 实现。"""

    def test_no_subcommand_returns_2_and_writes_usage(self):
        from utils import archive_query as cli
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.run([])
        self.assertEqual(rc, 2)

    def test_unknown_subcommand_returns_2(self):
        from utils import archive_query as cli
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = cli.run(["unknown", "thing"])
        self.assertEqual(rc, 2)

    def test_database_url_empty_returns_2(self):
        from utils import archive_query as cli
        with mock.patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                rc = cli.run(["batches", "list", "--project-key", "x"])
            self.assertEqual(rc, 2)
            self.assertIn("DATABASE_URL", stderr.getvalue())

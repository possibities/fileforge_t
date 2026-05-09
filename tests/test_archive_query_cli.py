"""阶段 1C archive_query CLI 端到端测试。

测试通过 in-process dispatch:`from utils.archive_query import run; run([...])`,
不走 subprocess(慢,且不便断言 stdout/stderr/exit-code)。
"""

from __future__ import annotations

import io
import json
import os
import sys
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


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestCliBatches(unittest.TestCase):
    def setUp(self):
        from utils import archive_query as cli
        self.cli = cli
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            self.ids = _seed_query_fixtures(session)
            session.commit()
        # 让 cli.run() 在内部 make_engine 时拿到同一个 in-memory DB
        self._patch_engine = mock.patch("utils.archive_query.run", new=self._run_with_session)
        self._original_run = cli.run

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _run_with_session(self, argv):
        """绕开 cli.run 的 make_engine,使用本测试的 in-memory engine 执行。"""
        parser = self.cli._build_parser()
        try:
            args = parser.parse_args(argv)
        except SystemExit as exc:
            return exc.code if isinstance(exc.code, int) else 2

        if not getattr(args, "resource", None) or not getattr(args, "verb", None):
            return 2
        func = getattr(args, "func", None)
        if func is None:
            return 2
        try:
            with self.Session() as session:
                return func(args, session)
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        except Exception as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 9

    def test_batches_list_outputs_json_envelope(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session(["batches", "list", "--project-key", "proj_test"])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["page_size"], 50)
        self.assertEqual(payload["has_next"], False)
        self.assertEqual(len(payload["items"]), 2)

    def test_batches_list_status_filter(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session([
                "batches", "list",
                "--project-key", "proj_test",
                "--status", "completed",
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["batch_key"], "batch_a")

    def test_batches_show_returns_detail_json(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = self._run_with_session([
                "batches", "show", "--batch-id", str(self.ids["batch_a_id"]),
            ])
        self.assertEqual(rc, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["batch_key"], "batch_a")
        self.assertEqual(payload["failure_breakdown"], {"LLM_PARSE_FAIL": 1, "OCR_TIMEOUT": 1})

    def test_batches_show_not_found_returns_4(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            rc = self._run_with_session(["batches", "show", "--batch-id", "99999"])
        self.assertEqual(rc, 4)
        self.assertIn("not found", stderr.getvalue())

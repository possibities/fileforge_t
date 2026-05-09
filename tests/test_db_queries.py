"""阶段 1C queries.py 的 SQLite 回归测试。"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import queries
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
class TestDataclasses(unittest.TestCase):
    def test_list_result_is_generic_and_frozen(self):
        result = queries.ListResult(
            items=[],
            total=0,
            page=1,
            page_size=50,
            has_next=False,
        )
        self.assertEqual(result.total, 0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.total = 1  # type: ignore[misc]

    def test_archive_filter_defaults_all_none(self):
        f = queries.ArchiveFilter()
        for field in dataclasses.fields(f):
            self.assertIsNone(getattr(f, field.name), msg=field.name)

    def test_archive_filter_field_count(self):
        # 与 spec §3.2 / 数据契约 §9 锁定 12 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveFilter)), 12)

    def test_archive_summary_field_count(self):
        # 与 spec §3.6 锁定 27 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveSummary)), 27)

    def test_archive_detail_field_count(self):
        # 与 spec §3.7 锁定 45 字段
        self.assertEqual(len(dataclasses.fields(queries.ArchiveDetail)), 45)

    def test_dataclasses_are_frozen(self):
        now = datetime.now(timezone.utc)
        page = queries.ArchivePage(
            id=1, page_no=1, image_path="a/b.png", image_name="b.png",
            file_hash=None, file_size=None, ocr_text=None,
            ocr_avg_confidence=None, ocr_low_conf_count=None, ocr_variant=None,
            created_at=now,
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            page.page_no = 2  # type: ignore[misc]


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestPaginate(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_validate_page_lt_1_raises(self):
        with self.assertRaises(ValueError) as ctx:
            queries._validate_pagination(0, 50)
        self.assertIn("page must be >= 1", str(ctx.exception))

    def test_validate_page_size_lt_1_raises(self):
        with self.assertRaises(ValueError) as ctx:
            queries._validate_pagination(1, 0)
        self.assertIn("page_size must be in [1, 200]", str(ctx.exception))

    def test_validate_page_size_gt_200_raises(self):
        with self.assertRaises(ValueError):
            queries._validate_pagination(1, 201)

    def test_validate_accepts_boundary_values(self):
        queries._validate_pagination(1, 1)
        queries._validate_pagination(1, 200)

    def test_build_list_result_has_next_true_when_total_exceeds_page(self):
        result = queries._build_list_result(
            items=["a", "b"], total=10, page=1, page_size=2
        )
        self.assertTrue(result.has_next)
        self.assertEqual(result.total, 10)
        self.assertEqual(result.page, 1)
        self.assertEqual(result.page_size, 2)

    def test_build_list_result_has_next_false_on_last_page(self):
        result = queries._build_list_result(
            items=["i9", "i10"], total=10, page=5, page_size=2
        )
        self.assertFalse(result.has_next)

    def test_build_list_result_empty_items(self):
        result = queries._build_list_result(items=[], total=0, page=1, page_size=50)
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)
        self.assertFalse(result.has_next)

    def test_build_list_result_page_beyond_end_returns_empty_no_next(self):
        result = queries._build_list_result(items=[], total=3, page=99, page_size=50)
        self.assertEqual(result.items, [])
        self.assertFalse(result.has_next)
        self.assertEqual(result.total, 3)

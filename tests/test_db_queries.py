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

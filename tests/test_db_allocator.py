"""验证 InMemoryAllocator 与 DatabaseAllocator 在同输入下生成一致的件号格式。

DatabaseAllocator 走 SQLite in-memory；行锁在 SQLite 上是 no-op，但递增结果依然正确。
"""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.allocator import DatabaseAllocator, InMemoryAllocator
    from infrastructure.db.models import Base, Project
except ImportError as _exc:  # pragma: no cover
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


YEAR_KEY = "归档年度"
CLASS_KEY = "实体分类号"
PERIOD_KEY = "保管期限"
SERIAL_KEY = "件号"
DOC_ID_KEY = "档号"


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestAllocatorParity(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

        with self.Session() as session:
            project = Project(project_key="parity")
            session.add(project)
            session.flush()
            self.project_id = project.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _payload(self):
        return {YEAR_KEY: "2026", CLASS_KEY: "DQL", PERIOD_KEY: "30年"}

    def test_inmemory_first_allocations(self):
        allocator = InMemoryAllocator()
        first = allocator.assign(self._payload())
        second = allocator.assign(self._payload())
        self.assertEqual(first[SERIAL_KEY], "0001")
        self.assertEqual(first[DOC_ID_KEY], "2026-DQL-D30-0001")
        self.assertEqual(second[SERIAL_KEY], "0002")
        self.assertEqual(second[DOC_ID_KEY], "2026-DQL-D30-0002")

    def test_database_first_allocations(self):
        allocator = DatabaseAllocator(
            session_factory=self.Session, project_id=self.project_id
        )
        first = allocator.assign(self._payload())
        second = allocator.assign(self._payload())
        self.assertEqual(first[SERIAL_KEY], "0001")
        self.assertEqual(first[DOC_ID_KEY], "2026-DQL-D30-0001")
        self.assertEqual(second[SERIAL_KEY], "0002")
        self.assertEqual(second[DOC_ID_KEY], "2026-DQL-D30-0002")

    def test_database_alloc_persists_across_factories(self):
        allocator = DatabaseAllocator(
            session_factory=self.Session, project_id=self.project_id
        )
        allocator.assign(self._payload())
        allocator.assign(self._payload())

        # 模拟新的进程拿到一样的 project_id 与同一数据库 → 应该接着 0003 发
        allocator2 = DatabaseAllocator(
            session_factory=self.Session, project_id=self.project_id
        )
        third = allocator2.assign(self._payload())
        self.assertEqual(third[SERIAL_KEY], "0003")

    def test_returns_none_on_missing_field(self):
        allocator = DatabaseAllocator(
            session_factory=self.Session, project_id=self.project_id
        )
        out = allocator.assign({YEAR_KEY: "2026", CLASS_KEY: "", PERIOD_KEY: "30年"})
        self.assertIsNone(out[SERIAL_KEY])
        self.assertIsNone(out[DOC_ID_KEY])


if __name__ == "__main__":
    unittest.main()

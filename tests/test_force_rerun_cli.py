"""utils.force_rerun_cli 的端到端测试。

策略:用临时文件 SQLite 数据库,先 setup 一个 archive,再调 CLI run() 函数,
最后用同一个 URL 重连验证写入结果。
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from infrastructure.db.models import (
        ArchiveRecord,
        AuditLog,
        Base,
        MetadataRevision,
        ProcessingBatch,
        Project,
    )
    from utils.force_rerun_cli import run as cli_run
except ImportError as _exc:  # pragma: no cover
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestForceRerunCli(unittest.TestCase):
    def setUp(self):
        self.tmp_root = Path("tests") / "_tmp_force_rerun_cli"
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self._meta_counter = 0

        self.db_path = self.tmp_root / "cli.sqlite"
        self.db_url = f"sqlite:///{self.db_path.as_posix()}"
        self.engine = create_engine(self.db_url, future=True)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

        with self.Session() as session:
            project = Project(project_key="cli_p")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="cli_b")
            session.add(batch)
            session.flush()
            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="cli_a",
                archive_name="cli_a",
                title="原题名",
                final_metadata={"题名": "原题名", "归档年度": "2026"},
                correction_status="corrected",
            )
            session.add(archive)
            session.commit()

    def tearDown(self):
        self.engine.dispose()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _meta_file(self, payload: dict) -> str:
        # 每次唯一文件名,避免 _argv 默认调用与 override 调用相互覆盖文件
        self._meta_counter += 1
        path = self.tmp_root / f"new_metadata_{self._meta_counter}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _argv(self, **overrides) -> list[str]:
        defaults = {
            "--project-key": "cli_p",
            "--batch-key": "cli_b",
            "--archive-key": "cli_a",
            "--metadata-file": self._meta_file({"题名": "新题名", "归档年度": "2026"}),
            "--database-url": self.db_url,
        }
        defaults.update(overrides)
        return [item for pair in defaults.items() for item in pair]

    def test_diff_writes_revision_and_returns_zero(self):
        rc = cli_run(self._argv())
        self.assertEqual(rc, 0)

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.title, "新题名")
            revs = session.scalars(select(MetadataRevision)).all()
            self.assertEqual({r.field_key for r in revs}, {"题名"})
            log = session.scalar(select(AuditLog))
            self.assertEqual(log.action, "force_rerun_rules")

    def test_no_diff_writes_nothing_and_returns_zero(self):
        argv = self._argv(
            **{
                "--metadata-file": self._meta_file(
                    {"题名": "原题名", "归档年度": "2026"}
                )
            }
        )
        rc = cli_run(argv)
        self.assertEqual(rc, 0)

        with self.Session() as session:
            self.assertEqual(session.scalars(select(MetadataRevision)).all(), [])
            self.assertEqual(session.scalars(select(AuditLog)).all(), [])

    def test_archive_not_found_returns_4(self):
        argv = self._argv(**{"--archive-key": "does_not_exist"})
        rc = cli_run(argv)
        self.assertEqual(rc, 4)

    def test_missing_database_url_returns_2(self):
        argv = self._argv()
        # 删除 --database-url
        idx = argv.index("--database-url")
        del argv[idx : idx + 2]

        # 同时确保环境变量也没设
        import os
        original = os.environ.pop("DATABASE_URL", None)
        try:
            rc = cli_run(argv)
        finally:
            if original is not None:
                os.environ["DATABASE_URL"] = original
        self.assertEqual(rc, 2)

    def test_invalid_metadata_file_returns_5(self):
        bad_path = self.tmp_root / "not_json.txt"
        bad_path.write_text("this is not json {{{", encoding="utf-8")
        argv = self._argv(**{"--metadata-file": str(bad_path)})
        rc = cli_run(argv)
        self.assertEqual(rc, 5)

    def test_metadata_file_must_be_object(self):
        list_path = self.tmp_root / "list.json"
        list_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        argv = self._argv(**{"--metadata-file": str(list_path)})
        rc = cli_run(argv)
        self.assertEqual(rc, 5)


if __name__ == "__main__":
    unittest.main()

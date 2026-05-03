"""阶段 1A repositories 模块的回归测试。

覆盖三条容易被遗漏的逻辑：
  - apply_classification_result 对 correction_status='corrected' 档案的保护
  - apply_classification_result 在 force_rerun_rules=True 时的强制覆盖
  - upsert_archive 重跑时的状态重置（且 corrected 档案不被清状态）
"""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import repositories
    from infrastructure.db.models import (
        ArchiveRecord,
        Base,
        ProcessingBatch,
        Project,
    )
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
class TestApplyClassificationResult(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

        with self.Session() as session:
            project = Project(project_key="p")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="b")
            session.add(batch)
            session.flush()
            self.project_id = project.id
            self.batch_id = batch.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _make_archive(self, **overrides) -> int:
        with self.Session() as session:
            archive = ArchiveRecord(
                project_id=self.project_id,
                batch_id=self.batch_id,
                archive_key=overrides.pop("archive_key", "demo"),
                archive_name=overrides.pop("archive_name", "demo"),
                **overrides,
            )
            session.add(archive)
            session.commit()
            return archive.id

    def _payload(self, title: str = "新题名", year: str = "2026"):
        return {
            "题名": title,
            "归档年度": year,
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "30年",
            "件号": "0007",
            "档号": f"{year}-DQL-D30-0007",
        }

    def test_corrected_archive_is_protected_by_default(self):
        archive_id = self._make_archive(
            correction_status="corrected",
            title="人工修正后的题名",
            final_metadata={"题名": "人工修正后的题名"},
        )

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(),
                rules_metadata=self._payload(),
                llm_metadata={"raw": "llm-output"},
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            # final_metadata 与冗余列保留
            self.assertEqual(archive.final_metadata, {"题名": "人工修正后的题名"})
            self.assertEqual(archive.title, "人工修正后的题名")
            # 但 llm/rules 快照刷新了
            self.assertEqual(archive.llm_metadata, {"raw": "llm-output"})
            self.assertEqual(archive.rules_metadata, self._payload())

    def test_force_rerun_rules_overrides_protection(self):
        archive_id = self._make_archive(
            correction_status="corrected",
            title="人工修正后的题名",
            final_metadata={"题名": "人工修正后的题名"},
        )

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(title="规则重排后的题名"),
                rules_metadata=self._payload(title="规则重排后的题名"),
                force_rerun_rules=True,
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.title, "规则重排后的题名")
            self.assertEqual(archive.final_metadata["题名"], "规则重排后的题名")
            self.assertEqual(archive.archive_no, "2026-DQL-D30-0007")
            self.assertEqual(archive.item_no, "0007")

    def test_first_time_apply_fills_redundant_columns(self):
        archive_id = self._make_archive()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata=self._payload(),
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.title, "新题名")
            self.assertEqual(archive.archive_year, "2026")
            self.assertEqual(archive.classification_code, "DQL")
            self.assertEqual(archive.retention_period, "30年")
            self.assertEqual(archive.retention_period_code, "D30")

    def test_retention_code_resolves_old_year(self):
        archive_id = self._make_archive()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            repositories.apply_classification_result(
                session,
                archive=archive,
                final_metadata={
                    "归档年度": "2005",
                    "保管期限": "长期",
                    "实体分类号": "002",
                    "实体分类名称": "综合类",
                    "题名": "旧档案",
                },
            )
            session.commit()

        with self.Session() as session:
            archive = session.get(ArchiveRecord, archive_id)
            self.assertEqual(archive.retention_period_code, "C")


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestUpsertArchiveRerun(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            project = Project(project_key="p2")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="b2")
            session.add(batch)
            session.flush()
            self.project_id = project.id
            self.batch_id = batch.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _common_kwargs(self):
        return dict(
            project_id=self.project_id,
            batch_id=self.batch_id,
            archive_key="demo",
            archive_name="demo",
            source_folder="/in",
            page_count=1,
            image_files=["/in/0001.jpg"],
            image_names=["0001.jpg"],
            processed_time="2026-05-03T18:00:00",
        )

    def test_rerun_resets_status_for_non_corrected(self):
        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            archive.processing_status = "failed"
            archive.error_code = "PROCESS_EXCEPTION"
            archive.error_message = "boom"
            archive.traceback_text = "trace"
            session.commit()
            first_id = archive.id

        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            session.commit()

            self.assertEqual(archive.id, first_id)
            self.assertEqual(archive.processing_status, "pending")
            self.assertIsNone(archive.error_code)
            self.assertIsNone(archive.error_message)
            self.assertIsNone(archive.traceback_text)

    def test_rerun_keeps_status_for_corrected(self):
        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            archive.processing_status = "success"
            archive.correction_status = "corrected"
            archive.title = "人工修正题名"
            archive.error_code = None
            session.commit()

        with self.Session() as session:
            archive = repositories.upsert_archive(session, **self._common_kwargs())
            session.commit()

            self.assertEqual(archive.processing_status, "success")
            self.assertEqual(archive.correction_status, "corrected")
            self.assertEqual(archive.title, "人工修正题名")


if __name__ == "__main__":
    unittest.main()

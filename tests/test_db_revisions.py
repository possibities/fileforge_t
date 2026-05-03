"""阶段 1B 修正记录与审计日志的端到端测试。

覆盖:
  - next_revision_no / record_revisions 共享 revision_no
  - record_audit_log 基本字段填充
  - apply_force_rerun_rules 端到端:
      * 无差异时不写 revision、不写 audit
      * 有差异时 revision/audit 各自写,final_metadata 与冗余列被覆盖
      * 已 corrected 档案被强制覆盖,但 correction_status 不变
  - BatchRecorder.force_rerun_rules_for_archive 走通整条
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db import repositories
    from infrastructure.db.models import (
        ArchiveRecord,
        AuditLog,
        Base,
        MetadataRevision,
        ProcessingBatch,
        Project,
    )
    from infrastructure.db.recorder import BatchRecorder
    from infrastructure.db.repositories import FieldRevision
    from processors.batch_processor import BatchProcessor
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
class TestRevisionRepositories(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            project = Project(project_key="rv-p")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="rv-b")
            session.add(batch)
            session.flush()
            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="rv-a",
                archive_name="rv-a",
                final_metadata={"题名": "原题名", "责任者": "甲"},
                title="原题名",
                responsible_party="甲",
            )
            session.add(archive)
            session.flush()
            self.project_id = project.id
            self.batch_id = batch.id
            self.archive_id = archive.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_next_revision_no_starts_from_one(self):
        with self.Session() as session:
            self.assertEqual(
                repositories.next_revision_no(session, archive_id=self.archive_id), 1
            )

    def test_record_revisions_shares_one_revision_no(self):
        revs = [
            FieldRevision(field_key="题名", field_column="title", old_value="a", new_value="b"),
            FieldRevision(field_key="责任者", field_column="responsible_party", old_value="x", new_value="y"),
        ]
        with self.Session() as session:
            rev_no = repositories.record_revisions(
                session,
                archive_id=self.archive_id,
                revisions=revs,
                actor_user_id=None,
                reason="test",
            )
            session.commit()
            self.assertEqual(rev_no, 1)

        with self.Session() as session:
            rows = session.scalars(
                select(MetadataRevision).where(
                    MetadataRevision.archive_id == self.archive_id
                )
            ).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual({r.revision_no for r in rows}, {1})
            self.assertEqual({r.field_key for r in rows}, {"题名", "责任者"})
            # 第二次调用应得到 revision_no=2
            self.assertEqual(
                repositories.next_revision_no(session, archive_id=self.archive_id), 2
            )

    def test_record_revisions_empty_input_is_noop(self):
        with self.Session() as session:
            rev_no = repositories.record_revisions(
                session, archive_id=self.archive_id, revisions=[]
            )
            self.assertEqual(rev_no, 0)
            count = session.scalar(
                select(MetadataRevision).where(
                    MetadataRevision.archive_id == self.archive_id
                )
            )
            self.assertIsNone(count)

    def test_record_audit_log_basic(self):
        with self.Session() as session:
            log = repositories.record_audit_log(
                session,
                actor_user_id=None,
                action="export",
                target_type="batch",
                target_id=self.batch_id,
                before_data=None,
                after_data={"file": "out.json"},
            )
            session.commit()
            self.assertEqual(log.action, "export")
            self.assertEqual(log.target_id, self.batch_id)
            self.assertEqual(log.after_data, {"file": "out.json"})


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestApplyForceRerunRules(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)
        with self.Session() as session:
            project = Project(project_key="fr-p")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="fr-b")
            session.add(batch)
            session.flush()
            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="fr-a",
                archive_name="fr-a",
                correction_status="corrected",
                title="人工修正题名",
                responsible_party="人工修正责任者",
                archive_year="2026",
                final_metadata={
                    "题名": "人工修正题名",
                    "责任者": "人工修正责任者",
                    "归档年度": "2026",
                },
            )
            session.add(archive)
            session.flush()
            self.archive_id = archive.id
            session.commit()

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_no_diff_writes_nothing(self):
        same_metadata = {
            "题名": "人工修正题名",
            "责任者": "人工修正责任者",
            "归档年度": "2026",
        }
        with self.Session() as session:
            archive = session.get(ArchiveRecord, self.archive_id)
            rev_no = repositories.apply_force_rerun_rules(
                session, archive=archive, new_metadata=same_metadata
            )
            session.commit()
            self.assertEqual(rev_no, 0)

        with self.Session() as session:
            self.assertEqual(
                session.scalars(select(MetadataRevision)).all(),
                [],
            )
            self.assertEqual(session.scalars(select(AuditLog)).all(), [])

    def test_diff_overrides_corrected_and_writes_revision_audit(self):
        new_metadata = {
            "题名": "规则重排后的题名",
            "责任者": "人工修正责任者",  # 不变
            "归档年度": "2026",
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "30年",
        }
        with self.Session() as session:
            archive = session.get(ArchiveRecord, self.archive_id)
            rev_no = repositories.apply_force_rerun_rules(
                session,
                archive=archive,
                new_metadata=new_metadata,
                actor_user_id=None,
                reason="rules_rerun_force",
            )
            session.commit()
            self.assertEqual(rev_no, 1)

        with self.Session() as session:
            archive = session.get(ArchiveRecord, self.archive_id)
            # final_metadata 与冗余列被覆盖
            self.assertEqual(archive.title, "规则重排后的题名")
            self.assertEqual(archive.classification_code, "DQL")
            self.assertEqual(archive.retention_period_code, "D30")
            # correction_status 不变(force_rerun 只覆盖 metadata,不重置人工修正标志)
            self.assertEqual(archive.correction_status, "corrected")

            # 修正历史:只有真正变化的字段才进入 revisions(责任者未变)
            revs = session.scalars(select(MetadataRevision)).all()
            changed_keys = {r.field_key for r in revs}
            self.assertIn("题名", changed_keys)
            self.assertNotIn("责任者", changed_keys)
            for r in revs:
                self.assertEqual(r.revision_no, 1)
                self.assertEqual(r.reason, "rules_rerun_force")

            # audit_logs 写一条
            log = session.scalar(select(AuditLog))
            self.assertEqual(log.action, "force_rerun_rules")
            self.assertEqual(log.target_type, "archive")
            self.assertEqual(log.target_id, self.archive_id)
            self.assertEqual(log.before_data["题名"], "人工修正题名")
            self.assertEqual(log.after_data["题名"], "规则重排后的题名")


class _StubClassifier:
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0
        self.last_extraction_trace = None

    def process_multi_page_document(self, archive_name, image_paths):
        self.calls += 1
        return dict(self._payload)


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestRecorderForceRerunHook(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

        self.tmp_root = Path("tests") / "_tmp_force_rerun_case"
        if self.tmp_root.exists():
            shutil.rmtree(self.tmp_root, ignore_errors=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        archive_dir = self.tmp_root / "input" / "demo"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "0001.jpg").write_bytes(b"fake")
        self.archive_dict = {"demo": [str(archive_dir / "0001.jpg")]}
        self.payload = {
            "归档年度": "2026",
            "实体分类号": "DQL",
            "实体分类名称": "党群类",
            "保管期限": "30年",
            "题名": "原题名",
            "责任者": "原责任者",
        }

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_force_rerun_via_recorder_writes_revision_and_audit(self):
        recorder = BatchRecorder(
            engine=self.engine,
            session_factory=self.Session,
            project_key="fr",
            project_name=None,
            batch_key="fr-1",
            input_dir=str(self.tmp_root / "input"),
            output_dir=str(self.tmp_root / "out"),
        )
        processor = BatchProcessor(_StubClassifier(self.payload), recorder=recorder)
        processor.batch_process_archives(
            self.archive_dict, output_dir=str(self.tmp_root / "out")
        )

        # 模拟人工修正:把档案标记为 corrected
        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            archive.correction_status = "corrected"
            archive.title = "人工修正后的题名"
            archive.final_metadata = dict(archive.final_metadata or {})
            archive.final_metadata["题名"] = "人工修正后的题名"
            session.commit()

        # 调 force_rerun
        new_metadata = dict(self.payload)
        new_metadata["题名"] = "新规则推出的题名"
        rev_no = recorder.force_rerun_rules_for_archive(
            archive_key="demo",
            new_metadata=new_metadata,
            actor_user_id=None,
            reason="rules_rerun_force",
        )
        self.assertEqual(rev_no, 1)

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.title, "新规则推出的题名")
            self.assertEqual(archive.correction_status, "corrected")
            revs = session.scalars(select(MetadataRevision)).all()
            self.assertGreaterEqual(len(revs), 1)
            self.assertTrue(any(r.field_key == "题名" for r in revs))
            log = session.scalar(select(AuditLog))
            self.assertEqual(log.action, "force_rerun_rules")


if __name__ == "__main__":
    unittest.main()

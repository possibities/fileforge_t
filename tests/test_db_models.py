"""SQLite in-memory smoke test for the phase 1A ORM models.

Skipped automatically if SQLAlchemy is unavailable (base.txt does not include it).
"""

from __future__ import annotations

import unittest


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import (
        ArchivePage,
        ArchiveRecord,
        AuditLog,
        Base,
        ExportFile,
        MetadataRevision,
        ProcessingBatch,
        ProcessingJob,
        ProcessingJobAttempt,
        Project,
        SequenceCounter,
    )
except ImportError as _exc:  # pragma: no cover - exercised when sqlalchemy 不在环境中
    SQLALCHEMY_AVAILABLE = False
    _IMPORT_ERROR = _exc
else:
    SQLALCHEMY_AVAILABLE = True
    _IMPORT_ERROR = None


@unittest.skipUnless(SQLALCHEMY_AVAILABLE, f"sqlalchemy 未安装: {_IMPORT_ERROR}")
class TestDbModels(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_create_full_chain(self):
        with self.Session() as session:
            project = Project(project_key="demo", project_name="demo project")
            session.add(project)
            session.flush()

            batch = ProcessingBatch(
                project_id=project.id,
                batch_key="2026-05-03_demo",
                input_dir="/tmp/in",
                output_dir="/tmp/out",
            )
            session.add(batch)
            session.flush()

            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="folder_a",
                archive_name="folder_a",
                page_count=2,
                image_files=["a/0001.jpg", "a/0002.jpg"],
                image_names=["0001.jpg", "0002.jpg"],
                title="测试题名",
                final_metadata={"题名": "测试题名", "归档年度": "2026"},
            )
            session.add(archive)
            session.flush()

            session.add_all(
                [
                    ArchivePage(
                        archive_id=archive.id,
                        page_no=1,
                        image_path="a/0001.jpg",
                        image_name="0001.jpg",
                        file_hash="deadbeef",
                    ),
                    ArchivePage(
                        archive_id=archive.id,
                        page_no=2,
                        image_path="a/0002.jpg",
                        image_name="0002.jpg",
                        file_hash="cafebabe",
                    ),
                ]
            )
            job = ProcessingJob(
                batch_id=batch.id, archive_id=archive.id, processing_status="success"
            )
            session.add(job)
            session.flush()
            session.add(
                ProcessingJobAttempt(
                    job_id=job.id, attempt_no=1, processing_status="success"
                )
            )
            session.add(
                SequenceCounter(
                    project_id=project.id,
                    archive_year="2026",
                    classification_code="DQL",
                    retention_period_code="D30",
                    current_value=1,
                )
            )
            session.add(
                ExportFile(
                    batch_id=batch.id,
                    export_type="json",
                    file_path="/tmp/out/result.json",
                    row_count=1,
                )
            )
            session.commit()

        with self.Session() as session:
            archive = session.scalar(select(ArchiveRecord))
            self.assertEqual(archive.title, "测试题名")
            self.assertEqual(archive.final_metadata["归档年度"], "2026")
            self.assertEqual(len(archive.image_files), 2)

            counter = session.scalar(select(SequenceCounter))
            self.assertEqual(counter.current_value, 1)

    def test_unique_archive_per_batch(self):
        with self.Session() as session:
            project = Project(project_key="demo")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="b1")
            session.add(batch)
            session.flush()
            session.add(
                ArchiveRecord(
                    project_id=project.id,
                    batch_id=batch.id,
                    archive_key="dup",
                    archive_name="dup",
                )
            )
            session.commit()

            session.add(
                ArchiveRecord(
                    project_id=project.id,
                    batch_id=batch.id,
                    archive_key="dup",
                    archive_name="dup",
                )
            )
            with self.assertRaises(Exception):
                session.commit()

    def test_archive_pages_unique_constraints(self):
        with self.Session() as session:
            project = Project(project_key="pg")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="bp")
            session.add(batch)
            session.flush()
            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="a",
                archive_name="a",
            )
            session.add(archive)
            session.flush()
            session.add(
                ArchivePage(
                    archive_id=archive.id,
                    page_no=1,
                    image_path="/in/a/0001.jpg",
                    image_name="0001.jpg",
                )
            )
            session.commit()

            # 重复 page_no 必须报错
            session.add(
                ArchivePage(
                    archive_id=archive.id,
                    page_no=1,
                    image_path="/in/a/duplicate-page-no.jpg",
                    image_name="duplicate-page-no.jpg",
                )
            )
            with self.assertRaises(Exception):
                session.commit()
            session.rollback()

            # 重复 image_path 必须报错
            session.add(
                ArchivePage(
                    archive_id=archive.id,
                    page_no=99,
                    image_path="/in/a/0001.jpg",
                    image_name="0001.jpg",
                )
            )
            with self.assertRaises(Exception):
                session.commit()

    def test_multiple_null_archive_no_allowed_in_same_project(self):
        # SQLite 与 PostgreSQL 都允许 partial unique 在 NULL 值上重复
        with self.Session() as session:
            project = Project(project_key="np")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="bn")
            session.add(batch)
            session.flush()
            session.add_all(
                [
                    ArchiveRecord(
                        project_id=project.id,
                        batch_id=batch.id,
                        archive_key="x1",
                        archive_name="x1",
                        archive_no=None,
                    ),
                    ArchiveRecord(
                        project_id=project.id,
                        batch_id=batch.id,
                        archive_key="x2",
                        archive_name="x2",
                        archive_no=None,
                    ),
                ]
            )
            session.commit()  # 不应抛异常

    def test_metadata_revision_unique_constraint(self):
        with self.Session() as session:
            project = Project(project_key="rv")
            session.add(project)
            session.flush()
            batch = ProcessingBatch(project_id=project.id, batch_key="rvb")
            session.add(batch)
            session.flush()
            archive = ArchiveRecord(
                project_id=project.id,
                batch_id=batch.id,
                archive_key="rva",
                archive_name="rva",
            )
            session.add(archive)
            session.flush()

            # 同一 (archive_id, revision_no) 下不同 field_key 允许
            session.add_all(
                [
                    MetadataRevision(
                        archive_id=archive.id,
                        revision_no=1,
                        field_key="题名",
                        old_value="a",
                        new_value="b",
                    ),
                    MetadataRevision(
                        archive_id=archive.id,
                        revision_no=1,
                        field_key="责任者",
                        old_value="x",
                        new_value="y",
                    ),
                ]
            )
            session.commit()

            # 同一 (archive_id, revision_no, field_key) 重复必须报错
            session.add(
                MetadataRevision(
                    archive_id=archive.id,
                    revision_no=1,
                    field_key="题名",
                    old_value="a",
                    new_value="c",
                )
            )
            with self.assertRaises(Exception):
                session.commit()

    def test_audit_log_basic_insert(self):
        with self.Session() as session:
            session.add(
                AuditLog(
                    actor_user_id=None,
                    action="login",
                    target_type="user",
                    target_id=42,
                    before_data=None,
                    after_data={"ip": "127.0.0.1"},
                )
            )
            session.commit()

            log = session.scalar(select(AuditLog))
            self.assertEqual(log.action, "login")
            self.assertEqual(log.target_id, 42)
            self.assertEqual(log.after_data, {"ip": "127.0.0.1"})


if __name__ == "__main__":
    unittest.main()

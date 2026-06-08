from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


try:
    from sqlalchemy import select
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import (
        AuditLog,
        Base,
        ProcessingJob,
        Project,
        UploadedFile,
        UploadBatch,
    )
    from web_admin.processing import create_upload_processing_batch
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = _exc
else:
    DEPENDENCIES_AVAILABLE = True
    _IMPORT_ERROR = None


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"processing deps missing: {_IMPORT_ERROR}")
class TestCreateUploadProcessingBatch(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_upload(self, session) -> UploadBatch:
        project = Project(project_key="demo", project_name="Demo")
        session.add(project)
        session.flush()
        upload = UploadBatch(
            project_id=project.id,
            upload_name="demo upload",
            source_type="zip",
            status="uploaded",
            storage_root="/tmp/upload",
        )
        session.add(upload)
        session.flush()
        for idx, document_key in enumerate(["doc_a", "doc_a", "doc_b"], start=1):
            session.add(
                UploadedFile(
                    upload_batch_id=upload.id,
                    original_filename=f"{document_key}_{idx}.jpg",
                    stored_path=f"/tmp/upload/{document_key}/{idx}.jpg",
                    file_ext=".jpg",
                    size_bytes=10,
                    sha256=f"hash-{idx}",
                    page_no=idx,
                    document_key=document_key,
                    status="stored",
                )
            )
        session.flush()
        return upload

    def test_creates_batch_jobs_and_audit_log(self):
        with TemporaryDirectory() as tmpdir, self.Session() as session:
            upload = self._create_upload(session)
            batch = create_upload_processing_batch(
                session,
                upload_batch_id=upload.id,
                output_root=tmpdir,
                actor_user_id=123,
                batch_key="cli_demo",
                batch_key_prefix="cli",
            )
            session.flush()

            jobs = session.scalars(
                select(ProcessingJob).where(ProcessingJob.batch_id == batch.id)
            ).all()
            audits = session.scalars(select(AuditLog)).all()

            self.assertEqual(batch.batch_key, "cli_demo")
            self.assertEqual(batch.total_archives, 2)
            self.assertEqual(batch.total_pages, 3)
            self.assertEqual(batch.output_dir, str(Path(tmpdir) / f"batch_{batch.id}"))
            self.assertEqual(upload.status, "processing")
            self.assertEqual(sorted(job.document_key for job in jobs), ["doc_a", "doc_b"])
            self.assertEqual([job.page_count for job in sorted(jobs, key=lambda j: j.document_key)], [2, 1])
            self.assertEqual(audits[0].action, "processing_started")

    def test_rejects_upload_without_stored_files(self):
        with TemporaryDirectory() as tmpdir, self.Session() as session:
            project = Project(project_key="empty")
            session.add(project)
            session.flush()
            upload = UploadBatch(
                project_id=project.id,
                upload_name="empty",
                source_type="image",
                status="uploaded",
                storage_root="/tmp/empty",
            )
            session.add(upload)
            session.flush()

            with self.assertRaises(ValueError):
                create_upload_processing_batch(
                    session,
                    upload_batch_id=upload.id,
                    output_root=tmpdir,
                )


if __name__ == "__main__":
    unittest.main()

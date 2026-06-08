from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace


try:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from infrastructure.db.models import Base, Project, UploadBatch, UploadedFile
    from web_admin.upload_storage import ingest_upload_files
except ImportError as _exc:  # pragma: no cover
    DEPENDENCIES_AVAILABLE = False
    _IMPORT_ERROR = _exc
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


def _fake_upload(filename: str, payload: bytes, content_type: str = "image/jpeg"):
    return SimpleNamespace(
        filename=filename,
        file=io.BytesIO(payload),
        content_type=content_type,
    )


def _zip_upload(entries: dict[str, bytes]):
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    payload.seek(0)
    return SimpleNamespace(
        filename="archives.zip",
        file=payload,
        content_type="application/zip",
    )


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, f"deps missing: {_IMPORT_ERROR}")
class TestUploadStorage(unittest.TestCase):
    def setUp(self):
        self.engine = _make_engine()
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create_upload_batch(self, session) -> UploadBatch:
        project = Project(project_key="p")
        session.add(project)
        session.flush()
        upload = UploadBatch(
            project_id=project.id,
            upload_name="demo",
            source_type="images",
            storage_root="/tmp/upload",
        )
        session.add(upload)
        session.flush()
        return upload

    def test_image_uploads_are_grouped_as_one_document(self):
        with tempfile.TemporaryDirectory() as tmpdir, self.Session() as session:
            upload = self._create_upload_batch(session)
            result = ingest_upload_files(
                session,
                upload_batch_id=upload.id,
                upload_root=Path(tmpdir),
                upload_name="demo document",
                files=[
                    _fake_upload("001.jpg", b"aaa"),
                    _fake_upload("002.png", b"bbbb", "image/png"),
                ],
                max_total_bytes=100,
                max_file_count=10,
            )

            self.assertEqual(result.file_count, 2)
            self.assertEqual(result.document_count, 1)
            self.assertEqual(result.total_size_bytes, 7)
            rows = session.scalars(select(UploadedFile)).all()
            self.assertEqual({row.document_key for row in rows}, {"demo document"})

    def test_zip_upload_groups_by_top_level_folder_and_skips_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir, self.Session() as session:
            upload = self._create_upload_batch(session)
            upload_file = _zip_upload(
                {
                    "archive_a/001.jpg": b"a",
                    "archive_b/001.png": b"bb",
                    "../escape.jpg": b"bad",
                    "notes.txt": b"ignored",
                }
            )
            result = ingest_upload_files(
                session,
                upload_batch_id=upload.id,
                upload_root=Path(tmpdir),
                upload_name="ignored",
                files=[upload_file],
                max_total_bytes=100,
                max_file_count=10,
            )

            self.assertEqual(result.source_type, "zip")
            self.assertEqual(result.file_count, 2)
            self.assertEqual(result.document_count, 2)
            rows = session.scalars(select(UploadedFile)).all()
            self.assertEqual({row.document_key for row in rows}, {"archive_a", "archive_b"})
            upload_root = Path(tmpdir).resolve()
            for row in rows:
                self.assertTrue(Path(row.stored_path).resolve().is_relative_to(upload_root))

    def test_total_size_limit_is_enforced(self):
        with tempfile.TemporaryDirectory() as tmpdir, self.Session() as session:
            upload = self._create_upload_batch(session)
            with self.assertRaises(ValueError):
                ingest_upload_files(
                    session,
                    upload_batch_id=upload.id,
                    upload_root=Path(tmpdir),
                    upload_name="demo",
                    files=[_fake_upload("001.jpg", b"too-large")],
                    max_total_bytes=3,
                    max_file_count=10,
                )


if __name__ == "__main__":
    unittest.main()

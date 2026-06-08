from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from config.config import Config
from core.classifier import ArchiveClassifier
from infrastructure.db import repositories
from infrastructure.db.engine import dispose_engine, make_engine, make_session_factory
from infrastructure.db.models import ProcessingBatch, ProcessingJob, UploadedFile, UploadBatch
from infrastructure.db.recorder import BatchRecorder
from processors.batch_processor import BatchProcessor
from processors.exporter import Exporter


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UploadRunContext:
    project_key: str
    project_name: str | None
    created_by: int | None
    organization_id: int | None
    input_dir: str
    output_dir: str


def run_upload_processing_batch(
    *,
    database_url: str,
    upload_batch_id: int,
    batch_key: str,
    output_root: str,
) -> bool:
    """Run OCR/LLM/rules for one uploaded batch in a FastAPI background task.

    This is intentionally a simple in-process worker for the graduation project.
    It keeps Web request latency short while reusing the same BatchProcessor path
    as ``main.py``.
    """
    engine = None
    session_factory = None
    try:
        engine = make_engine(database_url)
        session_factory = make_session_factory(engine)
        context = _prepare_run_context(
            session_factory,
            upload_batch_id=upload_batch_id,
            batch_key=batch_key,
            output_root=output_root,
        )

        Path(context.output_dir).mkdir(parents=True, exist_ok=True)
        Exporter.initialize(Config.EXPORTER_CONFIG_PATH)
        classifier = ArchiveClassifier(
            ocr_lang=Config.OCR_LANG,
            model_name=Config.LLM_MODEL_NAME,
        )
        recorder = BatchRecorder(
            engine=engine,
            session_factory=session_factory,
            project_key=context.project_key,
            project_name=context.project_name,
            batch_key=batch_key,
            rerun_policy="rerun-all",
            summary_schema_version=BatchProcessor.SUMMARY_SCHEMA_VERSION,
            summary_schema_ref=BatchProcessor.SUMMARY_SCHEMA_REF,
            summary_changelog_ref=BatchProcessor.SUMMARY_CHANGELOG_REF,
            input_dir=context.input_dir,
            output_dir=context.output_dir,
            upload_batch_id=upload_batch_id,
            trigger_type="web_upload",
            organization_id=context.organization_id,
            created_by=context.created_by,
        )
        processor = BatchProcessor(classifier, recorder=recorder)
        results = processor.process_directory(context.input_dir, context.output_dir)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_output = Path(context.output_dir) / f"archive_results_{timestamp}.json"
        csv_output = Path(context.output_dir) / f"archive_results_{timestamp}.csv"
        json_written = Exporter.export_to_json(results, str(json_output))
        csv_written = Exporter.export_to_csv(results, str(csv_output))
        recorder.record_export(
            export_type="json",
            file_path=str(json_output),
            template_name="default",
            row_count=json_written,
        )
        recorder.record_export(
            export_type="csv",
            file_path=str(csv_output),
            template_name="default",
            row_count=csv_written,
        )

        _mark_upload_processed(session_factory, upload_batch_id=upload_batch_id)
        return True
    except Exception as exc:
        logger.exception("[Web processing] upload_batch_id=%s failed: %s", upload_batch_id, exc)
        if session_factory is not None:
            _mark_run_failed(
                session_factory,
                upload_batch_id=upload_batch_id,
                batch_key=batch_key,
                error_message=str(exc),
            )
        return False
    finally:
        dispose_engine(engine)


def create_upload_processing_batch(
    session: Session,
    *,
    upload_batch_id: int,
    output_root: str,
    actor_user_id: Optional[int] = None,
    batch_key: Optional[str] = None,
    batch_key_prefix: str = "web",
) -> ProcessingBatch:
    """Create the DB-side processing batch/jobs for an uploaded batch.

    The caller owns transaction boundaries. This function is shared by the Web
    route and the CLI runner so both entry points build identical job state.
    """
    upload = session.get(UploadBatch, upload_batch_id)
    if upload is None:
        raise LookupError(f"upload batch not found: {upload_batch_id}")
    if upload.status not in {"uploaded", "validated", "failed"}:
        raise ValueError(f"当前上传状态不能启动处理: {upload.status}")

    project = upload.project
    if project is None:
        raise LookupError(f"project not found for upload batch: {upload_batch_id}")

    resolved_batch_key = batch_key or (
        f"{batch_key_prefix}_{upload.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    batch = repositories.get_or_create_batch(
        session,
        project_id=project.id,
        upload_batch_id=upload.id,
        batch_key=resolved_batch_key,
        batch_name=upload.upload_name,
        trigger_type="web_upload",
        input_dir=upload.storage_root,
        output_dir=None,
        organization_id=project.organization_id,
        created_by=actor_user_id,
        summary_schema_version=BatchProcessor.SUMMARY_SCHEMA_VERSION,
        summary_schema_ref=BatchProcessor.SUMMARY_SCHEMA_REF,
        summary_changelog_ref=BatchProcessor.SUMMARY_CHANGELOG_REF,
    )
    batch.output_dir = str(Path(output_root) / f"batch_{batch.id}")

    rows = session.scalars(
        select(UploadedFile)
        .where(UploadedFile.upload_batch_id == upload.id, UploadedFile.status == "stored")
        .order_by(UploadedFile.document_key.asc(), UploadedFile.page_no.asc().nullslast())
    ).all()
    document_counts: dict[str, int] = {}
    for row in rows:
        document_counts[row.document_key] = document_counts.get(row.document_key, 0) + 1
    if not document_counts:
        raise ValueError("上传批次没有可处理文件")

    for document_key, page_count in document_counts.items():
        existing = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.batch_id == batch.id,
                ProcessingJob.document_key == document_key,
            )
        )
        if existing is None:
            session.add(
                ProcessingJob(
                    batch_id=batch.id,
                    project_id=project.id,
                    upload_batch_id=upload.id,
                    document_key=document_key,
                    processing_status="queued",
                    progress=0,
                    page_count=page_count,
                )
            )

    batch.total_archives = len(document_counts)
    batch.total_pages = len(rows)
    upload.status = "processing"
    repositories.record_audit_log(
        session,
        actor_user_id=actor_user_id,
        organization_id=project.organization_id,
        project_id=project.id,
        action="processing_started",
        target_type="batch",
        target_id=batch.id,
        message=f"启动在线跑批 {batch.batch_key}",
        payload={"upload_batch_id": upload.id},
    )
    return batch


def _prepare_run_context(
    session_factory,
    *,
    upload_batch_id: int,
    batch_key: str,
    output_root: str,
) -> UploadRunContext:
    with session_factory() as session:
        upload = session.get(UploadBatch, upload_batch_id)
        if upload is None:
            raise RuntimeError(f"upload batch not found: {upload_batch_id}")
        batch = session.scalar(
            select(ProcessingBatch).where(
                ProcessingBatch.project_id == upload.project_id,
                ProcessingBatch.batch_key == batch_key,
            )
        )
        if batch is None:
            raise RuntimeError(f"processing batch not found: {batch_key}")
        project = upload.project
        output_dir = str(Path(output_root) / f"batch_{batch.id}")
        batch.output_dir = output_dir
        batch.batch_status = "running"
        upload.status = "processing"
        session.commit()

        return UploadRunContext(
            project_key=project.project_key,
            project_name=project.project_name,
            created_by=upload.uploaded_by,
            organization_id=project.organization_id,
            input_dir=upload.storage_root,
            output_dir=output_dir,
        )


def _mark_upload_processed(session_factory, *, upload_batch_id: int) -> None:
    with session_factory() as session:
        upload = session.get(UploadBatch, upload_batch_id)
        if upload is not None:
            upload.status = "processed"
        session.commit()


def _mark_run_failed(
    session_factory,
    *,
    upload_batch_id: int,
    batch_key: str,
    error_message: str,
) -> None:
    with session_factory() as session:
        upload = session.get(UploadBatch, upload_batch_id)
        if upload is not None:
            upload.status = "failed"
            upload.error_message = error_message

        batch = session.scalar(
            select(ProcessingBatch).where(ProcessingBatch.batch_key == batch_key)
        )
        if batch is not None:
            batch.batch_status = "failed"
            batch.finished_at = datetime.now(timezone.utc)
            batch.failure_breakdown = {"WEB_PROCESSING_EXCEPTION": 1}
        session.commit()

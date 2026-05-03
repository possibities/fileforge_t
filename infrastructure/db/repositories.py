"""窄查询函数集合，封装 ORM 细节，对外只暴露领域语义。

调用方负责传入 session、控制事务边界（commit/rollback）；本模块不做 commit。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.sequence_generator import SequenceGenerator

from .models import (
    ArchivePage,
    ArchiveRecord,
    ExportFile,
    ProcessingBatch,
    ProcessingJob,
    ProcessingJobAttempt,
    Project,
    SequenceCounter,
)

logger = logging.getLogger(__name__)


# 中文 metadata key -> archive_records 英文冗余列
_REDUNDANT_COLUMN_MAP: dict[str, str] = {
    "门类": "category_code",
    "归档年度": "archive_year",
    "实体分类号": "classification_code",
    "实体分类名称": "classification_name",
    "保管期限": "retention_period",
    "责任者": "responsible_party",
    "文件编号": "document_number",
    "题名": "title",
    "文件形成时间": "document_date",
    "密级": "security_level",
    "保密期限": "secret_period",
    "开放状态": "openness_status",
    "延期开放理由": "openness_delay_reason",
    "立档单位名称": "fonds_unit_name",
    "数字化时间": "digitized_time",
    "档号": "archive_no",
    "件号": "item_no",
}


# ── 项目 ─────────────────────────────────────────────────────────────────────
def get_or_create_project(
    session: Session,
    project_key: str,
    project_name: Optional[str] = None,
) -> Project:
    if not project_key:
        raise ValueError("project_key 不能为空")

    project = session.scalar(select(Project).where(Project.project_key == project_key))
    if project is not None:
        if project.status != "active":
            logger.warning(
                "[DB] 项目 %s 当前状态为 %s，禁止新建批次", project_key, project.status
            )
            raise RuntimeError(f"项目 {project_key} 状态为 {project.status}，禁止使用")
        return project

    project = Project(
        project_key=project_key,
        project_name=project_name or project_key,
        status="active",
        preserve_existing_numbers_on_rerun=True,
        numbering_rule={
            "scheme": "by_year_classification_retention",
            "retention_code_cutoff_year": SequenceGenerator._CUTOFF_YEAR,
        },
    )
    session.add(project)
    session.flush()
    logger.info("[DB] 已创建项目 project_key=%s id=%s", project_key, project.id)
    return project


# ── 批次 ─────────────────────────────────────────────────────────────────────
def get_or_create_batch(
    session: Session,
    *,
    project_id: int,
    batch_key: str,
    input_dir: Optional[str],
    output_dir: Optional[str],
    summary_schema_version: Optional[str] = None,
    summary_schema_ref: Optional[str] = None,
    summary_changelog_ref: Optional[str] = None,
) -> ProcessingBatch:
    if not batch_key:
        raise ValueError("batch_key 不能为空")

    batch = session.scalar(
        select(ProcessingBatch).where(
            ProcessingBatch.project_id == project_id,
            ProcessingBatch.batch_key == batch_key,
        )
    )
    if batch is None:
        batch = ProcessingBatch(
            project_id=project_id,
            batch_key=batch_key,
            input_dir=input_dir,
            output_dir=output_dir,
            batch_status="running",
            summary_schema_version=summary_schema_version,
            summary_schema_ref=summary_schema_ref,
            summary_changelog_ref=summary_changelog_ref,
        )
        session.add(batch)
        session.flush()
        logger.info("[DB] 已创建批次 project_id=%s batch_key=%s id=%s", project_id, batch_key, batch.id)
    else:
        batch.batch_status = "running"
        batch.input_dir = input_dir or batch.input_dir
        batch.output_dir = output_dir or batch.output_dir
        if summary_schema_version:
            batch.summary_schema_version = summary_schema_version
        if summary_schema_ref:
            batch.summary_schema_ref = summary_schema_ref
        if summary_changelog_ref:
            batch.summary_changelog_ref = summary_changelog_ref
        logger.info("[DB] 复用已有批次 id=%s, 状态切回 running", batch.id)

    return batch


def update_batch_progress(
    session: Session,
    *,
    batch_id: int,
    total_archives: int,
    total_pages: int,
    started_at: Optional[datetime] = None,
) -> None:
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        raise RuntimeError(f"batch id={batch_id} 不存在")
    batch.total_archives = total_archives
    batch.total_pages = total_pages
    if started_at is not None and batch.started_at is None:
        batch.started_at = started_at


def finalize_batch(
    session: Session,
    *,
    batch_id: int,
    success_count: int,
    fail_count: int,
    failure_breakdown: dict[str, int],
    batch_status: str = "completed",
    finished_at: Optional[datetime] = None,
) -> None:
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        raise RuntimeError(f"batch id={batch_id} 不存在")
    batch.success_count = success_count
    batch.fail_count = fail_count
    batch.failure_breakdown = dict(failure_breakdown) if failure_breakdown else {}
    batch.batch_status = batch_status
    batch.finished_at = finished_at or datetime.now(timezone.utc)


# ── 档案 ─────────────────────────────────────────────────────────────────────
def upsert_archive(
    session: Session,
    *,
    project_id: int,
    batch_id: int,
    archive_key: str,
    archive_name: str,
    source_folder: Optional[str],
    page_count: int,
    image_files: list[str],
    image_names: list[str],
    processed_time: Optional[str],
) -> ArchiveRecord:
    archive = session.scalar(
        select(ArchiveRecord).where(
            ArchiveRecord.batch_id == batch_id,
            ArchiveRecord.archive_key == archive_key,
        )
    )
    if archive is None:
        archive = ArchiveRecord(
            project_id=project_id,
            batch_id=batch_id,
            archive_key=archive_key,
            archive_name=archive_name,
            source_folder=source_folder,
            page_count=page_count,
            image_files=image_files,
            image_names=image_names,
            processed_time=processed_time,
            processing_status="pending",
        )
        session.add(archive)
        session.flush()
    else:
        archive.archive_name = archive_name
        archive.source_folder = source_folder
        archive.page_count = page_count
        archive.image_files = image_files
        archive.image_names = image_names
        if processed_time:
            archive.processed_time = processed_time
        # 重跑前重置状态为 running，但若已 corrected 不动
        if archive.correction_status != "corrected":
            archive.processing_status = "pending"
            archive.error_code = None
            archive.error_message = None
            archive.traceback_text = None

    return archive


def find_existing_success(
    session: Session,
    *,
    batch_id: int,
    archive_key: str,
) -> Optional[ArchiveRecord]:
    return session.scalar(
        select(ArchiveRecord).where(
            ArchiveRecord.batch_id == batch_id,
            ArchiveRecord.archive_key == archive_key,
            ArchiveRecord.processing_status == "success",
        )
    )


def upsert_pages(
    session: Session,
    *,
    archive_id: int,
    image_paths: Iterable[str],
) -> None:
    existing = {
        page.image_path: page
        for page in session.scalars(
            select(ArchivePage).where(ArchivePage.archive_id == archive_id)
        ).all()
    }

    for idx, image_path in enumerate(image_paths, start=1):
        path_obj = Path(image_path)
        image_name = path_obj.name
        file_hash = _hash_file_safely(path_obj)
        file_size = _stat_size_safely(path_obj)

        if image_path in existing:
            page = existing[image_path]
            page.page_no = idx
            page.image_name = image_name
            if file_hash:
                page.file_hash = file_hash
            if file_size is not None:
                page.file_size = file_size
        else:
            session.add(
                ArchivePage(
                    archive_id=archive_id,
                    page_no=idx,
                    image_path=image_path,
                    image_name=image_name,
                    file_hash=file_hash,
                    file_size=file_size,
                )
            )


def _hash_file_safely(path: Path) -> Optional[str]:
    try:
        if not path.exists() or not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        logger.warning("[DB] 计算 hash 失败 %s: %s", path, exc)
        return None


def _stat_size_safely(path: Path) -> Optional[int]:
    try:
        if not path.exists() or not path.is_file():
            return None
        return path.stat().st_size
    except OSError:
        return None


# ── 处理任务 ─────────────────────────────────────────────────────────────────
def record_job_start(
    session: Session,
    *,
    batch_id: int,
    archive_id: int,
    job_type: str = "archive_classify",
) -> ProcessingJob:
    job = ProcessingJob(
        batch_id=batch_id,
        archive_id=archive_id,
        job_type=job_type,
        processing_status="running",
        attempt_count=0,
        started_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.flush()
    return job


def record_job_attempt(
    session: Session,
    *,
    job: ProcessingJob,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    traceback_text: Optional[str] = None,
) -> ProcessingJobAttempt:
    job.attempt_count = (job.attempt_count or 0) + 1
    attempt = ProcessingJobAttempt(
        job_id=job.id,
        attempt_no=job.attempt_count,
        processing_status=status,
        error_code=error_code,
        error_message=error_message,
        traceback_text=traceback_text,
        started_at=job.started_at,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(attempt)

    job.processing_status = status
    job.last_error_code = error_code
    job.last_error_message = error_message
    job.finished_at = attempt.finished_at
    return attempt


# ── 应用分类结果 ─────────────────────────────────────────────────────────────
def apply_classification_result(
    session: Session,
    *,
    archive: ArchiveRecord,
    final_metadata: dict[str, Any],
    rules_metadata: Optional[dict[str, Any]] = None,
    llm_metadata: Optional[dict[str, Any]] = None,
    force_rerun_rules: bool = False,
) -> None:
    """把 metadata 写入快照与冗余列。

    若 archive.correction_status == 'corrected' 且未传 force_rerun_rules，则只刷新
    rules_metadata/llm_metadata 与失败诊断字段，**不覆盖** final_metadata 与冗余列；
    见数据契约 §5.1。
    """
    if llm_metadata is not None:
        archive.llm_metadata = llm_metadata
    if rules_metadata is not None:
        archive.rules_metadata = rules_metadata

    protect = archive.correction_status == "corrected" and not force_rerun_rules
    if protect:
        logger.info(
            "[DB] archive id=%s 已 corrected，跳过 final_metadata 覆盖", archive.id
        )
        return

    archive.final_metadata = final_metadata
    archive.retention_period_code = _resolve_retention_code(
        final_metadata.get("归档年度"),
        final_metadata.get("保管期限"),
    )
    for key, column in _REDUNDANT_COLUMN_MAP.items():
        value = final_metadata.get(key)
        if value is not None:
            value = str(value)
        setattr(archive, column, value)


def _resolve_retention_code(year_value: Any, retention_period: Any) -> Optional[str]:
    if not year_value or not retention_period:
        return None
    try:
        year = int(str(year_value).strip())
    except (TypeError, ValueError):
        return None
    period = str(retention_period).strip()
    mapping = (
        SequenceGenerator._PERIOD_CODE_NEW
        if year >= SequenceGenerator._CUTOFF_YEAR
        else SequenceGenerator._PERIOD_CODE_OLD
    )
    return mapping.get(period)


def mark_archive_status(
    session: Session,
    *,
    archive: ArchiveRecord,
    status: str,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    traceback_text: Optional[str] = None,
    review_status: Optional[str] = None,
    result_filename: Optional[str] = None,
) -> None:
    archive.processing_status = status
    archive.error_code = error_code
    archive.error_message = error_message
    archive.traceback_text = traceback_text
    if review_status is not None:
        archive.review_status = review_status
    if result_filename is not None:
        archive.result_filename = result_filename


# ── 件号续号 ─────────────────────────────────────────────────────────────────
def assign_sequence(
    session: Session,
    *,
    project_id: int,
    archive_year: str,
    classification_code: str,
    retention_period_code: str,
) -> Tuple[str, str]:
    """事务内行锁 + 递增；返回 (item_no, archive_no)。

    item_no = 4 位补零字符串，与 core/sequence_generator.py 兼容。
    archive_no = "{year}-{classification}-{period_code}-{item}"。
    """
    counter = session.scalar(
        select(SequenceCounter)
        .where(
            SequenceCounter.project_id == project_id,
            SequenceCounter.archive_year == archive_year,
            SequenceCounter.classification_code == classification_code,
            SequenceCounter.retention_period_code == retention_period_code,
        )
        .with_for_update()
    )
    if counter is None:
        counter = SequenceCounter(
            project_id=project_id,
            archive_year=archive_year,
            classification_code=classification_code,
            retention_period_code=retention_period_code,
            current_value=0,
        )
        session.add(counter)
        session.flush()

    counter.current_value = (counter.current_value or 0) + 1
    item_no = f"{counter.current_value:04d}"
    archive_no = f"{archive_year}-{classification_code}-{retention_period_code}-{item_no}"
    return item_no, archive_no


# ── 导出文件 ─────────────────────────────────────────────────────────────────
def record_export_file(
    session: Session,
    *,
    batch_id: int,
    export_type: str,
    file_path: str,
    template_name: Optional[str] = None,
    row_count: Optional[int] = None,
    file_hash: Optional[str] = None,
) -> ExportFile:
    record = ExportFile(
        batch_id=batch_id,
        export_type=export_type,
        file_path=file_path,
        template_name=template_name,
        row_count=row_count,
        file_hash=file_hash,
    )
    session.add(record)
    session.flush()
    return record


__all__ = [
    "get_or_create_project",
    "get_or_create_batch",
    "update_batch_progress",
    "finalize_batch",
    "upsert_archive",
    "find_existing_success",
    "upsert_pages",
    "record_job_start",
    "record_job_attempt",
    "apply_classification_result",
    "mark_archive_status",
    "assign_sequence",
    "record_export_file",
]

"""SQLAlchemy 2.x ORM 模型，对应数据契约 §4.1/4.3/4.4/4.5/4.6/4.8/4.9。

设计约定：
  - JSONB 列在 PostgreSQL 上落 JSONB，在其他 dialect（SQLite 单测）降级为 JSON。
  - 时间列统一用 TIMESTAMP WITH TIME ZONE。
  - 枚举使用 native_enum=False，落到 VARCHAR + CHECK，便于后续 ALTER 不跨 PG enum 维护。
  - 中文 metadata key 不进 ORM 字段名，所有冗余列用英文 key（与数据契约 §2.3 表对齐）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ── JSONB / JSON variant ─────────────────────────────────────────────────────
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


# ── 枚举取值（落 VARCHAR + CHECK，便于平滑扩展） ─────────────────────────────
PROJECT_STATUS = ("active", "disabled", "archived")
BATCH_STATUS = ("running", "completed", "aborted")
PROCESSING_STATUS = ("pending", "running", "success", "failed", "error")
REVIEW_STATUS = ("not_required", "needs_review", "in_review", "confirmed")
CORRECTION_STATUS = ("none", "corrected")
LLM_PARSE_STRATEGY = ("json", "repaired", "regex", "failed")


def _ts_col(*, server_default: bool = True) -> Mapped[datetime]:
    if server_default:
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            nullable=False,
        )
    return mapped_column(DateTime(timezone=True), nullable=False)


def _ts_col_optional() -> Mapped[Optional[datetime]]:
    return mapped_column(DateTime(timezone=True), nullable=True)


# ── 项目 ─────────────────────────────────────────────────────────────────────
class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_key: Mapped[str] = mapped_column(String(128), nullable=False)
    project_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(*PROJECT_STATUS, name="project_status", native_enum=False, length=32),
        nullable=False,
        server_default="active",
    )
    numbering_rule: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    preserve_existing_numbers_on_rerun: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    # 平台阶段预留
    organization_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("project_key", name="uq_projects_project_key"),
        Index("ix_projects_status", "status"),
    )


# ── 批次 ─────────────────────────────────────────────────────────────────────
class ProcessingBatch(Base):
    __tablename__ = "processing_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    batch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    input_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    batch_status: Mapped[str] = mapped_column(
        Enum(*BATCH_STATUS, name="batch_status", native_enum=False, length=32),
        nullable=False,
        server_default="running",
    )
    started_at: Mapped[Optional[datetime]] = _ts_col_optional()
    finished_at: Mapped[Optional[datetime]] = _ts_col_optional()

    total_archives: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    failure_breakdown: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)

    summary_schema_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    summary_schema_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    summary_changelog_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    organization_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    project: Mapped[Project] = relationship(Project)

    __table_args__ = (
        UniqueConstraint("project_id", "batch_key", name="uq_batches_project_batch_key"),
        Index("ix_batches_status_started", "batch_status", "started_at"),
        Index("ix_batches_project_status_started", "project_id", "batch_status", "started_at"),
    )


# ── 档案 ─────────────────────────────────────────────────────────────────────
class ArchiveRecord(Base):
    __tablename__ = "archive_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="RESTRICT"), nullable=False
    )

    archive_key: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_name: Mapped[str] = mapped_column(String(512), nullable=False)
    archive_folder_name: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    source_folder: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    image_files: Mapped[Optional[list[str]]] = mapped_column(JsonDoc, nullable=True)
    image_names: Mapped[Optional[list[str]]] = mapped_column(JsonDoc, nullable=True)
    result_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    processing_status: Mapped[str] = mapped_column(
        Enum(*PROCESSING_STATUS, name="processing_status", native_enum=False, length=32),
        nullable=False,
        server_default="pending",
    )
    review_status: Mapped[str] = mapped_column(
        Enum(*REVIEW_STATUS, name="review_status", native_enum=False, length=32),
        nullable=False,
        server_default="not_required",
    )
    correction_status: Mapped[str] = mapped_column(
        Enum(*CORRECTION_STATUS, name="correction_status", native_enum=False, length=32),
        nullable=False,
        server_default="none",
    )

    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    traceback_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_time: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # 冗余查询列（数据契约 §2.3、§4.4）
    category_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    archive_year: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    classification_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    classification_name: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    retention_period: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    retention_period_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    responsible_party: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    document_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    document_date: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    security_level: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    secret_period: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    openness_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    openness_delay_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    fonds_unit_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # 数字化时间是 "YYYY年M月" 中文字符串，不是时间戳
    digitized_time: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    archive_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    item_no: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    # JSONB 快照
    llm_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    rules_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    final_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)

    # LLM 原始响应（数据契约 §4.4 可选列）
    llm_raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_cleaned_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_parse_strategy: Mapped[Optional[str]] = mapped_column(
        Enum(*LLM_PARSE_STRATEGY, name="llm_parse_strategy", native_enum=False, length=16),
        nullable=True,
    )

    organization_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("batch_id", "archive_key", name="uq_archive_batch_key"),
        Index("ix_archive_batch_processing", "batch_id", "processing_status"),
        Index("ix_archive_batch_review", "batch_id", "review_status"),
        Index("ix_archive_archive_year", "archive_year"),
        Index("ix_archive_classification_code", "classification_code"),
        Index("ix_archive_retention_period", "retention_period"),
        Index("ix_archive_openness_status", "openness_status"),
        Index("ix_archive_archive_no", "archive_no"),
        Index("ix_archive_item_no", "item_no"),
        # 项目内档号唯一（partial unique 在 PG 上 work，在 SQLite 上忽略 postgresql_where 退化为全表唯一，
        # SQLite 单测中允许 archive_no=NULL 多条不冲突，因为 SQLite 把多个 NULL 视为不重复）
        Index(
            "uq_archive_no_per_project",
            "project_id",
            "archive_no",
            unique=True,
            postgresql_where=text("archive_no IS NOT NULL"),
        ),
    )


# ── 页面 ─────────────────────────────────────────────────────────────────────
class ArchivePage(Base):
    __tablename__ = "archive_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[int] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), nullable=False
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 阶段 4 字段，先预留可空
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_avg_confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    ocr_low_conf_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ocr_variant: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("archive_id", "page_no", name="uq_archive_page_no"),
        UniqueConstraint("archive_id", "image_path", name="uq_archive_page_path"),
    )


# ── 处理任务 ─────────────────────────────────────────────────────────────────
class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="RESTRICT"), nullable=False
    )
    archive_id: Mapped[int] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="archive_classify"
    )
    processing_status: Mapped[str] = mapped_column(
        Enum(*PROCESSING_STATUS, name="processing_status_job", native_enum=False, length=32),
        nullable=False,
        server_default="pending",
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = _ts_col_optional()
    finished_at: Mapped[Optional[datetime]] = _ts_col_optional()
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        Index("ix_jobs_batch_status", "batch_id", "processing_status"),
    )


class ProcessingJobAttempt(Base):
    __tablename__ = "processing_job_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    processing_status: Mapped[str] = mapped_column(
        Enum(*PROCESSING_STATUS, name="processing_status_attempt", native_enum=False, length=32),
        nullable=False,
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    traceback_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = _ts_col_optional()
    finished_at: Mapped[Optional[datetime]] = _ts_col_optional()
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("job_id", "attempt_no", name="uq_attempt_no_per_job"),
    )


# ── 件号计数器 ───────────────────────────────────────────────────────────────
class SequenceCounter(Base):
    __tablename__ = "sequence_counters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    archive_year: Mapped[str] = mapped_column(String(8), nullable=False)
    classification_code: Mapped[str] = mapped_column(String(16), nullable=False)
    retention_period_code: Mapped[str] = mapped_column(String(8), nullable=False)
    current_value: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    organization_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "archive_year",
            "classification_code",
            "retention_period_code",
            name="uq_sequence_counter_scope",
        ),
    )


# ── 导出文件 ─────────────────────────────────────────────────────────────────
class ExportFile(Base):
    __tablename__ = "export_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="RESTRICT"), nullable=False
    )
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    template_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _ts_col()


__all__ = [
    "Base",
    "JsonDoc",
    "PROJECT_STATUS",
    "BATCH_STATUS",
    "PROCESSING_STATUS",
    "REVIEW_STATUS",
    "CORRECTION_STATUS",
    "LLM_PARSE_STRATEGY",
    "Project",
    "ProcessingBatch",
    "ArchiveRecord",
    "ArchivePage",
    "ProcessingJob",
    "ProcessingJobAttempt",
    "SequenceCounter",
    "ExportFile",
]

"""SQLAlchemy 2.x ORM models for the rebuilt upload + online processing schema.

The project has no production data, so the schema is intentionally rebuilt around
the runtime flow used by the web UI:

    upload_batches -> uploaded_files -> processing_batches/jobs/events
    -> archive_records/pages/llm_traces -> revisions/audit/export

Some legacy attribute names are kept where they are already used by the Web
templates and repositories, for example ``project_name``, ``batch_status`` and
``total_archives``. They are now compatibility names over the new product model,
not separate old concepts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
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


JsonDoc = JSON().with_variant(JSONB(), "postgresql")


PROJECT_STATUS = ("active", "archived", "disabled")
UPLOAD_BATCH_STATUS = ("uploading", "uploaded", "validated", "processing", "processed", "failed")
UPLOADED_FILE_STATUS = ("stored", "invalid")
BATCH_STATUS = (
    "queued",
    "running",
    "success",
    "partial_failed",
    "failed",
    "cancelled",
    # Compatibility with the previous CLI/Web wording.
    "completed",
    "aborted",
)
PROCESSING_STATUS = (
    "pending",
    "queued",
    "running",
    "ocr_running",
    "llm_running",
    "rules_running",
    "exporting",
    "success",
    "failed",
    "cancelled",
    "error",
)
REVIEW_STATUS = (
    "pending",
    "needs_review",
    "reviewed",
    # Compatibility with the previous review-state vocabulary.
    "not_required",
    "in_review",
    "confirmed",
)
CORRECTION_STATUS = ("none", "corrected")
LLM_PARSE_STRATEGY = ("json", "repaired", "regex", "failed")
ORGANIZATION_STATUS = ("active", "disabled")
APP_USER_STATUS = ("active", "disabled")
APP_USER_ROLE = ("platform_admin", "org_admin", "org_operator")


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


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(*ORGANIZATION_STATUS, name="organization_status", native_enum=False, length=32),
        nullable=False,
        server_default="active",
    )
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("name", name="uq_organizations_name"),
        UniqueConstraint("code", name="uq_organizations_code"),
        Index("ix_organizations_status", "status"),
    )


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(
        Enum(*APP_USER_ROLE, name="app_user_role", native_enum=False, length=32),
        nullable=False,
        server_default="org_operator",
    )
    status: Mapped[str] = mapped_column(
        Enum(*APP_USER_STATUS, name="app_user_status", native_enum=False, length=32),
        nullable=False,
        server_default="active",
    )
    last_login_at: Mapped[Optional[datetime]] = _ts_col_optional()
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    organization: Mapped[Optional[Organization]] = relationship(Organization)

    __table_args__ = (
        UniqueConstraint("username", name="uq_app_users_username"),
        Index("ix_app_users_status", "status"),
        Index("ix_app_users_organization", "organization_id"),
        Index("ix_app_users_role", "role"),
    )


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("app_users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    csrf_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = _ts_col_optional()
    created_at: Mapped[datetime] = _ts_col()
    last_seen_at: Mapped[datetime] = _ts_col()

    user: Mapped[AppUser] = relationship(AppUser)

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
        Index("ix_web_sessions_user", "user_id"),
        Index("ix_web_sessions_expires", "expires_at"),
        Index("ix_web_sessions_revoked", "revoked_at"),
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
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
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    organization: Mapped[Optional[Organization]] = relationship(Organization)

    __table_args__ = (
        UniqueConstraint("project_key", name="uq_projects_project_key"),
        Index("ix_projects_status", "status"),
        Index("ix_projects_organization", "organization_id"),
    )


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    upload_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="images")
    status: Mapped[str] = mapped_column(
        Enum(*UPLOAD_BATCH_STATUS, name="upload_batch_status", native_enum=False, length=32),
        nullable=False,
        server_default="uploading",
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    storage_root: Mapped[str] = mapped_column(Text, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    project: Mapped[Project] = relationship(Project)

    __table_args__ = (
        Index("ix_upload_batches_project_created", "project_id", "created_at"),
        Index("ix_upload_batches_status", "status"),
    )


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_batch_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_ext: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    page_no: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    document_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(*UPLOADED_FILE_STATUS, name="uploaded_file_status", native_enum=False, length=32),
        nullable=False,
        server_default="stored",
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_col()

    upload_batch: Mapped[UploadBatch] = relationship(UploadBatch)

    __table_args__ = (
        UniqueConstraint("upload_batch_id", "stored_path", name="uq_uploaded_file_path"),
        Index("ix_uploaded_files_batch_document", "upload_batch_id", "document_key"),
        Index("ix_uploaded_files_sha256", "sha256"),
    )


class ProcessingBatch(Base):
    __tablename__ = "processing_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    upload_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="SET NULL"), nullable=True
    )
    batch_key: Mapped[str] = mapped_column(String(128), nullable=False)
    batch_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="manual_cli")
    input_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    batch_status: Mapped[str] = mapped_column(
        Enum(*BATCH_STATUS, name="batch_status", native_enum=False, length=32),
        nullable=False,
        server_default="queued",
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
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    project: Mapped[Project] = relationship(Project)
    upload_batch: Mapped[Optional[UploadBatch]] = relationship(UploadBatch)

    __table_args__ = (
        UniqueConstraint("project_id", "batch_key", name="uq_batches_project_batch_key"),
        Index("ix_batches_status_started", "batch_status", "started_at"),
        Index("ix_batches_project_status_started", "project_id", "batch_status", "started_at"),
        Index("ix_batches_upload", "upload_batch_id"),
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    upload_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="SET NULL"), nullable=True
    )
    archive_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_records.id", ondelete="SET NULL"), nullable=True
    )
    document_key: Mapped[str] = mapped_column(String(512), nullable=False)
    processing_status: Mapped[str] = mapped_column(
        "status",
        Enum(*PROCESSING_STATUS, name="processing_job_status", native_enum=False, length=32),
        nullable=False,
        server_default="queued",
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    current_stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    started_at: Mapped[Optional[datetime]] = _ts_col_optional()
    finished_at: Mapped[Optional[datetime]] = _ts_col_optional()
    created_at: Mapped[datetime] = _ts_col()
    updated_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("batch_id", "document_key", name="uq_processing_job_document"),
        Index("ix_jobs_batch_status", "batch_id", "status"),
        Index("ix_jobs_archive", "archive_id"),
    )

    @property
    def status(self) -> str:
        return self.processing_status

    @status.setter
    def status(self, value: str) -> None:
        self.processing_status = value


class ProcessingEvent(Base):
    __tablename__ = "processing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=True
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        Index("ix_processing_events_batch_created", "batch_id", "created_at"),
        Index("ix_processing_events_job_created", "job_id", "created_at"),
    )


class ArchiveRecord(Base):
    __tablename__ = "archive_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="RESTRICT"), nullable=False
    )
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey(
            "processing_jobs.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_archive_records_job_id",
        ),
        nullable=True,
    )
    upload_batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="SET NULL"), nullable=True
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
        Enum(*PROCESSING_STATUS, name="archive_processing_status", native_enum=False, length=32),
        nullable=False,
        server_default="queued",
    )
    review_status: Mapped[str] = mapped_column(
        Enum(*REVIEW_STATUS, name="review_status", native_enum=False, length=32),
        nullable=False,
        server_default="pending",
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
    digitized_time: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    archive_no: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    item_no: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    llm_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    rules_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    final_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    # Latest trace cache for existing detail pages; full call history is in llm_traces.
    llm_raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_cleaned_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    llm_parse_strategy: Mapped[Optional[str]] = mapped_column(
        Enum(*LLM_PARSE_STRATEGY, name="llm_parse_strategy", native_enum=False, length=16),
        nullable=True,
    )
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
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
        Index(
            "uq_archive_no_per_project",
            "project_id",
            "archive_no",
            unique=True,
            postgresql_where=text("archive_no IS NOT NULL"),
        ),
    )


class ArchivePage(Base):
    __tablename__ = "archive_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[int] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    image_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    ocr_avg_confidence: Mapped[Optional[float]] = mapped_column(Numeric(8, 4), nullable=True)
    ocr_low_conf_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ocr_variant: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    layout_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JsonDoc, nullable=True)
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("archive_id", "page_no", name="uq_archive_page_no"),
        UniqueConstraint("archive_id", "image_path", name="uq_archive_page_path"),
    )


class LlmTrace(Base):
    __tablename__ = "llm_traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), nullable=True
    )
    job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("processing_jobs.id", ondelete="SET NULL"), nullable=True
    )
    call_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    raw_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cleaned_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parse_strategy: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        Index("ix_llm_traces_archive_created", "archive_id", "created_at"),
        Index("ix_llm_traces_job_created", "job_id", "created_at"),
    )


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
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
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


class ExportFile(Base):
    __tablename__ = "export_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    batch_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="SET NULL"), nullable=True
    )
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    template_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts_col()


class MetadataRevision(Base):
    __tablename__ = "metadata_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[int] = mapped_column(
        ForeignKey("archive_records.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    field_column: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    old_value: Mapped[Optional[Any]] = mapped_column(JsonDoc, nullable=True)
    new_value: Mapped[Optional[Any]] = mapped_column(JsonDoc, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        UniqueConstraint("archive_id", "revision_no", "field_key", name="uq_revision_field"),
        Index("ix_revisions_archive_revision", "archive_id", "revision_no"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("app_users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[Any]] = mapped_column(JsonDoc, nullable=True)
    before_data: Mapped[Optional[Any]] = mapped_column(JsonDoc, nullable=True)
    after_data: Mapped[Optional[Any]] = mapped_column(JsonDoc, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = _ts_col()

    __table_args__ = (
        Index("ix_audit_action_created", "action", "created_at"),
        Index("ix_audit_target", "target_type", "target_id"),
        Index("ix_audit_project_created", "project_id", "created_at"),
    )


__all__ = [
    "Base",
    "JsonDoc",
    "PROJECT_STATUS",
    "UPLOAD_BATCH_STATUS",
    "UPLOADED_FILE_STATUS",
    "BATCH_STATUS",
    "PROCESSING_STATUS",
    "REVIEW_STATUS",
    "CORRECTION_STATUS",
    "LLM_PARSE_STRATEGY",
    "ORGANIZATION_STATUS",
    "APP_USER_STATUS",
    "APP_USER_ROLE",
    "Organization",
    "AppUser",
    "WebSession",
    "Project",
    "UploadBatch",
    "UploadedFile",
    "ProcessingBatch",
    "ProcessingJob",
    "ProcessingEvent",
    "ArchiveRecord",
    "ArchivePage",
    "LlmTrace",
    "SequenceCounter",
    "ExportFile",
    "MetadataRevision",
    "AuditLog",
]

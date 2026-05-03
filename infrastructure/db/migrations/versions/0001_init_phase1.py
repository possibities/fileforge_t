"""init phase 1A schema

Revision ID: 0001_init_phase1
Revises:
Create Date: 2026-05-03

阶段 1A 初始迁移：8 张表 + PG-only partial unique + GIN(final_metadata)。
不实现 downgrade —— 阶段 1A 视作演进起点，回滚请清库重建。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0001_init_phase1"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 models.py 保持一致的枚举取值
PROJECT_STATUS = ("active", "disabled", "archived")
BATCH_STATUS = ("running", "completed", "aborted")
PROCESSING_STATUS = ("pending", "running", "success", "failed", "error")
REVIEW_STATUS = ("not_required", "needs_review", "in_review", "confirmed")
CORRECTION_STATUS = ("none", "corrected")
LLM_PARSE_STRATEGY = ("json", "repaired", "regex", "failed")


def _enum(name: str, values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def _ts(server_default: bool = True, nullable: bool = False) -> sa.Column:
    if server_default:
        return sa.Column(
            "_placeholder",  # 占位，实际 column 名由调用方覆盖
            sa.DateTime(timezone=True),
            nullable=nullable,
            server_default=sa.func.now(),
        )
    return sa.Column("_placeholder", sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    # ── projects ────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_key", sa.String(128), nullable=False),
        sa.Column("project_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "status",
            _enum("project_status", PROJECT_STATUS),
            nullable=False,
            server_default="active",
        ),
        sa.Column("numbering_rule", _jsonb(), nullable=True),
        sa.Column(
            "preserve_existing_numbers_on_rerun",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("organization_id", sa.Integer, nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_key", name="uq_projects_project_key"),
    )
    op.create_index("ix_projects_status", "projects", ["status"])

    # ── processing_batches ──────────────────────────────────────────────────
    op.create_table(
        "processing_batches",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("batch_key", sa.String(128), nullable=False),
        sa.Column("batch_name", sa.String(255), nullable=True),
        sa.Column("input_dir", sa.Text, nullable=True),
        sa.Column("output_dir", sa.Text, nullable=True),
        sa.Column(
            "batch_status",
            _enum("batch_status", BATCH_STATUS),
            nullable=False,
            server_default="running",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_archives", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_pages", sa.Integer, nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fail_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failure_breakdown", _jsonb(), nullable=True),
        sa.Column("summary_schema_version", sa.String(32), nullable=True),
        sa.Column("summary_schema_ref", sa.String(255), nullable=True),
        sa.Column("summary_changelog_ref", sa.String(255), nullable=True),
        sa.Column("organization_id", sa.Integer, nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "batch_key", name="uq_batches_project_batch_key"),
    )
    op.create_index(
        "ix_batches_status_started", "processing_batches", ["batch_status", "started_at"]
    )
    op.create_index(
        "ix_batches_project_status_started",
        "processing_batches",
        ["project_id", "batch_status", "started_at"],
    )

    # ── archive_records ─────────────────────────────────────────────────────
    op.create_table(
        "archive_records",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            sa.Integer,
            sa.ForeignKey("processing_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("archive_key", sa.String(512), nullable=False),
        sa.Column("archive_name", sa.String(512), nullable=False),
        sa.Column("archive_folder_name", sa.String(512), nullable=True),
        sa.Column("source_folder", sa.Text, nullable=True),
        sa.Column("page_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("image_files", _jsonb(), nullable=True),
        sa.Column("image_names", _jsonb(), nullable=True),
        sa.Column("result_filename", sa.String(255), nullable=True),
        sa.Column(
            "processing_status",
            _enum("processing_status", PROCESSING_STATUS),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "review_status",
            _enum("review_status", REVIEW_STATUS),
            nullable=False,
            server_default="not_required",
        ),
        sa.Column(
            "correction_status",
            _enum("correction_status", CORRECTION_STATUS),
            nullable=False,
            server_default="none",
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("traceback_text", sa.Text, nullable=True),
        sa.Column("processed_time", sa.String(64), nullable=True),
        # 冗余查询列
        sa.Column("category_code", sa.String(32), nullable=True),
        sa.Column("archive_year", sa.String(8), nullable=True),
        sa.Column("classification_code", sa.String(16), nullable=True),
        sa.Column("classification_name", sa.String(32), nullable=True),
        sa.Column("retention_period", sa.String(16), nullable=True),
        sa.Column("retention_period_code", sa.String(8), nullable=True),
        sa.Column("responsible_party", sa.String(255), nullable=True),
        sa.Column("document_number", sa.String(128), nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("document_date", sa.String(16), nullable=True),
        sa.Column("security_level", sa.String(16), nullable=True),
        sa.Column("secret_period", sa.String(16), nullable=True),
        sa.Column("openness_status", sa.String(16), nullable=True),
        sa.Column("openness_delay_reason", sa.String(32), nullable=True),
        sa.Column("fonds_unit_name", sa.String(255), nullable=True),
        sa.Column("digitized_time", sa.String(32), nullable=True),
        sa.Column("archive_no", sa.String(64), nullable=True),
        sa.Column("item_no", sa.String(16), nullable=True),
        # JSONB 快照
        sa.Column("llm_metadata", _jsonb(), nullable=True),
        sa.Column("rules_metadata", _jsonb(), nullable=True),
        sa.Column("final_metadata", _jsonb(), nullable=True),
        # LLM 原始响应（数据契约 §4.4 可选列）
        sa.Column("llm_raw_response", sa.Text, nullable=True),
        sa.Column("llm_cleaned_response", sa.Text, nullable=True),
        sa.Column("llm_parse_strategy", _enum("llm_parse_strategy", LLM_PARSE_STRATEGY), nullable=True),
        sa.Column("organization_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "archive_key", name="uq_archive_batch_key"),
    )
    op.create_index("ix_archive_batch_processing", "archive_records", ["batch_id", "processing_status"])
    op.create_index("ix_archive_batch_review", "archive_records", ["batch_id", "review_status"])
    op.create_index("ix_archive_archive_year", "archive_records", ["archive_year"])
    op.create_index("ix_archive_classification_code", "archive_records", ["classification_code"])
    op.create_index("ix_archive_retention_period", "archive_records", ["retention_period"])
    op.create_index("ix_archive_openness_status", "archive_records", ["openness_status"])
    op.create_index("ix_archive_archive_no", "archive_records", ["archive_no"])
    op.create_index("ix_archive_item_no", "archive_records", ["item_no"])

    # PG-only partial unique；其他 dialect 退化到普通 unique 即可
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_no_per_project "
            "ON archive_records (project_id, archive_no) WHERE archive_no IS NOT NULL"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_archive_final_metadata_gin "
            "ON archive_records USING GIN (final_metadata)"
        )
    else:
        op.create_index(
            "uq_archive_no_per_project",
            "archive_records",
            ["project_id", "archive_no"],
            unique=True,
        )

    # ── archive_pages ───────────────────────────────────────────────────────
    op.create_table(
        "archive_pages",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "archive_id",
            sa.Integer,
            sa.ForeignKey("archive_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_no", sa.Integer, nullable=False),
        sa.Column("image_path", sa.Text, nullable=False),
        sa.Column("image_name", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(128), nullable=True),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("ocr_text", sa.Text, nullable=True),
        sa.Column("ocr_avg_confidence", sa.Float, nullable=True),
        sa.Column("ocr_low_conf_count", sa.Integer, nullable=True),
        sa.Column("ocr_variant", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("archive_id", "page_no", name="uq_archive_page_no"),
        sa.UniqueConstraint("archive_id", "image_path", name="uq_archive_page_path"),
    )

    # ── processing_jobs ─────────────────────────────────────────────────────
    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer,
            sa.ForeignKey("processing_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "archive_id",
            sa.Integer,
            sa.ForeignKey("archive_records.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(32), nullable=False, server_default="archive_classify"),
        sa.Column(
            "processing_status",
            _enum("processing_status_job", PROCESSING_STATUS),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_jobs_batch_status", "processing_jobs", ["batch_id", "processing_status"])

    # ── processing_job_attempts ─────────────────────────────────────────────
    op.create_table(
        "processing_job_attempts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.Integer,
            sa.ForeignKey("processing_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column(
            "processing_status",
            _enum("processing_status_attempt", PROCESSING_STATUS),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("traceback_text", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_attempt_no_per_job"),
    )

    # ── sequence_counters ───────────────────────────────────────────────────
    op.create_table(
        "sequence_counters",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("archive_year", sa.String(8), nullable=False),
        sa.Column("classification_code", sa.String(16), nullable=False),
        sa.Column("retention_period_code", sa.String(8), nullable=False),
        sa.Column("current_value", sa.Integer, nullable=False, server_default="0"),
        sa.Column("organization_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "project_id",
            "archive_year",
            "classification_code",
            "retention_period_code",
            name="uq_sequence_counter_scope",
        ),
    )

    # ── export_files ────────────────────────────────────────────────────────
    op.create_table(
        "export_files",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "batch_id",
            sa.Integer,
            sa.ForeignKey("processing_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("export_type", sa.String(32), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("template_name", sa.String(64), nullable=True),
        sa.Column("row_count", sa.Integer, nullable=True),
        sa.Column("file_hash", sa.String(128), nullable=True),
        sa.Column("created_by", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    # 阶段 1A 不实现 downgrade。要回滚请清库重新 upgrade。
    raise NotImplementedError("阶段 1A 不支持 downgrade，请重建数据库")

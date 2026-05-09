"""读侧只读查询函数集合,对外暴露领域 dataclass。

调用方负责 session 生命周期;本模块不做 commit、不打开 engine。
设计参考 docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Iterable, Optional, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from .models import (
    ArchivePage as ArchivePageModel,
    ArchiveRecord,
    AuditLog,
    MetadataRevision,
    ProcessingBatch,
    Project,
)

T = TypeVar("T")


# ── 列表返回信封(spec §3.1) ─────────────────────────────────────────────────
@dataclass(frozen=True)
class ListResult(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    has_next: bool


# ── ArchiveFilter(spec §3.2),12 字段 ────────────────────────────────────────
@dataclass(frozen=True)
class ArchiveFilter:
    archive_year: Optional[int] = None
    classification_code: Optional[Iterable[str]] = None
    retention_period: Optional[Iterable[str]] = None
    openness_status: Optional[str] = None
    processing_status: Optional[Iterable[str]] = None
    review_status: Optional[Iterable[str]] = None
    correction_status: Optional[str] = None
    archive_no: Optional[str] = None
    item_no: Optional[str] = None
    title_like: Optional[str] = None
    responsible_party_like: Optional[str] = None
    error_code: Optional[Iterable[str]] = None


# ── BatchSummary / BatchDetail(spec §3.3 / §3.4) ────────────────────────────
@dataclass(frozen=True)
class BatchSummary:
    id: int
    project_id: int
    batch_key: str
    batch_name: Optional[str]
    input_dir: Optional[str]
    output_dir: Optional[str]
    batch_status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    total_archives: int
    total_pages: int
    success_count: int
    fail_count: int
    summary_schema_version: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BatchDetail:
    id: int
    project_id: int
    batch_key: str
    batch_name: Optional[str]
    input_dir: Optional[str]
    output_dir: Optional[str]
    batch_status: str
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    total_archives: int
    total_pages: int
    success_count: int
    fail_count: int
    summary_schema_version: Optional[str]
    created_at: datetime
    updated_at: datetime
    failure_breakdown: dict[str, int]
    summary_schema_ref: Optional[str]
    summary_changelog_ref: Optional[str]


# ── ArchivePage(spec §3.5) ──────────────────────────────────────────────────
@dataclass(frozen=True)
class ArchivePage:
    id: int
    page_no: int
    image_path: str
    image_name: str
    file_hash: Optional[str]
    file_size: Optional[int]
    ocr_text: Optional[str]
    ocr_avg_confidence: Optional[float]
    ocr_low_conf_count: Optional[int]
    ocr_variant: Optional[str]
    created_at: datetime


# ── ArchiveSummary(spec §3.6),27 字段 ───────────────────────────────────────
@dataclass(frozen=True)
class ArchiveSummary:
    id: int
    project_id: int
    batch_id: int
    archive_key: str
    archive_name: str
    page_count: int
    processing_status: str
    review_status: str
    correction_status: str
    error_code: Optional[str]
    error_message: Optional[str]
    archive_year: Optional[str]
    classification_code: Optional[str]
    classification_name: Optional[str]
    retention_period: Optional[str]
    retention_period_code: Optional[str]
    responsible_party: Optional[str]
    document_number: Optional[str]
    title: Optional[str]
    document_date: Optional[str]
    openness_status: Optional[str]
    archive_no: Optional[str]
    item_no: Optional[str]
    fonds_unit_name: Optional[str]
    processed_time: Optional[str]
    created_at: datetime
    updated_at: datetime


# ── ArchiveDetail(spec §3.7),45 字段 ───────────────────────────────────────
@dataclass(frozen=True)
class ArchiveDetail:
    id: int
    project_id: int
    batch_id: int
    archive_key: str
    archive_name: str
    page_count: int
    processing_status: str
    review_status: str
    correction_status: str
    error_code: Optional[str]
    error_message: Optional[str]
    archive_year: Optional[str]
    classification_code: Optional[str]
    classification_name: Optional[str]
    retention_period: Optional[str]
    retention_period_code: Optional[str]
    responsible_party: Optional[str]
    document_number: Optional[str]
    title: Optional[str]
    document_date: Optional[str]
    openness_status: Optional[str]
    archive_no: Optional[str]
    item_no: Optional[str]
    fonds_unit_name: Optional[str]
    processed_time: Optional[str]
    created_at: datetime
    updated_at: datetime
    archive_folder_name: Optional[str]
    source_folder: Optional[str]
    image_files: Optional[list[str]]
    image_names: Optional[list[str]]
    result_filename: Optional[str]
    traceback_text: Optional[str]
    category_code: Optional[str]
    security_level: Optional[str]
    secret_period: Optional[str]
    openness_delay_reason: Optional[str]
    digitized_time: Optional[str]
    llm_metadata: Optional[dict[str, Any]]
    rules_metadata: Optional[dict[str, Any]]
    final_metadata: Optional[dict[str, Any]]
    llm_raw_response: Optional[str]
    llm_cleaned_response: Optional[str]
    llm_parse_strategy: Optional[str]
    pages: list[ArchivePage]


# ── RevisionRow / AuditLogRow(spec §3.8 / §3.9) ─────────────────────────────
@dataclass(frozen=True)
class RevisionRow:
    id: int
    archive_id: int
    revision_no: int
    field_key: str
    field_column: Optional[str]
    old_value: Any
    new_value: Any
    reason: Optional[str]
    created_by: Optional[int]
    created_at: datetime


@dataclass(frozen=True)
class AuditLogRow:
    id: int
    actor_user_id: Optional[int]
    action: str
    target_type: Optional[str]
    target_id: Optional[int]
    before_data: Any
    after_data: Any
    ip_address: Optional[str]
    user_agent: Optional[str]
    created_at: datetime


# ── 内部常量 ─────────────────────────────────────────────────────────────────
_PAGE_SIZE_MIN = 1
_PAGE_SIZE_MAX = 200
_AUDIT_TARGET_TYPES_ALLOWED: frozenset[str] = frozenset({"archive"})


__all__ = [
    "ListResult",
    "ArchiveFilter",
    "BatchSummary",
    "BatchDetail",
    "ArchivePage",
    "ArchiveSummary",
    "ArchiveDetail",
    "RevisionRow",
    "AuditLogRow",
]

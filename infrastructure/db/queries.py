"""读侧只读查询函数集合,对外暴露领域 dataclass。

调用方负责 session 生命周期;本模块不做 commit、不打开 engine。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Generic, Iterable, Optional, TypeVar

from sqlalchemy import Select, func, or_, select
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
class BatchDetail(BatchSummary):
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


# ── ArchiveDetail(spec §3.7),在 ArchiveSummary 27 字段基础上扩 18 个 ────────
@dataclass(frozen=True)
class ArchiveDetail(ArchiveSummary):
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


# ── 分页 helper ──────────────────────────────────────────────────────────────
def _validate_pagination(page: int, page_size: int) -> None:
    """校验分页参数;不合法时立即抛 ValueError(spec §6)。"""
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    if page_size < _PAGE_SIZE_MIN or page_size > _PAGE_SIZE_MAX:
        raise ValueError(
            f"page_size must be in [{_PAGE_SIZE_MIN}, {_PAGE_SIZE_MAX}], got {page_size}"
        )


def _paginate(stmt: Select, *, page: int, page_size: int) -> Select:
    """把 page/page_size 转 LIMIT/OFFSET 拍到 select 语句上。"""
    offset = (page - 1) * page_size
    return stmt.limit(page_size).offset(offset)


def _build_list_result(
    *,
    items: list[T],
    total: int,
    page: int,
    page_size: int,
) -> "ListResult[T]":
    """统一构造 ListResult,集中算 has_next。

    has_next 语义:仍有下一页(下一页可能为空,与 page > 末页 时一致返回空集 has_next=False)。
    """
    if total <= 0 or page_size <= 0:
        has_next = False
    else:
        last_page = math.ceil(total / page_size)
        has_next = page < last_page
    return ListResult(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=has_next,
    )


# ── Query 函数 ───────────────────────────────────────────────────────────────
def _batch_to_summary(batch: ProcessingBatch) -> BatchSummary:
    return BatchSummary(
        id=batch.id,
        project_id=batch.project_id,
        batch_key=batch.batch_key,
        batch_name=batch.batch_name,
        input_dir=batch.input_dir,
        output_dir=batch.output_dir,
        batch_status=batch.batch_status,
        started_at=batch.started_at,
        finished_at=batch.finished_at,
        total_archives=batch.total_archives,
        total_pages=batch.total_pages,
        success_count=batch.success_count,
        fail_count=batch.fail_count,
        summary_schema_version=batch.summary_schema_version,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


def list_batches(
    session: Session,
    *,
    project_key: str,
    status_filter: Optional[Iterable[str]] = None,
    organization_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[BatchSummary]":
    """按 project_key 过滤批次,默认按 started_at DESC NULLS LAST 排序。"""
    _validate_pagination(page, page_size)

    base = (
        select(ProcessingBatch)
        .join(Project, ProcessingBatch.project_id == Project.id)
        .where(Project.project_key == project_key)
    )
    statuses = list(status_filter) if status_filter else []
    if statuses:
        base = base.where(ProcessingBatch.batch_status.in_(statuses))
    if organization_id is not None:
        base = base.where(Project.organization_id == organization_id).where(
            or_(
                ProcessingBatch.organization_id == organization_id,
                ProcessingBatch.organization_id.is_(None),
            )
        )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                ProcessingBatch.started_at.desc().nullslast(),
                ProcessingBatch.id.desc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_batch_to_summary(b) for b in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def get_batch_detail(
    session: Session,
    *,
    batch_id: int,
) -> Optional[BatchDetail]:
    """返回批次详情 + failure_breakdown + schema 三件套。找不到返回 None。"""
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return None
    summary = _batch_to_summary(batch)
    return BatchDetail(
        **{f.name: getattr(summary, f.name) for f in fields(BatchSummary)},
        failure_breakdown=dict(batch.failure_breakdown or {}),
        summary_schema_ref=batch.summary_schema_ref,
        summary_changelog_ref=batch.summary_changelog_ref,
    )


def _archive_to_summary(ar: ArchiveRecord) -> ArchiveSummary:
    return ArchiveSummary(
        id=ar.id,
        project_id=ar.project_id,
        batch_id=ar.batch_id,
        archive_key=ar.archive_key,
        archive_name=ar.archive_name,
        page_count=ar.page_count,
        processing_status=ar.processing_status,
        review_status=ar.review_status,
        correction_status=ar.correction_status,
        error_code=ar.error_code,
        error_message=ar.error_message,
        archive_year=ar.archive_year,
        classification_code=ar.classification_code,
        classification_name=ar.classification_name,
        retention_period=ar.retention_period,
        retention_period_code=ar.retention_period_code,
        responsible_party=ar.responsible_party,
        document_number=ar.document_number,
        title=ar.title,
        document_date=ar.document_date,
        openness_status=ar.openness_status,
        archive_no=ar.archive_no,
        item_no=ar.item_no,
        fonds_unit_name=ar.fonds_unit_name,
        processed_time=ar.processed_time,
        created_at=ar.created_at,
        updated_at=ar.updated_at,
    )


def _apply_archive_filter(stmt: Select, f: ArchiveFilter) -> Select:
    """把 ArchiveFilter 12 字段映射到 SQL where 子句。

    约定(spec §3.2):
      - None 值不附加条件
      - Iterable 字段空集等价于 None
      - *_like 字段空字符串等价于 None
      - archive_year 是 int 输入,DB 存 String → 转 str(value)
    """
    if f.archive_year is not None:
        stmt = stmt.where(ArchiveRecord.archive_year == str(f.archive_year))

    classification_codes = list(f.classification_code) if f.classification_code else []
    if classification_codes:
        stmt = stmt.where(ArchiveRecord.classification_code.in_(classification_codes))

    retention_periods = list(f.retention_period) if f.retention_period else []
    if retention_periods:
        stmt = stmt.where(ArchiveRecord.retention_period.in_(retention_periods))

    if f.openness_status:
        stmt = stmt.where(ArchiveRecord.openness_status == f.openness_status)

    processing_statuses = list(f.processing_status) if f.processing_status else []
    if processing_statuses:
        stmt = stmt.where(ArchiveRecord.processing_status.in_(processing_statuses))

    review_statuses = list(f.review_status) if f.review_status else []
    if review_statuses:
        stmt = stmt.where(ArchiveRecord.review_status.in_(review_statuses))

    if f.correction_status:
        stmt = stmt.where(ArchiveRecord.correction_status == f.correction_status)

    if f.archive_no:
        stmt = stmt.where(ArchiveRecord.archive_no == f.archive_no)

    if f.item_no:
        stmt = stmt.where(ArchiveRecord.item_no == f.item_no)

    if f.title_like:
        stmt = stmt.where(ArchiveRecord.title.ilike(f"%{f.title_like}%"))

    if f.responsible_party_like:
        stmt = stmt.where(
            ArchiveRecord.responsible_party.ilike(f"%{f.responsible_party_like}%")
        )

    error_codes = list(f.error_code) if f.error_code else []
    if error_codes:
        stmt = stmt.where(ArchiveRecord.error_code.in_(error_codes))

    return stmt


def list_archives(
    session: Session,
    *,
    batch_id: int,
    filter: Optional[ArchiveFilter] = None,
    organization_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[ArchiveSummary]":
    """按 batch_id 列出档案,支持 12 字段过滤,默认按 archive_no/item_no ASC NULLS LAST 排序。"""
    _validate_pagination(page, page_size)

    base = select(ArchiveRecord).where(ArchiveRecord.batch_id == batch_id)
    if organization_id is not None:
        base = (
            base.join(ProcessingBatch, ArchiveRecord.batch_id == ProcessingBatch.id)
            .join(Project, ProcessingBatch.project_id == Project.id)
            .where(Project.organization_id == organization_id)
            .where(
                or_(
                    ProcessingBatch.organization_id == organization_id,
                    ProcessingBatch.organization_id.is_(None),
                )
            )
            .where(
                or_(
                    ArchiveRecord.organization_id == organization_id,
                    ArchiveRecord.organization_id.is_(None),
                )
            )
        )
    if filter is not None:
        base = _apply_archive_filter(base, filter)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                ArchiveRecord.archive_no.asc().nullslast(),
                ArchiveRecord.item_no.asc().nullslast(),
                ArchiveRecord.id.asc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_archive_to_summary(ar) for ar in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def _page_to_dataclass(p: ArchivePageModel) -> ArchivePage:
    return ArchivePage(
        id=p.id,
        page_no=p.page_no,
        image_path=p.image_path,
        image_name=p.image_name,
        file_hash=p.file_hash,
        file_size=p.file_size,
        ocr_text=p.ocr_text,
        ocr_avg_confidence=p.ocr_avg_confidence,
        ocr_low_conf_count=p.ocr_low_conf_count,
        ocr_variant=p.ocr_variant,
        created_at=p.created_at,
    )


def _archive_to_detail(ar: ArchiveRecord, pages: list[ArchivePage]) -> ArchiveDetail:
    summary = _archive_to_summary(ar)
    return ArchiveDetail(
        **{f.name: getattr(summary, f.name) for f in fields(ArchiveSummary)},
        archive_folder_name=ar.archive_folder_name,
        source_folder=ar.source_folder,
        image_files=list(ar.image_files) if ar.image_files else None,
        image_names=list(ar.image_names) if ar.image_names else None,
        result_filename=ar.result_filename,
        traceback_text=ar.traceback_text,
        category_code=ar.category_code,
        security_level=ar.security_level,
        secret_period=ar.secret_period,
        openness_delay_reason=ar.openness_delay_reason,
        digitized_time=ar.digitized_time,
        llm_metadata=dict(ar.llm_metadata) if ar.llm_metadata else None,
        rules_metadata=dict(ar.rules_metadata) if ar.rules_metadata else None,
        final_metadata=dict(ar.final_metadata) if ar.final_metadata else None,
        llm_raw_response=ar.llm_raw_response,
        llm_cleaned_response=ar.llm_cleaned_response,
        llm_parse_strategy=ar.llm_parse_strategy,
        pages=pages,
    )


def get_archive_detail(
    session: Session,
    *,
    archive_id: int,
) -> Optional[ArchiveDetail]:
    """返回档案详情 + 全页面列表。找不到返回 None。"""
    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return None

    page_rows = session.scalars(
        select(ArchivePageModel)
        .where(ArchivePageModel.archive_id == archive_id)
        .order_by(ArchivePageModel.page_no.asc())
    ).all()
    pages = [_page_to_dataclass(p) for p in page_rows]

    return _archive_to_detail(archive, pages)


def _revision_to_row(rev: MetadataRevision) -> RevisionRow:
    return RevisionRow(
        id=rev.id,
        archive_id=rev.archive_id,
        revision_no=rev.revision_no,
        field_key=rev.field_key,
        field_column=rev.field_column,
        old_value=rev.old_value,
        new_value=rev.new_value,
        reason=rev.reason,
        created_by=rev.created_by,
        created_at=rev.created_at,
    )


def list_revisions(
    session: Session,
    *,
    archive_id: int,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[RevisionRow]":
    """按 archive_id 列出修正记录,默认 revision_no DESC, id DESC。"""
    _validate_pagination(page, page_size)

    base = select(MetadataRevision).where(MetadataRevision.archive_id == archive_id)

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(
                MetadataRevision.revision_no.desc(),
                MetadataRevision.id.desc(),
            ),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_revision_to_row(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def _audit_to_row(log: AuditLog) -> AuditLogRow:
    return AuditLogRow(
        id=log.id,
        actor_user_id=log.actor_user_id,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        before_data=log.before_data,
        after_data=log.after_data,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        created_at=log.created_at,
    )


def list_audit_logs(
    session: Session,
    *,
    target_type: str,
    target_id: int,
    page: int = 1,
    page_size: int = 50,
) -> "ListResult[AuditLogRow]":
    """按 (target_type, target_id) 列出审计记录,默认 created_at DESC, id DESC。

    一期白名单 target_type ∈ {"archive"};未知值快速失败,避免 audit 漏检(spec §6/§12.4)。
    """
    if target_type not in _AUDIT_TARGET_TYPES_ALLOWED:
        raise ValueError(
            f"unknown target_type={target_type!r}; "
            f"allowed: {sorted(_AUDIT_TARGET_TYPES_ALLOWED)}"
        )
    _validate_pagination(page, page_size)

    base = select(AuditLog).where(
        AuditLog.target_type == target_type,
        AuditLog.target_id == target_id,
    )

    total = session.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = session.scalars(
        _paginate(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()),
            page=page,
            page_size=page_size,
        )
    ).all()

    return _build_list_result(
        items=[_audit_to_row(r) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


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
    "list_batches",
    "get_batch_detail",
    "list_archives",
    "get_archive_detail",
    "list_revisions",
    "list_audit_logs",
]

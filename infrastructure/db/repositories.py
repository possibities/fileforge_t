"""窄查询函数集合，封装 ORM 细节，对外只暴露领域语义。

调用方负责传入 session、控制事务边界（commit/rollback）；本模块不做 commit。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.sequence_generator import SequenceGenerator

from .models import (
    ArchivePage,
    ArchiveRecord,
    AuditLog,
    ExportFile,
    MetadataRevision,
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
    input_dir: Optional[str] = None,
) -> None:
    """Upsert ArchivePage 行。

    image_path 列存归一化后的相对 input_dir 的 POSIX 路径(数据契约 §4.5),
    避免绝对路径在跨机器重跑或目录搬迁时破坏 (archive_id, image_path) 唯一约束。
    file_hash / file_size 仍按原始绝对路径读盘,与归一化解耦。

    input_dir 缺省或路径不在其下时,退化为"原始路径转 POSIX 风格"。退化分支
    会丢失幂等保护,因此在外层(BatchRecorder)调用时应始终传入 input_dir。
    """
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
        stored_path = _to_relative_posix(image_path, input_dir)

        if stored_path in existing:
            page = existing[stored_path]
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
                    image_path=stored_path,
                    image_name=image_name,
                    file_hash=file_hash,
                    file_size=file_size,
                )
            )


def _to_relative_posix(image_path: str, input_dir: Optional[str]) -> str:
    """归一化 image_path 为相对 input_dir 的 POSIX 风格路径。

    退化策略:
      - input_dir 为 None/空 → 仅把反斜杠换为正斜杠。
      - image_path 不在 input_dir 下(relative_to 抛 ValueError) → 同上,且写日志。

    退化分支会丢失"跨机器搬目录仍幂等"的保证,但保证不会因归一化失败而崩溃管线。
    """
    if not input_dir:
        return image_path.replace("\\", "/")
    try:
        rel = Path(image_path).resolve().relative_to(Path(input_dir).resolve())
        return rel.as_posix()
    except ValueError:
        logger.warning(
            "[DB] image_path 不在 input_dir 下,退化为原始路径(POSIX): input_dir=%s path=%s",
            input_dir,
            image_path,
        )
        return image_path.replace("\\", "/")


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
    "FieldRevision",
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
    "next_revision_no",
    "record_revisions",
    "record_audit_log",
    "apply_force_rerun_rules",
    "EDITABLE_FIELDS",
    "RETENTION_PERIOD_CHOICES",
    "ManualCorrectionInput",
    "apply_manual_correction",
]


# ── 修正记录与审计日志(数据契约 §4.7) ───────────────────────────────────────
@dataclass
class FieldRevision:
    """单字段差异记录,作为 record_revisions 的输入单元。

    field_key 为中文 metadata key(如 "题名"),field_column 是英文冗余列名(可空)。
    old_value/new_value 落 JSONB,允许 None。
    """

    field_key: str
    field_column: Optional[str] = None
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None


def next_revision_no(session: Session, *, archive_id: int) -> int:
    """档案内 revision_no 单调递增分配。

    并发场景下应在调用方持有 archive_records 行锁;阶段 1B 的修正写入由
    apply_force_rerun_rules / 后续人工修正 API 在事务内完成。
    """
    current = session.scalar(
        select(func.max(MetadataRevision.revision_no)).where(
            MetadataRevision.archive_id == archive_id
        )
    )
    return int(current or 0) + 1


def record_revisions(
    session: Session,
    *,
    archive_id: int,
    revisions: Iterable[FieldRevision],
    actor_user_id: Optional[int] = None,
    reason: Optional[str] = None,
    revision_no: Optional[int] = None,
) -> int:
    """把一组字段 diff 写成同一 revision_no 的多行;返回该 revision_no。

    若 revisions 为空,直接返回 0 且不分配 revision_no(无副作用)。
    """
    rev_list = [r for r in revisions if r is not None]
    if not rev_list:
        return 0

    if revision_no is None:
        revision_no = next_revision_no(session, archive_id=archive_id)

    for rev in rev_list:
        session.add(
            MetadataRevision(
                archive_id=archive_id,
                revision_no=revision_no,
                field_key=rev.field_key,
                field_column=rev.field_column,
                old_value=rev.old_value,
                new_value=rev.new_value,
                reason=reason,
                created_by=actor_user_id,
            )
        )
    session.flush()
    return revision_no


def record_audit_log(
    session: Session,
    *,
    actor_user_id: Optional[int] = None,
    action: str,
    target_type: Optional[str] = None,
    target_id: Optional[int] = None,
    before_data: Optional[Any] = None,
    after_data: Optional[Any] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        before_data=before_data,
        after_data=after_data,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    session.add(log)
    session.flush()
    return log


def _diff_metadata_to_revisions(
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[FieldRevision]:
    """对比新旧 metadata,返回需要写入 metadata_revisions 的字段差异。

    采用并集 key 比较,任何值差异都生成一条;新增字段 old=None,删除字段 new=None。
    """
    out: list[FieldRevision] = []
    keys = set(old.keys()) | set(new.keys())
    for key in sorted(keys):
        old_v = old.get(key)
        new_v = new.get(key)
        if old_v == new_v:
            continue
        out.append(
            FieldRevision(
                field_key=key,
                field_column=_REDUNDANT_COLUMN_MAP.get(key),
                old_value=old_v,
                new_value=new_v,
            )
        )
    return out


def apply_force_rerun_rules(
    session: Session,
    *,
    archive: ArchiveRecord,
    new_metadata: dict[str, Any],
    actor_user_id: Optional[int] = None,
    reason: str = "rules_rerun_force",
) -> int:
    """显式 --force-rerun-rules:覆盖已 corrected 档案的 final_metadata。

    自动生成:
      - 一组字段级 metadata_revisions(共享同一 revision_no,reason=rules_rerun_force)
      - 一条 audit_logs(action=force_rerun_rules)

    返回写入的 revision_no(无差异时返回 0,且不写 audit)。
    """
    old_final = dict(archive.final_metadata or {})
    diffs = _diff_metadata_to_revisions(old_final, new_metadata)
    if not diffs:
        return 0

    rev_no = record_revisions(
        session,
        archive_id=archive.id,
        revisions=diffs,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    apply_classification_result(
        session,
        archive=archive,
        final_metadata=new_metadata,
        rules_metadata=new_metadata,
        force_rerun_rules=True,
    )
    record_audit_log(
        session,
        actor_user_id=actor_user_id,
        action="force_rerun_rules",
        target_type="archive",
        target_id=archive.id,
        before_data=old_final,
        after_data=new_metadata,
    )
    return rev_no


# ── Web 后台人工修正(数据契约 §4.7 + §9.4) ──────────────────────────────────
EDITABLE_FIELDS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")
RETENTION_PERIOD_CHOICES: tuple[str, ...] = ("永久", "30年", "10年")


@dataclass
class ManualCorrectionInput:
    """人工修正提交的 4 个字段新值。

    所有字段都应在 Web/CLI 入口处完成 strip / 长度 / enum 校验后再传入;
    apply_manual_correction 不做二次校验。
    """

    title: str
    responsible_party: str
    classification_code: str
    retention_period: str


def apply_manual_correction(
    session: Session,
    *,
    archive: ArchiveRecord,
    new_values: ManualCorrectionInput,
    actor_user_id: int,
    reason: Optional[str] = None,
) -> int:
    """对档案做人工修正:diff → revisions → 同步冗余列 + retention_period_code
    → 置 correction_status='corrected' → audit。函数自身不 commit。

    返回写入的 revision_no;无差异返回 0(无 audit、无字段更新)。
    """
    old_final = dict(archive.final_metadata or {})
    overlay = {
        "题名": new_values.title,
        "责任者": new_values.responsible_party,
        "实体分类号": new_values.classification_code,
        "保管期限": new_values.retention_period,
    }
    new_final = {**old_final, **overlay}

    diffs = _diff_metadata_to_revisions(old_final, new_final)
    if not diffs:
        return 0

    stored_reason = reason if reason else "manual_correction"
    rev_no = record_revisions(
        session,
        archive_id=archive.id,
        revisions=diffs,
        actor_user_id=actor_user_id,
        reason=stored_reason,
    )

    archive.final_metadata = new_final
    for key, column in _REDUNDANT_COLUMN_MAP.items():
        if key in overlay:
            value = overlay[key]
            setattr(archive, column, str(value) if value is not None else None)
    archive.retention_period_code = _resolve_retention_code(
        new_final.get("归档年度"),
        new_final.get("保管期限"),
    )
    archive.correction_status = "corrected"

    record_audit_log(
        session,
        actor_user_id=actor_user_id,
        action="manual_correction",
        target_type="archive",
        target_id=archive.id,
        before_data=old_final,
        after_data=new_final,
    )
    return rev_no

"""BatchProcessor 旁路写库 hook。

设计原则：
  - 所有公开方法 try/except 包裹，DB 写失败只 log + 自增 db_error_count，
    不能让批次崩溃或丢 JSON/CSV 输出（数据契约 §6.3）。
  - 单档案一个事务（archive + pages + job + attempt + final_metadata + 状态）。
  - allocator 走自己的短事务，与档案大事务解耦。
  - 件号策略：skip-success 默认 + 尾部新发号；force-renumber/rerun-all 留接口暂未实现。
"""

from __future__ import annotations

import logging
import traceback as tb
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import repositories
from .allocator import DatabaseAllocator, InMemoryAllocator, SequenceAllocator
from .engine import check_connectivity
from .models import ArchiveRecord, ProcessingJob

logger = logging.getLogger(__name__)


VALID_RERUN_POLICIES = {"skip-success", "rerun-failed-only", "rerun-all"}


@dataclass
class _ArchiveContext:
    archive_id: int
    job_id: int
    archive_key: str


@dataclass
class _RecorderState:
    project_id: int
    batch_id: int
    db_error_count: int = 0
    skipped_archive_keys: List[str] = field(default_factory=list)


class BatchRecorder:
    """旁路写库主入口。"""

    def __init__(
        self,
        *,
        engine: Engine,
        session_factory: sessionmaker[Session],
        project_key: str,
        project_name: Optional[str],
        batch_key: str,
        rerun_policy: str = "skip-success",
        summary_schema_version: Optional[str] = None,
        summary_schema_ref: Optional[str] = None,
        summary_changelog_ref: Optional[str] = None,
        input_dir: Optional[str] = None,
        output_dir: Optional[str] = None,
    ) -> None:
        if rerun_policy not in VALID_RERUN_POLICIES:
            raise ValueError(
                f"未知 rerun_policy={rerun_policy!r}，允许值: {sorted(VALID_RERUN_POLICIES)}"
            )
        self._engine = engine
        self._session_factory = session_factory
        self._project_key = project_key
        self._project_name = project_name
        self._batch_key = batch_key
        self._rerun_policy = rerun_policy
        self._summary_schema_version = summary_schema_version
        self._summary_schema_ref = summary_schema_ref
        self._summary_changelog_ref = summary_changelog_ref
        self._input_dir = input_dir
        self._output_dir = output_dir

        check_connectivity(engine)
        self._state = self._bootstrap()
        self._allocator: SequenceAllocator = self._build_allocator()

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def allocator(self) -> SequenceAllocator:
        return self._allocator

    @property
    def db_error_count(self) -> int:
        return self._state.db_error_count

    @property
    def project_id(self) -> int:
        return self._state.project_id

    @property
    def batch_id(self) -> int:
        return self._state.batch_id

    def on_batch_start(
        self,
        *,
        total_archives: int,
        total_pages: int,
    ) -> None:
        with self._safe_session("on_batch_start") as session:
            repositories.update_batch_progress(
                session,
                batch_id=self._state.batch_id,
                total_archives=total_archives,
                total_pages=total_pages,
                started_at=datetime.now(timezone.utc),
            )

    def should_skip(self, archive_key: str) -> bool:
        if self._rerun_policy != "skip-success":
            return False
        try:
            with self._session_factory() as session:
                hit = repositories.find_existing_success(
                    session, batch_id=self._state.batch_id, archive_key=archive_key
                )
            if hit is not None:
                self._state.skipped_archive_keys.append(archive_key)
                logger.info(
                    "[DB] skip-success: archive_key=%s 上次成功，跳过本次处理", archive_key
                )
                return True
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] should_skip 失败 archive_key=%s: %s", archive_key, exc)
        return False

    def load_previous_success(self, archive_key: str) -> Optional[Dict[str, Any]]:
        try:
            with self._session_factory() as session:
                archive = repositories.find_existing_success(
                    session, batch_id=self._state.batch_id, archive_key=archive_key
                )
                if archive is None:
                    return None
                return {
                    "archive_name": archive.archive_name,
                    "source_folder": archive.source_folder,
                    "page_count": archive.page_count,
                    "image_files": list(archive.image_files or []),
                    "image_names": list(archive.image_names or []),
                    "processed_time": archive.processed_time,
                    "metadata": dict(archive.final_metadata or {}),
                    "status": "success",
                    "error_code": None,
                    "error_message": None,
                    "error": None,
                }
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] load_previous_success 失败: %s", exc)
            return None

    def on_archive_start(
        self,
        *,
        archive_name: str,
        archive_key: str,
        source_folder: Optional[str],
        image_paths: List[str],
        processed_time: Optional[str],
    ) -> Optional[_ArchiveContext]:
        try:
            with self._session_factory() as session:
                archive = repositories.upsert_archive(
                    session,
                    project_id=self._state.project_id,
                    batch_id=self._state.batch_id,
                    archive_key=archive_key,
                    archive_name=archive_name,
                    source_folder=source_folder,
                    page_count=len(image_paths),
                    image_files=list(image_paths),
                    image_names=[image_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for image_path in image_paths],
                    processed_time=processed_time,
                )
                repositories.upsert_pages(
                    session, archive_id=archive.id, image_paths=image_paths
                )
                job = repositories.record_job_start(
                    session, batch_id=self._state.batch_id, archive_id=archive.id
                )
                session.commit()
                return _ArchiveContext(
                    archive_id=archive.id,
                    job_id=job.id,
                    archive_key=archive_key,
                )
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] on_archive_start 失败 %s: %s", archive_key, exc)
            return None

    def on_archive_complete(
        self,
        ctx: Optional[_ArchiveContext],
        *,
        status: str,
        metadata: Optional[Dict[str, Any]],
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        traceback_text: Optional[str] = None,
        result_filename: Optional[str] = None,
        llm_trace: Optional[Any] = None,
    ) -> None:
        if ctx is None:
            return
        try:
            with self._session_factory() as session:
                archive = session.get(ArchiveRecord, ctx.archive_id)
                if archive is None:
                    raise RuntimeError(f"archive id={ctx.archive_id} 不存在")
                review = self._derive_review_status(metadata) if status == "success" else None
                if status == "success" and metadata is not None:
                    repositories.apply_classification_result(
                        session,
                        archive=archive,
                        final_metadata=metadata,
                        rules_metadata=metadata,
                    )
                if llm_trace is not None:
                    archive.llm_raw_response = getattr(llm_trace, "raw_response", None)
                    archive.llm_cleaned_response = getattr(llm_trace, "cleaned_response", None)
                    strategy = getattr(llm_trace, "parse_strategy", None)
                    if strategy:
                        archive.llm_parse_strategy = strategy
                repositories.mark_archive_status(
                    session,
                    archive=archive,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    traceback_text=traceback_text,
                    review_status=review,
                    result_filename=result_filename,
                )
                job = session.get(ProcessingJob, ctx.job_id)
                if job is not None:
                    repositories.record_job_attempt(
                        session,
                        job=job,
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                        traceback_text=traceback_text,
                    )
                session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] on_archive_complete 失败 %s: %s", ctx.archive_key, exc)

    def update_result_filename(self, ctx: Optional[_ArchiveContext], result_filename: str) -> None:
        if ctx is None:
            return
        try:
            with self._session_factory() as session:
                archive = session.get(ArchiveRecord, ctx.archive_id)
                if archive is None:
                    return
                archive.result_filename = result_filename
                session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] update_result_filename 失败: %s", exc)

    def record_export(
        self,
        *,
        export_type: str,
        file_path: str,
        template_name: Optional[str] = None,
        row_count: Optional[int] = None,
    ) -> None:
        try:
            with self._session_factory() as session:
                repositories.record_export_file(
                    session,
                    batch_id=self._state.batch_id,
                    export_type=export_type,
                    file_path=file_path,
                    template_name=template_name,
                    row_count=row_count,
                )
                session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] record_export 失败 type=%s: %s", export_type, exc)

    def on_batch_finish(
        self,
        *,
        success_count: int,
        fail_count: int,
        failure_breakdown: Dict[str, int],
        batch_status: str = "completed",
    ) -> None:
        try:
            with self._session_factory() as session:
                repositories.finalize_batch(
                    session,
                    batch_id=self._state.batch_id,
                    success_count=success_count,
                    fail_count=fail_count,
                    failure_breakdown=failure_breakdown,
                    batch_status=batch_status,
                )
                session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] on_batch_finish 失败: %s", exc)

        if self._state.db_error_count:
            logger.warning(
                "[DB] 本次批次有 %s 条数据库写入失败，JSON/CSV 仍已正常交付",
                self._state.db_error_count,
            )
        if self._state.skipped_archive_keys:
            logger.info(
                "[DB] skip-success 命中 %s 条档案：%s",
                len(self._state.skipped_archive_keys),
                self._state.skipped_archive_keys,
            )

    # ── 内部 ─────────────────────────────────────────────────────────────────
    def _bootstrap(self) -> _RecorderState:
        with self._session_factory() as session:
            try:
                project = repositories.get_or_create_project(
                    session,
                    project_key=self._project_key,
                    project_name=self._project_name,
                )
                batch = repositories.get_or_create_batch(
                    session,
                    project_id=project.id,
                    batch_key=self._batch_key,
                    input_dir=self._input_dir,
                    output_dir=self._output_dir,
                    summary_schema_version=self._summary_schema_version,
                    summary_schema_ref=self._summary_schema_ref,
                    summary_changelog_ref=self._summary_changelog_ref,
                )
                project_id = project.id
                batch_id = batch.id
                session.commit()
            except Exception:
                session.rollback()
                raise
        return _RecorderState(project_id=project_id, batch_id=batch_id)

    def _build_allocator(self) -> SequenceAllocator:
        try:
            return DatabaseAllocator(
                session_factory=self._session_factory,
                project_id=self._state.project_id,
            )
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] DatabaseAllocator 初始化失败，回退内存版: %s", exc)
            return InMemoryAllocator()

    @contextmanager
    def _safe_session(self, where: str):
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            session.rollback()
            logger.exception("[DB] %s 失败: %s\n%s", where, exc, tb.format_exc())
        finally:
            session.close()

    @staticmethod
    def _derive_review_status(metadata: Optional[Dict[str, Any]]) -> str:
        if not metadata:
            return "not_required"
        notes = str(metadata.get("备注") or "")
        if "【待核查】" in notes:
            return "needs_review"
        return "not_required"


__all__ = ["BatchRecorder", "VALID_RERUN_POLICIES"]

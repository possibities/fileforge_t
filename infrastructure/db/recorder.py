"""BatchProcessor 旁路写库 hook。

设计原则：
  - 所有公开方法 try/except 包裹，DB 写失败只 log + 自增 db_error_count，
    不能让批次崩溃或丢 JSON/CSV 输出（数据契约 §6.3）。
  - 单档案一个事务（archive + pages + job + attempt + final_metadata + 状态）。
  - allocator 走自己的短事务，与档案大事务解耦。
  - rerun_policy 三档(VALID_RERUN_POLICIES)：
      · skip-success(默认) / rerun-failed-only —— 复用上次已成功档案的结果，
        只(重)处理失败与新增档案；件号在既有计数器尾部续号。
      · rerun-all —— 全部重新处理(不复用)，件号同样尾部续号(不重排，
        即不做 force-renumber)。
"""

from __future__ import annotations

import logging
import traceback as tb
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import Engine, select
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
        upload_batch_id: Optional[int] = None,
        trigger_type: str = "manual_cli",
        batch_name: Optional[str] = None,
        organization_id: Optional[int] = None,
        created_by: Optional[int] = None,
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
        self._upload_batch_id = upload_batch_id
        self._trigger_type = trigger_type
        self._batch_name = batch_name
        self._organization_id = organization_id
        self._created_by = created_by

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
        # rerun-all：从不跳过，全部档案重新处理。
        # skip-success / rerun-failed-only：复用上次已成功档案的结果，不再重复处理；
        #   上次失败的与新增的档案仍会(重新)处理。
        #   注意：当前架构下"跳过"等价于"复用既有成功结果"(见 BatchProcessor:
        #   should_skip 命中后只有能 load_previous_success 才会真正跳过)，因此
        #   skip-success 与 rerun-failed-only 的跳过判定一致——前者强调"保留成功"，
        #   后者强调"只重跑非成功"，二者描述同一操作。
        if self._rerun_policy == "rerun-all":
            return False
        try:
            with self._session_factory() as session:
                hit = repositories.find_existing_success(
                    session, batch_id=self._state.batch_id, archive_key=archive_key
                )
            if hit is not None:
                self._state.skipped_archive_keys.append(archive_key)
                logger.info(
                    "[DB] %s: archive_key=%s 上次成功，跳过本次处理",
                    self._rerun_policy,
                    archive_key,
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
                job = repositories.record_job_start(
                    session,
                    batch_id=self._state.batch_id,
                    project_id=self._state.project_id,
                    upload_batch_id=self._upload_batch_id,
                    document_key=archive_key,
                    page_count=len(image_paths),
                )
                archive = repositories.upsert_archive(
                    session,
                    project_id=self._state.project_id,
                    batch_id=self._state.batch_id,
                    upload_batch_id=self._upload_batch_id,
                    job_id=job.id,
                    organization_id=self._organization_id,
                    archive_key=archive_key,
                    archive_name=archive_name,
                    source_folder=source_folder,
                    page_count=len(image_paths),
                    image_files=list(image_paths),
                    image_names=[image_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for image_path in image_paths],
                    processed_time=processed_time,
                )
                repositories.upsert_pages(
                    session,
                    archive_id=archive.id,
                    image_paths=image_paths,
                    input_dir=self._input_dir,
                    upload_batch_id=self._upload_batch_id,
                )
                job.archive_id = archive.id
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
        rewrite_trace: Optional[Any] = None,
    ) -> None:
        if ctx is None:
            return
        try:
            with self._session_factory() as session:
                archive = session.get(ArchiveRecord, ctx.archive_id)
                if archive is None:
                    raise RuntimeError(f"archive id={ctx.archive_id} 不存在")
                review = (
                    self._derive_review_status(metadata)
                    if status == "success"
                    else "not_required"
                )
                if status == "success" and metadata is not None:
                    # llm_metadata = 规则引擎修正前的 LLM 原始结构化输出,
                    # 由 ExtractionTrace.parsed_metadata 透传而来(trace 可能为 None)。
                    llm_metadata = getattr(llm_trace, "parsed_metadata", None)
                    repositories.apply_classification_result(
                        session,
                        archive=archive,
                        final_metadata=metadata,
                        rules_metadata=metadata,
                        llm_metadata=llm_metadata,
                    )
                if llm_trace is not None:
                    repositories.record_llm_trace(
                        session,
                        archive=archive,
                        job_id=ctx.job_id,
                        call_type="metadata_extract",
                        trace=llm_trace,
                    )
                if rewrite_trace is not None:
                    # 二次简报重写：独立 LlmTrace 行；不覆盖 archive 主抽取的缓存 llm_* 列 [R2]
                    repositories.record_llm_trace(
                        session,
                        archive=archive,
                        job_id=ctx.job_id,
                        call_type="briefing_rewrite",
                        trace=rewrite_trace,
                        update_cached_columns=False,
                    )
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
                    repositories.mark_job_complete(
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

    def on_archive_stage(
        self,
        ctx: Optional[_ArchiveContext],
        *,
        stage: str,
        status: str,
        progress: int,
        message: Optional[str] = None,
    ) -> None:
        if ctx is None:
            return
        try:
            with self._session_factory() as session:
                job = session.get(ProcessingJob, ctx.job_id)
                if job is None:
                    return
                repositories.update_job_progress(
                    session,
                    job=job,
                    status=status,
                    stage=stage,
                    progress=progress,
                    message=message,
                )
                session.commit()
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception("[DB] on_archive_stage 失败 %s: %s", ctx.archive_key, exc)

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

    def force_rerun_rules_for_archive(
        self,
        *,
        archive_key: str,
        new_metadata: Dict[str, Any],
        actor_user_id: Optional[int] = None,
        reason: str = "rules_rerun_force",
    ) -> Optional[int]:
        """对单个档案显式触发规则重跑覆盖。

        典型使用场景:CLI 工具或人工修正 API 在用户确认后调用。
        - 找到该 batch 下的 archive_records 行
        - 在事务内 diff 旧 final_metadata 与 new_metadata,生成 revisions+audit
        - 用 new_metadata 覆盖 final_metadata 与冗余列(force_rerun_rules=True)

        返回写入的 revision_no;无差异时返回 0;DB 错误返回 None。
        """
        try:
            with self._session_factory() as session:
                archive = session.scalar(
                    select(ArchiveRecord).where(
                        ArchiveRecord.batch_id == self._state.batch_id,
                        ArchiveRecord.archive_key == archive_key,
                    )
                )
                if archive is None:
                    raise RuntimeError(
                        f"archive_key={archive_key!r} 在 batch_id={self._state.batch_id} 下不存在"
                    )
                rev_no = repositories.apply_force_rerun_rules(
                    session,
                    archive=archive,
                    new_metadata=new_metadata,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
                session.commit()
                return rev_no
        except Exception as exc:
            self._state.db_error_count += 1
            logger.exception(
                "[DB] force_rerun_rules_for_archive 失败 %s: %s", archive_key, exc
            )
            return None

    def on_batch_finish(
        self,
        *,
        success_count: int,
        fail_count: int,
        failure_breakdown: Dict[str, int],
        # 终态默认与 repositories.finalize_batch 保持一致(success);生产路径下
        # BatchProcessor 总会显式传入 success/failed/partial_failed,此默认仅兜底 [R14]。
        batch_status: str = "success",
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
                    organization_id=self._organization_id,
                    created_by=self._created_by,
                )
                batch = repositories.get_or_create_batch(
                    session,
                    project_id=project.id,
                    batch_key=self._batch_key,
                    input_dir=self._input_dir,
                    output_dir=self._output_dir,
                    upload_batch_id=self._upload_batch_id,
                    trigger_type=self._trigger_type,
                    batch_name=self._batch_name,
                    organization_id=self._organization_id,
                    created_by=self._created_by,
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
        """成功处理的档案默认进入“待审核”(pending);带【待核查】标记的升级为
        “重点审核”(needs_review)。处理失败的档案由调用方置为 not_required(无需审核),
        不进入审核队列。"""
        if not metadata:
            return "pending"
        notes = str(metadata.get("备注") or "")
        if "【待核查】" in notes:
            return "needs_review"
        return "pending"


__all__ = ["BatchRecorder", "VALID_RERUN_POLICIES"]

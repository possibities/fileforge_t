"""档案相关路由的共享依赖:访问控制、组织隔离、查询参数解析、分页、
图片路径解析、可选值常量与工作台表单辅助。

拆分自原 `archives.py`,供 `archives.py` / `review.py` / `audit.py` 共用,
通过 `from web_admin.routes.archive_common import *` 引入(下方 `__all__`
显式导出下划线开头的内部名)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants import CODE_NEW, CODE_OLD
from infrastructure.db import projects as projects_service, queries
from infrastructure.db.models import (
    ArchivePage,
    ArchiveRecord,
    ProcessingBatch,
    Project,
    UploadBatch,
    UploadedFile,
)
from web_admin.auth import CurrentUser
from web_admin.routes import has_platform_scope, load_current_user_from_request


ARCHIVE_VIEW_PERMISSION = "archive:view"
AUDIT_VIEW_PERMISSION = "audit:view"
ARCHIVE_CORRECT_PERMISSION = "archive:correct"
ARCHIVE_DELETE_PERMISSION = "archive:delete"
PREVIEW_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def _can_access_organization(
    current_user: CurrentUser,
    organization_id: Optional[int],
) -> bool:
    if has_platform_scope(current_user):
        return True
    return organization_id is not None and organization_id == current_user.organization_id


def _require_archive_view(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if ARCHIVE_VIEW_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _require_archive_correct(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return current_user, error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _require_archive_delete(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return current_user, error_response
    if ARCHIVE_DELETE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _project_by_key(session: Session, project_key: str) -> Optional[Project]:
    return session.scalar(select(Project).where(Project.project_key == project_key))


def _can_access_batch(
    session: Session,
    current_user: CurrentUser,
    batch: ProcessingBatch,
) -> Optional[Project]:
    project = session.get(Project, batch.project_id)
    if project is None or not _can_access_organization(current_user, project.organization_id):
        return None
    if (
        not has_platform_scope(current_user)
        and batch.organization_id is not None
        and batch.organization_id != current_user.organization_id
    ):
        return None
    return project


def _can_access_archive(
    session: Session,
    current_user: CurrentUser,
    archive: ArchiveRecord,
) -> Optional[tuple[ProcessingBatch, Project]]:
    batch = session.get(ProcessingBatch, archive.batch_id)
    if batch is None:
        return None
    project = _can_access_batch(session, current_user, batch)
    if project is None:
        return None
    if (
        not has_platform_scope(current_user)
        and archive.organization_id is not None
        and archive.organization_id != current_user.organization_id
    ):
        return None
    return batch, project


def _as_list(values: Optional[list[str]]) -> list[str]:
    return [value.strip() for value in (values or []) if value and value.strip()]


def _clean_optional_str(value: Optional[str]) -> Optional[str]:
    cleaned = (value or "").strip()
    return cleaned or None


def _parse_optional_int_query(value: Optional[str], *, name: str) -> Optional[int]:
    cleaned = _clean_optional_str(value)
    if cleaned is None:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{name}必须是整数") from exc


def _parse_int_query(value: Optional[str], *, name: str, default: int) -> int:
    cleaned = _clean_optional_str(value)
    if cleaned is None:
        return default
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{name}必须是整数") from exc


def _scoped_organization_id(current_user: CurrentUser) -> Optional[int]:
    if has_platform_scope(current_user):
        return None
    return current_user.organization_id


def _available_projects(session: Session, current_user: CurrentUser):
    return projects_service.list_projects(
        session,
        organization_id=_scoped_organization_id(current_user),
    )


def _bad_request(exc: ValueError) -> Response:
    return Response(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=str(exc).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
    )


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_page_image_path(
    session: Session,
    *,
    page: ArchivePage,
    batch: ProcessingBatch,
) -> Optional[Path]:
    candidates: list[Path] = []

    if page.uploaded_file_id is not None:
        uploaded = session.get(UploadedFile, page.uploaded_file_id)
        if uploaded is not None:
            upload = session.get(UploadBatch, uploaded.upload_batch_id)
            upload_root = Path(upload.storage_root).resolve() if upload is not None else None
            stored_path = Path(uploaded.stored_path)
            candidate = (
                stored_path.resolve()
                if stored_path.is_absolute() or upload_root is None
                else (upload_root / stored_path).resolve()
            )
            if upload_root is not None and _is_relative_to(candidate, upload_root):
                candidates.append(candidate)

    if batch.input_dir:
        input_root = Path(batch.input_dir).resolve()
        image_path = Path(page.image_path.replace("\\", "/"))
        candidate = (
            image_path.resolve()
            if image_path.is_absolute()
            else (input_root / image_path).resolve()
        )
        if _is_relative_to(candidate, input_root):
            candidates.append(candidate)

    for candidate in candidates:
        if candidate.suffix.lower() in PREVIEW_IMAGE_EXTENSIONS and candidate.is_file():
            return candidate
    return None


_SORT_DIRECTIONS = {"asc", "desc"}

# 档案实际可能出现的处理状态(检索筛选下拉用);完整枚举 PROCESSING_STATUS 还含
# ocr_running/llm_running 等 job 级分阶段状态,只出现在处理任务上,不会落到档案。
_ARCHIVE_PROCESSING_STATUS_CHOICES = ("queued", "running", "success", "failed", "error")

# 审核筛选只暴露当前流程在用的 3 个值(legacy 的 not_required/in_review/confirmed 不展示)。
_ARCHIVE_REVIEW_STATUS_CHOICES = ("pending", "needs_review", "reviewed")

# 实体分类号可选值:2020 起新码 + 2020 前旧码,(code, 展示标签) 对。
_CLASSIFICATION_CODE_CHOICES = (
    [(code, f"{code} · {name}") for name, code in CODE_NEW.items()]
    + [(code, f"{code} · {name}(2020前)") for name, code in CODE_OLD.items()]
)

# 实体分类号 → 名称(保存时由号自动同步名称,二者保持一致)。
_CODE_TO_CLASS_NAME: dict[str, str] = {}
for _cmap in (CODE_NEW, CODE_OLD):
    for _cname, _ccode in _cmap.items():
        _CODE_TO_CLASS_NAME[_ccode] = _cname


def _current_values_from_archive(archive: ArchiveRecord) -> dict[str, str]:
    md = archive.final_metadata or {}
    return {
        "title": md.get("题名") or archive.title or "",
        "responsible_party": md.get("责任者") or archive.responsible_party or "",
        "classification_code": md.get("实体分类号") or archive.classification_code or "",
        "retention_period": md.get("保管期限") or archive.retention_period or "永久",
        "openness_status": md.get("开放状态") or archive.openness_status or "",
        "archive_year": md.get("归档年度") or archive.archive_year or "",
        "document_number": md.get("文件编号") or archive.document_number or "",
        "fonds_unit_name": md.get("立档单位名称") or archive.fonds_unit_name or "",
        "reason": "",
    }


def _clean_form_field(
    raw: Optional[str],
    *,
    max_len: int,
    name: str,
    required: bool = True,
) -> tuple[Optional[str], Optional[str]]:
    value = (raw or "").strip()
    if not value:
        if required:
            return None, f"{name}不能为空"
        return "", None
    if len(value) > max_len:
        return None, f"{name}长度不能超过 {max_len} 字符"
    return value, None


__all__ = [
    "ARCHIVE_VIEW_PERMISSION",
    "AUDIT_VIEW_PERMISSION",
    "ARCHIVE_CORRECT_PERMISSION",
    "ARCHIVE_DELETE_PERMISSION",
    "PREVIEW_IMAGE_EXTENSIONS",
    "_SORT_DIRECTIONS",
    "_ARCHIVE_PROCESSING_STATUS_CHOICES",
    "_ARCHIVE_REVIEW_STATUS_CHOICES",
    "_CLASSIFICATION_CODE_CHOICES",
    "_CODE_TO_CLASS_NAME",
    "_can_access_organization",
    "_require_archive_view",
    "_require_archive_correct",
    "_require_archive_delete",
    "_project_by_key",
    "_can_access_batch",
    "_can_access_archive",
    "_as_list",
    "_clean_optional_str",
    "_parse_optional_int_query",
    "_parse_int_query",
    "_scoped_organization_id",
    "_available_projects",
    "_bad_request",
    "_is_relative_to",
    "_resolve_page_image_path",
    "_current_values_from_archive",
    "_clean_form_field",
]

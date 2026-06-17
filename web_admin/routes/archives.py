from __future__ import annotations

import csv
import io
import math
import mimetypes
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from config.config import Config
from constants import CODE_NEW, CODE_OLD
from processors.exporter import Exporter
from infrastructure.db import projects as projects_service, queries
from infrastructure.db.models import (
    CORRECTION_STATUS,
    ArchivePage,
    ArchiveRecord,
    ProcessingBatch,
    Project,
    UploadBatch,
    UploadedFile,
)
from infrastructure.db.repositories import (
    RETENTION_PERIOD_CHOICES,
    ManualCorrectionInput,
    apply_manual_correction,
    delete_archive,
    delete_processing_batch,
    record_audit_log,
)

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    has_platform_scope,
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter()


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


@router.get("/batches")
def list_batches(
    request: Request,
    project_key: Optional[str] = None,
    status_filter: Optional[list[str]] = Query(default=None),
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    templates = request.app.state.templates
    cleaned_project_key = (project_key or "").strip()
    statuses = _as_list(status_filter)
    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
    except ValueError as exc:
        return _bad_request(exc)
    context = {
        "user": current_user,
        "project_key": cleaned_project_key,
        "projects": _available_projects(session, current_user),
        "status_filter": statuses,
        "selected_status": statuses[0] if statuses else "",
        "result": None,
        "error": None,
    }

    if not cleaned_project_key:
        return templates.TemplateResponse(request, "batches_list.html", context)

    project = _project_by_key(session, cleaned_project_key)
    if project is None or not _can_access_organization(current_user, project.organization_id):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    try:
        result = queries.list_batches(
            session,
            project_key=cleaned_project_key,
            status_filter=statuses,
            organization_id=_scoped_organization_id(current_user),
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    context["result"] = result
    return templates.TemplateResponse(request, "batches_list.html", context)


@router.get("/batches/{batch_id}")
def get_batch_detail(
    request: Request,
    batch_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    project = _can_access_batch(session, current_user, batch)
    if project is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    detail = queries.get_batch_detail(session, batch_id=batch_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "batch_detail.html",
        {
            "user": current_user,
            "project": project,
            "batch": detail,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
        },
    )


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


def _archive_filter_from_query(query) -> queries.ArchiveFilter:
    """从 query_params 组装 ArchiveFilter;沿用 list 语义的字段用 getlist。"""
    return queries.ArchiveFilter(
        archive_year=_parse_optional_int_query(query.get("archive_year"), name="年度"),
        classification_code=_as_list(query.getlist("classification_code")),
        retention_period=_as_list(query.getlist("retention_period")),
        openness_status=_clean_optional_str(query.get("openness_status")),
        processing_status=_as_list(query.getlist("processing_status")),
        review_status=_as_list(query.getlist("review_status")),
        correction_status=_clean_optional_str(query.get("correction_status")),
        archive_no=_clean_optional_str(query.get("archive_no")),
        item_no=_clean_optional_str(query.get("item_no")),
        title_like=_clean_optional_str(query.get("title_like")),
        responsible_party_like=_clean_optional_str(query.get("responsible_party_like")),
        error_code=_as_list(query.getlist("error_code")),
    )


def _render_archive_search(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    path: str,
    project,
    batch,
    scope_locked: bool,
) -> Response:
    """渲染共享的档案检索表格(全局 /archives 与按批次 /batches/{id}/archives 共用)。

    scope_locked=True 时项目/批次被锁定(批次视图),否则展示项目/批次下拉。
    所有排序/过滤/分页都走 query string,服务端渲染;点击行直接进入档案详情页。
    """
    query = request.query_params

    try:
        archive_filter = _archive_filter_from_query(query)
        page_num = _parse_int_query(query.get("page"), name="page", default=1)
        page_size_num = _parse_int_query(query.get("page_size"), name="page_size", default=50)
    except ValueError as exc:
        return _bad_request(exc)

    sort_field = (query.get("sort") or "").strip()
    if sort_field not in queries.ARCHIVE_SORT_FIELDS:
        sort_field = ""
    sort_dir = (query.get("dir") or "asc").strip().lower()
    if sort_dir not in _SORT_DIRECTIONS:
        sort_dir = "asc"

    project_key = project.project_key if project else None
    batch_id = batch.id if batch else None

    try:
        result = queries.search_archives(
            session,
            filter=archive_filter,
            organization_id=_scoped_organization_id(current_user),
            project_key=project_key,
            batch_id=batch_id,
            sort_field=sort_field or None,
            sort_dir=sort_dir,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    filters = {
        name: (query.get(name) or "").strip()
        for name in (
            "archive_no", "item_no", "title_like", "responsible_party_like",
            "classification_code", "archive_year", "retention_period",
            "openness_status", "processing_status", "review_status",
            "correction_status",
        )
    }

    # 链接构造:保留 scope(全局视图)+ 已应用过滤 + page_size。
    base_params: dict = {}
    if not scope_locked:
        if project_key:
            base_params["project_key"] = project_key
        if batch_id is not None:
            base_params["batch_id"] = batch_id
    for name, value in filters.items():
        if value:
            base_params[name] = value
    base_params["page_size"] = page_size_num

    def _url(**overrides) -> str:
        merged = dict(base_params)
        merged.update(overrides)
        clean = {k: v for k, v in merged.items() if v not in (None, "", [])}
        qs = urlencode(clean)
        return f"{path}?{qs}" if qs else path

    sort_links = {}
    for key in queries.ARCHIVE_SORT_FIELDS:
        new_dir = "desc" if (sort_field == key and sort_dir == "asc") else "asc"
        sort_links[key] = _url(sort=key, dir=new_dir)  # 翻页位 reset 到 1

    sort_overrides = {"sort": sort_field, "dir": sort_dir} if sort_field else {}
    prev_url = _url(page=page_num - 1, **sort_overrides) if page_num > 1 else None
    next_url = _url(page=page_num + 1, **sort_overrides) if result.has_next else None
    total_pages = max(1, math.ceil(result.total / page_size_num)) if result.total else 1

    clear_scope: dict = {}
    if not scope_locked:
        if project_key:
            clear_scope["project_key"] = project_key
        if batch_id is not None:
            clear_scope["batch_id"] = batch_id
    clear_qs = urlencode(clear_scope)
    clear_url = f"{path}?{clear_qs}" if clear_qs else path

    # 导出链接始终携带 scope + 已应用过滤(导出路由是全局 /archives/export.csv)
    export_params: dict = {}
    if project_key:
        export_params["project_key"] = project_key
    if batch_id is not None:
        export_params["batch_id"] = batch_id
    for _name, _value in filters.items():
        if _value:
            export_params[_name] = _value
    export_qs = urlencode(export_params)
    export_url = "/archives/export.csv" + (f"?{export_qs}" if export_qs else "")

    # 整套当前状态(除 page),供跳页表单的隐藏域复用。
    state_params = dict(base_params)
    if sort_field:
        state_params["sort"] = sort_field
        state_params["dir"] = sort_dir

    projects = _available_projects(session, current_user) if not scope_locked else []
    batches = []
    if not scope_locked and project_key:
        try:
            batches = queries.list_batches(
                session,
                project_key=project_key,
                organization_id=_scoped_organization_id(current_user),
                page=1,
                page_size=200,
            ).items
        except ValueError:
            batches = []

    templates = request.app.state.templates
    # 局部刷新:JS 带 X-Requested-With: fetch 时只回表格片段,整页请求回完整页。
    is_fragment = request.headers.get("x-requested-with", "").lower() == "fetch"
    template_name = "_archive_grid.html" if is_fragment else "archive_search.html"
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "user": current_user,
            "path": path,
            "scope_locked": scope_locked,
            "project": project,
            "batch": batch,
            "projects": projects,
            "batches": batches,
            "project_key": project_key or "",
            "batch_id": batch_id,
            "result": result,
            "filters": filters,
            "sort_field": sort_field,
            "sort_dir": sort_dir,
            "sort_links": sort_links,
            "page": page_num,
            "page_size": page_size_num,
            "total_pages": total_pages,
            "prev_url": prev_url,
            "next_url": next_url,
            "clear_url": clear_url,
            "export_url": export_url,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "state_params": state_params,
            "processing_status_choices": list(_ARCHIVE_PROCESSING_STATUS_CHOICES),
            "classification_code_choices": _CLASSIFICATION_CODE_CHOICES,
            "review_status_choices": list(_ARCHIVE_REVIEW_STATUS_CHOICES),
            "correction_status_choices": list(CORRECTION_STATUS),
            "openness_status_choices": ["开放", "控制"],
            "retention_period_choices": list(RETENTION_PERIOD_CHOICES),
            "page_size_choices": [50, 100, 200],
        },
    )


@router.get("/archives")
def search_archives_page(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """全局档案检索页:跨批次/项目,按当前用户单位自动隔离。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    query = request.query_params
    project = None
    project_key = _clean_optional_str(query.get("project_key"))
    if project_key:
        project = _project_by_key(session, project_key)
        if project is None or not _can_access_organization(
            current_user, project.organization_id
        ):
            return Response(status_code=status.HTTP_404_NOT_FOUND)

    batch = None
    batch_id_raw = _clean_optional_str(query.get("batch_id"))
    if batch_id_raw:
        try:
            batch_id_val = int(batch_id_raw)
        except ValueError:
            return _bad_request(ValueError("batch_id必须是整数"))
        batch = session.get(ProcessingBatch, batch_id_val)
        if batch is None:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        batch_project = _can_access_batch(session, current_user, batch)
        if batch_project is None:
            return Response(status_code=status.HTTP_404_NOT_FOUND)
        if project is None:
            project = batch_project

    return _render_archive_search(
        request,
        current_user=current_user,
        session=session,
        path="/archives",
        project=project,
        batch=batch,
        scope_locked=False,
    )


def _ensure_exporter_initialized() -> None:
    if not Exporter.HEADERS:
        Exporter.initialize(Config.EXPORTER_CONFIG_PATH)


@router.get("/archives/export.csv")
def export_archives_csv(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """按当前查询条件导出匹配档案为 CSV(UTF-8 BOM + exporter 模板表头)。

    路由定义在 /archives/{archive_id} 之前,避免被单段路径参数路由抢匹配。
    """
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if "archive:export" not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    query = request.query_params
    try:
        archive_filter = _archive_filter_from_query(query)
    except ValueError as exc:
        return _bad_request(exc)

    project_key = _clean_optional_str(query.get("project_key"))
    batch_id = None
    batch_id_raw = _clean_optional_str(query.get("batch_id"))
    if batch_id_raw:
        try:
            batch_id = int(batch_id_raw)
        except ValueError:
            return _bad_request(ValueError("batch_id必须是整数"))

    metadatas = queries.export_archive_metadata(
        session,
        filter=archive_filter,
        organization_id=_scoped_organization_id(current_user),
        project_key=project_key,
        batch_id=batch_id,
    )

    _ensure_exporter_initialized()
    headers = Exporter.get_headers("default")
    rows = Exporter._build_export_rows([{"metadata": m} for m in metadatas], headers)
    buffer = io.StringIO()
    buffer.write("﻿")  # BOM,便于 Excel 正确识别中文
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        content=buffer.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="archives.csv"'},
    )


@router.get("/batches/{batch_id}/archives")
def list_archives(
    request: Request,
    batch_id: int,
    session: Session = Depends(get_session),
) -> Response:
    """按批次的档案检索:复用全局检索表格,锁定项目/批次范围。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    project = _can_access_batch(session, current_user, batch)
    if project is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return _render_archive_search(
        request,
        current_user=current_user,
        session=session,
        path=f"/batches/{batch_id}/archives",
        project=project,
        batch=batch,
        scope_locked=True,
    )



@router.get("/archives/{archive_id}")
def get_archive_detail(
    request: Request,
    archive_id: int,
    notice: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    detail = queries.get_archive_detail(session, archive_id=archive_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archive_detail.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": detail,
            "notice": notice,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
        },
    )


@router.get("/archives/{archive_id}/panel")
def get_archive_panel(
    request: Request,
    archive_id: int,
    session: Session = Depends(get_session),
) -> Response:
    """档案详情片段(右栏主从预览用):复用 archive_detail 的数据与组织隔离。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    detail = queries.get_archive_detail(session, archive_id=archive_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "_archive_panel.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": detail,
            "compact": True,
        },
    )


@router.get("/archives/{archive_id}/pages/{page_id}/image")
def get_archive_page_image(
    request: Request,
    archive_id: int,
    page_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, _project = access

    page = session.get(ArchivePage, page_id)
    if page is None or page.archive_id != archive_id:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    image_path = _resolve_page_image_path(session, page=page, batch=batch)
    if image_path is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    media_type = mimetypes.guess_type(str(image_path))[0] or "application/octet-stream"
    return FileResponse(str(image_path), media_type=media_type)


@router.get("/archives/{archive_id}/revisions")
def list_archive_revisions(
    request: Request,
    archive_id: int,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
        result = queries.list_revisions(
            session,
            archive_id=archive_id,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "revisions_list.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "result": result,
        },
    )


@router.get("/archives/{archive_id}/audit")
def list_archive_audit_logs(
    request: Request,
    archive_id: int,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if AUDIT_VIEW_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
        result = queries.list_audit_logs(
            session,
            target_type="archive",
            target_id=archive_id,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "audit_list.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "result": result,
        },
    )


@router.get("/admin/audit")
def global_audit_log(
    request: Request,
    action: Optional[str] = None,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    """全局审计记录:跨档案/批次/项目的操作留痕,按单位隔离,可按动作筛选。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if AUDIT_VIEW_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
    except ValueError as exc:
        return _bad_request(exc)

    org_id = _scoped_organization_id(current_user)
    clean_action = (action or "").strip() or None
    try:
        result = queries.search_audit_logs(
            session,
            organization_id=org_id,
            action=clean_action,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    action_choices = queries.audit_action_choices(session, organization_id=org_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "audit_global.html",
        {
            "user": current_user,
            "result": result,
            "action_choices": action_choices,
            "selected_action": clean_action or "",
            "page": page_num,
            "page_size": page_size_num,
        },
    )


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


@router.post("/archives/{archive_id}/delete")
def post_delete_archive(
    request: Request,
    archive_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """硬删除单份档案(连带页面/轨迹/修订),删除前写审计。"""
    current_user, error_response = _require_archive_delete(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    try:
        delete_archive(session, archive=archive, actor_user_id=current_user.id)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RedirectResponse(url="/archives", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/batches/{batch_id}/delete")
def post_delete_batch(
    request: Request,
    batch_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """硬删除整个处理批次(连带其下所有档案、任务、事件),删除前写审计。"""
    current_user, error_response = _require_archive_delete(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    project = _can_access_batch(session, current_user, batch)
    if project is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    project_key = project.project_key

    try:
        delete_processing_batch(session, batch=batch, actor_user_id=current_user.id)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RedirectResponse(
        url=f"/batches?project_key={project_key}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/archives/{archive_id}/review")
def post_mark_archive_reviewed(
    request: Request,
    archive_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """把档案标记为已复核(review_status=reviewed),写审计。需 archive:correct。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    old_status = archive.review_status
    archive.review_status = "reviewed"
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        organization_id=archive.organization_id,
        project_id=archive.project_id,
        action="archive_reviewed",
        target_type="archive",
        target_id=archive.id,
        before_data={"review_status": old_status},
        after_data={"review_status": "reviewed"},
    )
    session.commit()
    return RedirectResponse(
        url=f"/archives/{archive_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/archives/bulk-delete")
def post_bulk_delete_archives(
    request: Request,
    archive_id: list[int] = Form(default=[]),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """批量硬删除选中档案;逐条校验组织权限,跳过越权/不存在项。"""
    current_user, error_response = _require_archive_delete(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    for aid in archive_id:
        archive = session.get(ArchiveRecord, aid)
        if archive is None:
            continue
        if _can_access_archive(session, current_user, archive) is None:
            continue
        delete_archive(session, archive=archive, actor_user_id=current_user.id)
    session.commit()
    return RedirectResponse(url="/archives", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/review")
def review_queue(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """验证(待复核)队列:列出本权限范围内 review_status=needs_review 的档案。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    query = request.query_params
    try:
        page_num = _parse_int_query(query.get("page"), name="page", default=1)
    except ValueError as exc:
        return _bad_request(exc)

    result = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=page_num,
        page_size=50,
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "review_list.html",
        {
            "user": current_user,
            "result": result,
            "page": page_num,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "prev_url": f"/review?page={page_num - 1}" if page_num > 1 else None,
            "next_url": f"/review?page={page_num + 1}" if result.has_next else None,
        },
    )


@router.post("/review/mark-reviewed")
def post_review_mark_reviewed(
    request: Request,
    archive_id: list[int] = Form(default=[]),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """批量把选中档案标记为已复核(review_status=reviewed),逐条校验组织权限并写审计。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    for aid in archive_id:
        archive = session.get(ArchiveRecord, aid)
        if archive is None:
            continue
        if _can_access_archive(session, current_user, archive) is None:
            continue
        old_status = archive.review_status
        archive.review_status = "reviewed"
        record_audit_log(
            session,
            actor_user_id=current_user.id,
            organization_id=archive.organization_id,
            project_id=archive.project_id,
            action="archive_reviewed",
            target_type="archive",
            target_id=archive.id,
            before_data={"review_status": old_status},
            after_data={"review_status": "reviewed"},
        )
    session.commit()
    return RedirectResponse(url="/review", status_code=status.HTTP_303_SEE_OTHER)


def _render_workstation(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    archive,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    notice: Optional[str] = None,
) -> Response:
    """渲染审核工作台:左=待审核队列,中=页面图像,右=可改元数据。"""
    queue = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=1,
        page_size=200,
    ).items
    if values is None:
        values = _current_values_from_archive(archive)
    return request.app.state.templates.TemplateResponse(
        request,
        "workstation.html",
        {
            "user": current_user,
            "archive": archive,
            "queue": queue,
            "values": values,
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "classification_choices": _CLASSIFICATION_CODE_CHOICES,
            "openness_choices": ["开放", "控制"],
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "error": error,
            "notice": notice,
        },
    )


@router.get("/review/{archive_id}")
def review_workstation(
    request: Request,
    archive_id: int,
    notice: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive_rec = session.get(ArchiveRecord, archive_id)
    if archive_rec is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive_rec) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    detail = queries.get_archive_detail(session, archive_id=archive_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return _render_workstation(
        request,
        current_user=current_user,
        session=session,
        archive=detail,
        notice="已保存修改。" if notice == "saved" else None,
    )


@router.post("/review/{archive_id}/save")
def post_review_save(
    request: Request,
    archive_id: int,
    title: Optional[str] = Form(default=None),
    responsible_party: Optional[str] = Form(default=None),
    classification_code: Optional[str] = Form(default=None),
    retention_period: Optional[str] = Form(default=None),
    openness_status: Optional[str] = Form(default=None),
    archive_year: Optional[str] = Form(default=None),
    document_number: Optional[str] = Form(default=None),
    fonds_unit_name: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """在工作台保存人工修正(题名/责任者/分类号/保管期限/开放状态/年度/文号/立档单位)。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    err: Optional[str] = None
    clean_title, err = _clean_form_field(title, max_len=500, name="题名")
    clean_party = clean_class = clean_retention = clean_reason = None
    if err is None:
        clean_party, err = _clean_form_field(responsible_party, max_len=200, name="责任者")
    if err is None:
        clean_class, err = _clean_form_field(classification_code, max_len=32, name="实体分类号")
    if err is None:
        clean_retention = (retention_period or "").strip()
        if clean_retention not in RETENTION_PERIOD_CHOICES:
            err = f"保管期限必须为 {', '.join(RETENTION_PERIOD_CHOICES)} 之一"
    clean_openness = clean_year = clean_docnum = clean_fonds = None
    if err is None:
        clean_openness = (openness_status or "").strip()
        if clean_openness not in ("开放", "控制"):
            err = "开放状态必须为 开放 或 控制"
    if err is None:
        clean_year, err = _clean_form_field(archive_year, max_len=8, name="归档年度", required=False)
        if err is None and clean_year and not clean_year.isdigit():
            err = "归档年度必须是数字"
    if err is None:
        clean_docnum, err = _clean_form_field(document_number, max_len=128, name="文件编号", required=False)
    if err is None:
        clean_fonds, err = _clean_form_field(fonds_unit_name, max_len=255, name="立档单位名称", required=False)
    if err is None:
        clean_reason, err = _clean_form_field(reason, max_len=500, name="原因", required=False)

    if err is not None:
        detail = queries.get_archive_detail(session, archive_id=archive_id)
        return _render_workstation(
            request,
            current_user=current_user,
            session=session,
            archive=detail,
            values={
                "title": (title or "").strip(),
                "responsible_party": (responsible_party or "").strip(),
                "classification_code": (classification_code or "").strip(),
                "retention_period": (retention_period or "").strip(),
                "openness_status": (openness_status or "").strip(),
                "archive_year": (archive_year or "").strip(),
                "document_number": (document_number or "").strip(),
                "fonds_unit_name": (fonds_unit_name or "").strip(),
                "reason": (reason or "").strip(),
            },
            error=err,
        )

    try:
        apply_manual_correction(
            session,
            archive=archive,
            new_values=ManualCorrectionInput(
                title=clean_title,
                responsible_party=clean_party,
                classification_code=clean_class,
                retention_period=clean_retention,
                classification_name=_CODE_TO_CLASS_NAME.get(clean_class),
                openness_status=clean_openness,
                archive_year=clean_year or "",
                document_number=clean_docnum or "",
                fonds_unit_name=clean_fonds or "",
            ),
            actor_user_id=current_user.id,
            reason=clean_reason or None,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RedirectResponse(
        url=f"/review/{archive_id}?notice=saved", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/review/{archive_id}/done")
def post_review_done(
    request: Request,
    archive_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """标记为已审核并跳到队列中的下一条;队列空则回到审核列表。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    old_status = archive.review_status
    archive.review_status = "reviewed"
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        organization_id=archive.organization_id,
        project_id=archive.project_id,
        action="archive_reviewed",
        target_type="archive",
        target_id=archive.id,
        before_data={"review_status": old_status},
        after_data={"review_status": "reviewed"},
    )
    session.commit()

    nxt = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=1,
        page_size=1,
    ).items
    target = f"/review/{nxt[0].id}" if nxt else "/review"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

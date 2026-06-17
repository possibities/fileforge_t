from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from constants import CODE_NEW, CODE_OLD
from infrastructure.db import projects as projects_service, queries
from infrastructure.db.models import (
    CORRECTION_STATUS,
    REVIEW_STATUS,
    ArchivePage,
    ArchiveRecord,
    ProcessingBatch,
    Project,
    UploadBatch,
    UploadedFile,
)
from infrastructure.db.repositories import (
    EDITABLE_FIELDS,
    RETENTION_PERIOD_CHOICES,
    ManualCorrectionInput,
    apply_manual_correction,
    delete_archive,
    delete_processing_batch,
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

# 实体分类号可选值:2020 起新码 + 2020 前旧码,(code, 展示标签) 对。
_CLASSIFICATION_CODE_CHOICES = (
    [(code, f"{code} · {name}") for name, code in CODE_NEW.items()]
    + [(code, f"{code} · {name}(2020前)") for name, code in CODE_OLD.items()]
)


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
    所有排序/过滤/分页/选中态都走 query string,服务端渲染;右栏详情由 selected 决定。
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

    # 选中档案 → 右栏详情;组织越权或不存在则忽略选中态。
    selected_id = None
    selected_raw = _clean_optional_str(query.get("selected"))
    if selected_raw:
        try:
            selected_id = int(selected_raw)
        except ValueError:
            selected_id = None
    panel_archive = None
    if selected_id is not None:
        record = session.get(ArchiveRecord, selected_id)
        if record is not None and _can_access_archive(session, current_user, record) is not None:
            panel_archive = queries.get_archive_detail(session, archive_id=selected_id)
        else:
            selected_id = None

    filters = {
        name: (query.get(name) or "").strip()
        for name in (
            "archive_no", "item_no", "title_like", "responsible_party_like",
            "classification_code", "archive_year", "retention_period",
            "openness_status", "processing_status", "review_status",
            "correction_status",
        )
    }

    # 链接构造:保留 scope(全局视图)+ 已应用过滤 + page_size + 选中态。
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
    if selected_id is not None:
        base_params["selected"] = selected_id

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

    # 整套当前状态(除 page),供跳页表单的隐藏域 / 行链接复用。
    state_params = dict(base_params)
    if sort_field:
        state_params["sort"] = sort_field
        state_params["dir"] = sort_dir
    row_base_qs = urlencode({k: v for k, v in state_params.items() if k != "selected"})

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
            "state_params": state_params,
            "row_base_qs": row_base_qs,
            "selected_id": selected_id,
            "panel_archive": panel_archive,
            "processing_status_choices": list(_ARCHIVE_PROCESSING_STATUS_CHOICES),
            "classification_code_choices": _CLASSIFICATION_CODE_CHOICES,
            "review_status_choices": list(REVIEW_STATUS),
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


def _readonly_fields(archive: ArchiveRecord) -> list[tuple[str, str]]:
    md = dict(archive.final_metadata or {})
    seen = set(EDITABLE_FIELDS)
    return [(key, md.get(key) or "") for key in md.keys() if key not in seen]


def _current_values_from_archive(archive: ArchiveRecord) -> dict[str, str]:
    md = archive.final_metadata or {}
    return {
        "title": md.get("题名") or archive.title or "",
        "responsible_party": md.get("责任者") or archive.responsible_party or "",
        "classification_code": md.get("实体分类号") or archive.classification_code or "",
        "retention_period": md.get("保管期限") or archive.retention_period or "永久",
        "reason": "",
    }


def _render_edit_form(
    request: Request,
    *,
    current_user: CurrentUser,
    project,
    batch,
    archive: ArchiveRecord,
    values: dict[str, str],
    error: Optional[str],
) -> Response:
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archive_edit.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "values": values,
            "readonly_fields": _readonly_fields(archive),
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "csrf_token": csrf_token,
            "error": error,
        },
    )


@router.get("/archives/{archive_id}/edit")
def get_archive_edit_form(
    request: Request,
    archive_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    return _render_edit_form(
        request,
        current_user=current_user,
        project=project,
        batch=batch,
        archive=archive,
        values=_current_values_from_archive(archive),
        error=None,
    )


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


@router.post("/archives/{archive_id}/edit")
def post_archive_edit(
    request: Request,
    archive_id: int,
    title: Optional[str] = Form(default=None),
    responsible_party: Optional[str] = Form(default=None),
    classification_code: Optional[str] = Form(default=None),
    retention_period: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response

    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    err: Optional[str] = None
    clean_title, err = _clean_form_field(title, max_len=500, name="题名")
    clean_party = clean_class = clean_retention = clean_reason = None
    if err is None:
        clean_party, err = _clean_form_field(
            responsible_party, max_len=200, name="责任者"
        )
    if err is None:
        clean_class, err = _clean_form_field(
            classification_code, max_len=32, name="实体分类号"
        )
    if err is None:
        clean_retention = (retention_period or "").strip()
        if clean_retention not in RETENTION_PERIOD_CHOICES:
            err = f"保管期限必须为 {', '.join(RETENTION_PERIOD_CHOICES)} 之一"
    if err is None:
        clean_reason, err = _clean_form_field(
            reason, max_len=500, name="原因", required=False,
        )

    submitted_values = {
        "title": (title or "").strip(),
        "responsible_party": (responsible_party or "").strip(),
        "classification_code": (classification_code or "").strip(),
        "retention_period": (retention_period or "").strip(),
        "reason": (reason or "").strip(),
    }

    if err is not None:
        return _render_edit_form(
            request,
            current_user=current_user,
            project=project,
            batch=batch,
            archive=archive,
            values=submitted_values,
            error=err,
        )

    try:
        rev_no = apply_manual_correction(
            session,
            archive=archive,
            new_values=ManualCorrectionInput(
                title=clean_title,
                responsible_party=clean_party,
                classification_code=clean_class,
                retention_period=clean_retention,
            ),
            actor_user_id=current_user.id,
            reason=clean_reason or None,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    if rev_no == 0:
        return RedirectResponse(
            url=f"/archives/{archive_id}?notice=no_change",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=f"/archives/{archive_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


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

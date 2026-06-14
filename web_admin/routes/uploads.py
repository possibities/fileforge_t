from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import projects as projects_service, queries, repositories
from infrastructure.db.models import ProcessingBatch, Project, UploadBatch
from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.processing import create_upload_processing_batch, run_upload_processing_batch
from web_admin.routes import (
    has_platform_scope,
    load_current_user_from_request,
    verify_csrf_from_request,
)
from web_admin.upload_storage import (
    build_upload_root,
    detect_source_type,
    ingest_upload_files,
    remove_upload_root,
)


router = APIRouter()


BATCH_MANAGE_PERMISSION = "batch:manage"


def _parse_optional_int_query(value: Optional[str], *, name: str) -> Optional[int]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{name}必须是整数") from exc


def _parse_int_query(value: Optional[str], *, name: str, default: int) -> int:
    parsed = _parse_optional_int_query(value, name=name)
    return default if parsed is None else parsed


def _bad_request(exc: ValueError) -> Response:
    return Response(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=str(exc).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
    )


def _require_batch_manage(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if BATCH_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _can_access_project(current_user: CurrentUser, project: Project) -> bool:
    if has_platform_scope(current_user):
        return True
    return project.organization_id is not None and project.organization_id == current_user.organization_id


def _available_projects(session: Session, current_user: CurrentUser):
    org_id = None if has_platform_scope(current_user) else current_user.organization_id
    return projects_service.list_projects(
        session,
        organization_id=org_id,
        status_filter=("active",),
    )


def _render_uploads(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    error: Optional[str] = None,
    selected_project_id: Optional[int] = None,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    org_id = None if has_platform_scope(current_user) else current_user.organization_id
    result = queries.list_upload_batches(
        session,
        project_id=selected_project_id,
        organization_id=org_id,
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "uploads_list.html",
        {
            "user": current_user,
            "result": result,
            "projects": _available_projects(session, current_user),
            "selected_project_id": selected_project_id,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/uploads")
def list_uploads(
    request: Request,
    project_id: Optional[str] = None,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_batch_manage(request, session)
    if error_response is not None:
        return error_response
    try:
        project_id_num = _parse_optional_int_query(project_id, name="project_id")
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
    except ValueError as exc:
        return _bad_request(exc)

    if project_id_num is not None:
        project = session.get(Project, project_id_num)
        if project is None or not _can_access_project(current_user, project):
            return Response(status_code=status.HTTP_404_NOT_FOUND)

    org_id = None if has_platform_scope(current_user) else current_user.organization_id
    result = queries.list_upload_batches(
        session,
        project_id=project_id_num,
        organization_id=org_id,
        page=page_num,
        page_size=page_size_num,
    )
    return request.app.state.templates.TemplateResponse(
        request,
        "uploads_list.html",
        {
            "user": current_user,
            "result": result,
            "projects": _available_projects(session, current_user),
            "selected_project_id": project_id_num,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "error": None,
        },
    )


@router.post("/uploads")
def create_upload(
    request: Request,
    project_id: int = Form(...),
    upload_name: Optional[str] = Form(default=None),
    files: list[UploadFile] = File(...),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_batch_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    project = session.get(Project, project_id)
    if project is None or not _can_access_project(current_user, project):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    clean_name = (upload_name or "").strip() or f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    settings = request.app.state.settings
    upload_root = build_upload_root(settings.upload_storage_root, project_key=project.project_key)

    upload = UploadBatch(
        project_id=project.id,
        uploaded_by=current_user.id,
        upload_name=clean_name,
        source_type=detect_source_type(files),
        status="uploading",
        storage_root=str(upload_root),
    )
    session.add(upload)
    session.flush()

    try:
        ingest_result = ingest_upload_files(
            session,
            upload_batch_id=upload.id,
            upload_root=upload_root,
            upload_name=clean_name,
            files=files,
            max_total_bytes=settings.max_upload_bytes,
            max_file_count=settings.max_upload_files,
        )
        upload.source_type = ingest_result.source_type
        upload.file_count = ingest_result.file_count
        upload.document_count = ingest_result.document_count
        upload.total_size_bytes = ingest_result.total_size_bytes
        upload.status = "uploaded"
        repositories.record_audit_log(
            session,
            actor_user_id=current_user.id,
            organization_id=project.organization_id,
            project_id=project.id,
            action="upload_created",
            target_type="upload",
            target_id=upload.id,
            message=f"创建上传批次 {clean_name}",
            payload={"file_count": upload.file_count, "document_count": upload.document_count},
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        remove_upload_root(upload_root)
        return _render_uploads(
            request,
            current_user=current_user,
            session=session,
            error=str(exc),
            selected_project_id=project.id,
        )

    return RedirectResponse(url="/uploads", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/uploads/{upload_batch_id}/start")
def start_upload_processing(
    request: Request,
    upload_batch_id: int,
    background_tasks: BackgroundTasks,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_batch_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    upload = session.get(UploadBatch, upload_batch_id)
    if upload is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    project = session.get(Project, upload.project_id)
    if project is None or not _can_access_project(current_user, project):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if upload.status not in {"uploaded", "validated", "failed"}:
        return Response(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=f"当前上传状态不能启动处理: {upload.status}".encode("utf-8"),
            media_type="text/plain; charset=utf-8",
        )

    try:
        batch = create_upload_processing_batch(
            session,
            upload_batch_id=upload.id,
            output_root=request.app.state.settings.processing_output_root,
            actor_user_id=current_user.id,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        return Response(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=str(exc).encode("utf-8"),
            media_type="text/plain; charset=utf-8",
        )

    background_tasks.add_task(
        run_upload_processing_batch,
        database_url=request.app.state.settings.database_url,
        upload_batch_id=upload.id,
        batch_key=batch.batch_key,
        output_root=request.app.state.settings.processing_output_root,
    )
    return RedirectResponse(
        url=f"/processing/batches/{batch.id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/processing/batches/{batch_id}")
def processing_batch_detail(
    request: Request,
    batch_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_batch_manage(request, session)
    if error_response is not None:
        return error_response
    batch = session.get(ProcessingBatch, batch_id)
    if batch is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    project = session.get(Project, batch.project_id)
    if project is None or not _can_access_project(current_user, project):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    jobs = queries.list_processing_jobs(session, batch_id=batch_id, page_size=200)
    events = queries.list_processing_events(session, batch_id=batch_id, limit=100)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "processing_batch_detail.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "jobs": jobs,
            "events": events,
        },
    )

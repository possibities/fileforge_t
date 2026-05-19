from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db import queries
from infrastructure.db.models import ArchiveRecord, ProcessingBatch, Project

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter()


ARCHIVE_VIEW_PERMISSION = "archive:view"
AUDIT_VIEW_PERMISSION = "audit:view"
ARCHIVE_CORRECT_PERMISSION = "archive:correct"


def _has_platform_scope(current_user: CurrentUser) -> bool:
    return "platform_admin" in current_user.roles


def _can_access_organization(
    current_user: CurrentUser,
    organization_id: Optional[int],
) -> bool:
    if _has_platform_scope(current_user):
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
        not _has_platform_scope(current_user)
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
        not _has_platform_scope(current_user)
        and archive.organization_id is not None
        and archive.organization_id != current_user.organization_id
    ):
        return None
    return batch, project


def _as_list(values: Optional[list[str]]) -> list[str]:
    return [value for value in (values or []) if value]


def _scoped_organization_id(current_user: CurrentUser) -> Optional[int]:
    if _has_platform_scope(current_user):
        return None
    return current_user.organization_id


def _bad_request(exc: ValueError) -> Response:
    return Response(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=str(exc).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/batches")
def list_batches(
    request: Request,
    project_key: Optional[str] = None,
    status_filter: Optional[list[str]] = Query(default=None),
    page: int = 1,
    page_size: int = 50,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    templates = request.app.state.templates
    cleaned_project_key = (project_key or "").strip()
    statuses = _as_list(status_filter)
    context = {
        "user": current_user,
        "project_key": cleaned_project_key,
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
            page=page,
            page_size=page_size,
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
        {"user": current_user, "project": project, "batch": detail},
    )


@router.get("/batches/{batch_id}/archives")
def list_archives(
    request: Request,
    batch_id: int,
    archive_year: Optional[int] = None,
    classification_code: Optional[list[str]] = Query(default=None),
    retention_period: Optional[list[str]] = Query(default=None),
    openness_status: Optional[str] = None,
    processing_status: Optional[list[str]] = Query(default=None),
    review_status: Optional[list[str]] = Query(default=None),
    correction_status: Optional[str] = None,
    archive_no: Optional[str] = None,
    item_no: Optional[str] = None,
    title_like: Optional[str] = None,
    responsible_party_like: Optional[str] = None,
    error_code: Optional[list[str]] = Query(default=None),
    page: int = 1,
    page_size: int = 50,
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

    archive_filter = queries.ArchiveFilter(
        archive_year=archive_year,
        classification_code=_as_list(classification_code),
        retention_period=_as_list(retention_period),
        openness_status=(openness_status or None),
        processing_status=_as_list(processing_status),
        review_status=_as_list(review_status),
        correction_status=(correction_status or None),
        archive_no=(archive_no or None),
        item_no=(item_no or None),
        title_like=(title_like or None),
        responsible_party_like=(responsible_party_like or None),
        error_code=_as_list(error_code),
    )
    try:
        result = queries.list_archives(
            session,
            batch_id=batch_id,
            filter=archive_filter,
            organization_id=_scoped_organization_id(current_user),
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        return _bad_request(exc)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "archives_list.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "result": result,
            "filters": {
                "archive_year": archive_year or "",
                "classification_code": _as_list(classification_code),
                "retention_period": _as_list(retention_period),
                "openness_status": openness_status or "",
                "processing_status": _as_list(processing_status),
                "review_status": _as_list(review_status),
                "correction_status": correction_status or "",
                "archive_no": archive_no or "",
                "item_no": item_no or "",
                "title_like": title_like or "",
                "responsible_party_like": responsible_party_like or "",
                "error_code": _as_list(error_code),
            },
        },
    )


@router.get("/archives/{archive_id}")
def get_archive_detail(
    request: Request,
    archive_id: int,
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
        },
    )


@router.get("/archives/{archive_id}/revisions")
def list_archive_revisions(
    request: Request,
    archive_id: int,
    page: int = 1,
    page_size: int = 50,
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
        result = queries.list_revisions(
            session,
            archive_id=archive_id,
            page=page,
            page_size=page_size,
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
    page: int = 1,
    page_size: int = 50,
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
        result = queries.list_audit_logs(
            session,
            target_type="archive",
            target_id=archive_id,
            page=page,
            page_size=page_size,
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


_EDITABLE_FIELD_KEYS: tuple[str, ...] = ("题名", "责任者", "实体分类号", "保管期限")


def _readonly_fields(archive: ArchiveRecord) -> list[tuple[str, str]]:
    md = dict(archive.final_metadata or {})
    seen = set(_EDITABLE_FIELD_KEYS)
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

    from infrastructure.db.repositories import RETENTION_PERIOD_CHOICES
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
            "values": _current_values_from_archive(archive),
            "readonly_fields": _readonly_fields(archive),
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "csrf_token": csrf_token,
            "error": None,
        },
    )

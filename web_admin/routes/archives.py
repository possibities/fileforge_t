from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db import queries
from infrastructure.db.models import ProcessingBatch, Project

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import load_current_user_from_request


router = APIRouter()


ARCHIVE_VIEW_PERMISSION = "archive:view"


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

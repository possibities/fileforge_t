from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts, projects as projects_service
from infrastructure.db.models import Project

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    has_platform_scope,
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter(prefix="/admin/projects")


PROJECT_MANAGE_PERMISSION = "project:manage"
PROJECT_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def _parse_optional_int_query(value: Optional[str], *, name: str) -> Optional[int]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"{name}必须是整数") from exc


def _require_project_manage(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(
            url="/login", status_code=status.HTTP_303_SEE_OTHER
        )
    if PROJECT_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    if not has_platform_scope(current_user) and current_user.organization_id is None:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _resolve_organization_id_for_create(
    current_user: CurrentUser,
    submitted: Optional[int],
) -> Optional[int]:
    if has_platform_scope(current_user):
        return submitted
    return current_user.organization_id


def _available_orgs(current_user: CurrentUser, session: Session):
    actives = accounts.list_organizations(session, status_filter=("active",))
    if has_platform_scope(current_user):
        return actives
    return [o for o in actives if o.id == current_user.organization_id]


def _render_new_form(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    values: dict,
    error: Optional[str],
) -> Response:
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "project_form.html",
        {
            "user": current_user,
            "csrf_token": csrf_token,
            "values": values,
            "available_organizations": _available_orgs(current_user, session),
            "org_locked": not has_platform_scope(current_user),
            "error": error,
        },
    )


@router.get("")
def list_projects_route(
    request: Request,
    organization_id: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response

    if has_platform_scope(current_user):
        try:
            effective_org_id = _parse_optional_int_query(
                organization_id,
                name="organization_id",
            )
        except ValueError as exc:
            return Response(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=str(exc).encode("utf-8"),
                media_type="text/plain; charset=utf-8",
            )
        orgs_for_filter = accounts.list_organizations(session, status_filter=("active",))
        show_org_filter = True
    else:
        effective_org_id = current_user.organization_id
        orgs_for_filter = []
        show_org_filter = False

    rows = projects_service.list_projects(session, organization_id=effective_org_id)

    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "projects_list.html",
        {
            "user": current_user,
            "projects": rows,
            "csrf_token": csrf_token,
            "show_org_filter": show_org_filter,
            "organizations_for_filter": orgs_for_filter,
            "filter_organization_id": effective_org_id,
        },
    )


@router.get("/new")
def get_new_project_form(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response

    initial_org_id = (
        current_user.organization_id
        if not has_platform_scope(current_user)
        else None
    )
    return _render_new_form(
        request,
        current_user=current_user,
        session=session,
        values={
            "project_key": "",
            "project_name": "",
            "description": "",
            "organization_id": initial_org_id,
        },
        error=None,
    )


@router.post("/new")
def post_new_project(
    request: Request,
    project_key: Optional[str] = Form(default=None),
    project_name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    organization_id: Optional[int] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    clean_key = (project_key or "").strip()
    clean_name = (project_name or "").strip()
    clean_desc = (description or "").strip()
    effective_org_id = _resolve_organization_id_for_create(
        current_user, organization_id
    )

    submitted_values = {
        "project_key": clean_key,
        "project_name": clean_name,
        "description": clean_desc,
        "organization_id": effective_org_id,
    }

    error: Optional[str] = None
    if clean_key and len(clean_key) > 128:
        error = "项目标识长度不能超过 128 字符"
    elif clean_key and not PROJECT_KEY_PATTERN.match(clean_key):
        error = "项目标识只能包含字母 数字 - _"
    elif len(clean_name) > 255:
        error = "整理项目名称长度不能超过 255 字符"
    elif len(clean_desc) > 1000:
        error = "描述长度不能超过 1000 字符"
    elif effective_org_id is None:
        error = "必须选择一个单位"

    if error is None:
        try:
            projects_service.create_project(
                session,
                project_key=clean_key,
                organization_id=effective_org_id,
                project_name=clean_name or None,
                description=clean_desc or None,
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            error = str(exc)

    if error is not None:
        return _render_new_form(
            request,
            current_user=current_user,
            session=session,
            values=submitted_values,
            error=error,
        )

    return RedirectResponse(
        url="/admin/projects",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/{project_id}/disable")
def post_disable_project(
    request: Request,
    project_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, project_id, "disabled", csrf_token)


@router.post("/{project_id}/enable")
def post_enable_project(
    request: Request,
    project_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, project_id, "active", csrf_token)


def _set_status(
    request: Request,
    session: Session,
    project_id: int,
    new_status: str,
    csrf_token: Optional[str],
) -> Response:
    current_user, error_response = _require_project_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    project = session.get(Project, project_id)
    if project is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if (
        not has_platform_scope(current_user)
        and project.organization_id != current_user.organization_id
    ):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    try:
        projects_service.set_project_status(
            session, project_id=project_id, status=new_status
        )
        session.commit()
    except ValueError:
        session.rollback()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return RedirectResponse(
        url="/admin/projects",
        status_code=status.HTTP_303_SEE_OTHER,
    )

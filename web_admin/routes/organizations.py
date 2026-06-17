from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts
from infrastructure.db.models import Organization

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter(prefix="/admin/organizations")


ORGANIZATION_MANAGE_PERMISSION = "organization:manage"


def _require_organization_manage(
    request: Request,
    session: Session,
) -> tuple[Optional[CurrentUser], Optional[Response]]:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(
            url="/login", status_code=status.HTTP_303_SEE_OTHER
        )
    if ORGANIZATION_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


@router.get("")
def list_organizations_route(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response

    orgs = accounts.list_organizations(session)
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "organizations_list.html",
        {
            "user": current_user,
            "organizations": orgs,
            "csrf_token": csrf_token,
        },
    )


@router.get("/new")
def get_new_organization_form(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response

    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "organization_form.html",
        {
            "user": current_user,
            "csrf_token": csrf_token,
            "values": {"name": ""},
            "error": None,
        },
    )


@router.post("/new")
def post_new_organization(
    request: Request,
    name: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    cleaned = (name or "").strip()
    templates = request.app.state.templates
    cookie_csrf = request.cookies.get("fileforge_csrf", "")

    error: Optional[str] = None
    if not cleaned:
        error = "名称不能为空"
    elif len(cleaned) > 255:
        error = "名称长度不能超过 255 字符"

    if error is None:
        try:
            accounts.create_organization(session, name=cleaned)
            session.commit()
        except ValueError as exc:
            session.rollback()
            error = str(exc)

    if error is not None:
        return templates.TemplateResponse(
            request,
            "organization_form.html",
            {
                "user": current_user,
                "csrf_token": cookie_csrf,
                "values": {"name": cleaned},
                "error": error,
            },
        )

    return RedirectResponse(
        url="/admin/organizations",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/{organization_id}/edit")
def get_organization_edit_form(
    request: Request,
    organization_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    org = session.get(Organization, organization_id)
    if org is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return request.app.state.templates.TemplateResponse(
        request,
        "organization_edit.html",
        {
            "user": current_user,
            "org": org,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "values": {"name": org.name},
            "error": None,
        },
    )


@router.post("/{organization_id}/edit")
def post_organization_edit(
    request: Request,
    organization_id: int,
    name: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    org = session.get(Organization, organization_id)
    if org is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    cleaned = (name or "").strip()
    cookie_csrf = request.cookies.get("fileforge_csrf", "")
    error: Optional[str] = None
    if not cleaned:
        error = "名称不能为空"
    elif len(cleaned) > 255:
        error = "名称长度不能超过 255 字符"
    if error is None:
        try:
            accounts.rename_organization(
                session, organization_id=organization_id, name=cleaned
            )
            session.commit()
        except ValueError as exc:
            session.rollback()
            error = str(exc)
    if error is not None:
        return request.app.state.templates.TemplateResponse(
            request,
            "organization_edit.html",
            {
                "user": current_user,
                "org": org,
                "csrf_token": cookie_csrf,
                "values": {"name": cleaned},
                "error": error,
            },
        )
    return RedirectResponse(
        url="/admin/organizations", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{organization_id}/disable")
def post_disable_organization(
    request: Request,
    organization_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, organization_id, "disabled", csrf_token)


@router.post("/{organization_id}/enable")
def post_enable_organization(
    request: Request,
    organization_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    return _set_status(request, session, organization_id, "active", csrf_token)


def _set_status(
    request: Request,
    session: Session,
    organization_id: int,
    new_status: str,
    csrf_token: Optional[str],
) -> Response:
    current_user, error_response = _require_organization_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        accounts.set_organization_status(
            session, organization_id=organization_id, status=new_status
        )
        session.commit()
    except ValueError:
        session.rollback()
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return RedirectResponse(
        url="/admin/organizations",
        status_code=status.HTTP_303_SEE_OTHER,
    )

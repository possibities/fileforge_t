from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts
from infrastructure.db.models import AppUser

from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import (
    PLATFORM_ADMIN_ROLE,
    has_platform_scope,
    load_current_user_from_request,
    verify_csrf_from_request,
)


router = APIRouter(prefix="/admin/users")


USER_MANAGE_PERMISSION = "user:manage"


def _require_user_manage(request: Request, session: Session):
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        return None, RedirectResponse(
            url="/login", status_code=status.HTTP_303_SEE_OTHER
        )
    if USER_MANAGE_PERMISSION not in current_user.permissions:
        return current_user, Response(status_code=status.HTTP_403_FORBIDDEN)
    return current_user, None


def _can_manage_user(current_user: CurrentUser, target: AppUser) -> bool:
    if has_platform_scope(current_user):
        return True
    return (
        current_user.organization_id is not None
        and target.organization_id == current_user.organization_id
    )


def _visible_users(current_user: CurrentUser, users: list[accounts.UserRow]):
    if has_platform_scope(current_user):
        return users
    return [
        user
        for user in users
        if current_user.organization_id is not None
        and user.organization_id == current_user.organization_id
    ]


def _available_roles(session: Session, current_user: CurrentUser):
    roles = accounts.list_roles(session)
    if has_platform_scope(current_user):
        return roles
    return [role for role in roles if role.code != PLATFORM_ADMIN_ROLE]


def _new_user_organization_id(current_user: CurrentUser) -> Optional[int]:
    if has_platform_scope(current_user):
        return None
    return current_user.organization_id


@router.get("")
def list_users(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    users = _visible_users(current_user, accounts.list_users(session))
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "users_list.html",
        {"user": current_user, "users": users, "csrf_token": csrf_token},
    )


@router.get("/new")
def get_new_user_form(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    csrf_token = request.cookies.get("fileforge_csrf", "")
    roles = _available_roles(session, current_user)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "user_form.html",
        {
            "user": current_user,
            "csrf_token": csrf_token,
            "roles": roles,
            "error": None,
            "form": {"username": "", "display_name": "", "selected_role": ""},
        },
    )


@router.post("/new")
def post_new_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    display_name: Optional[str] = Form(default=None),
    role_codes: list[str] = Form(default=[]),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    cleaned_role_codes = [code for code in role_codes if code]
    organization_id = _new_user_organization_id(current_user)
    if organization_id is None and not has_platform_scope(current_user):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    if not has_platform_scope(current_user) and PLATFORM_ADMIN_ROLE in cleaned_role_codes:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    try:
        accounts.create_user(
            session,
            username=username,
            password=password,
            display_name=display_name,
            organization_id=organization_id,
            role_codes=cleaned_role_codes,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        roles = _available_roles(session, current_user)
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "user_form.html",
            {
                "user": current_user,
                "csrf_token": csrf_token,
                "roles": roles,
                "error": str(exc),
                "form": {
                    "username": username,
                    "display_name": display_name or "",
                    "selected_role": cleaned_role_codes[0] if cleaned_role_codes else "",
                },
            },
            status_code=status.HTTP_200_OK,
        )

    return RedirectResponse(
        url="/admin/users", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{user_id}/disable")
def post_disable_user(
    request: Request,
    user_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    if current_user is not None and user_id == current_user.id:
        return Response(
            status_code=status.HTTP_400_BAD_REQUEST,
            content="不能禁用自己".encode("utf-8"),
            media_type="text/plain; charset=utf-8",
        )

    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    target.status = "disabled"
    session.commit()
    return RedirectResponse(
        url="/admin/users", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/{user_id}/enable")
def post_enable_user(
    request: Request,
    user_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    target.status = "active"
    session.commit()
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


def _render_user_edit(
    request: Request,
    *,
    current_user: CurrentUser,
    target: AppUser,
    session: Session,
    error: Optional[str],
    form: dict,
) -> Response:
    return request.app.state.templates.TemplateResponse(
        request,
        "user_edit.html",
        {
            "user": current_user,
            "target": target,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "roles": _available_roles(session, current_user),
            "error": error,
            "form": form,
        },
    )


@router.get("/{user_id}/edit")
def get_user_edit_form(
    request: Request,
    user_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    return _render_user_edit(
        request,
        current_user=current_user,
        target=target,
        session=session,
        error=None,
        form={"display_name": target.display_name or "", "selected_role": target.role},
    )


@router.post("/{user_id}/edit")
def post_user_edit(
    request: Request,
    user_id: int,
    display_name: Optional[str] = Form(default=None),
    role_codes: list[str] = Form(default=[]),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    cleaned_role_codes = [code for code in role_codes if code]
    role_code = cleaned_role_codes[0] if cleaned_role_codes else None
    # 非平台管理员不能把用户设成平台管理员
    if role_code == PLATFORM_ADMIN_ROLE and not has_platform_scope(current_user):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        accounts.update_user(
            session,
            user_id=user_id,
            display_name=display_name or "",
            role_code=role_code,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        return _render_user_edit(
            request,
            current_user=current_user,
            target=target,
            session=session,
            error=str(exc),
            form={
                "display_name": (display_name or "").strip(),
                "selected_role": role_code or "",
            },
        )

    return RedirectResponse(url="/admin/users", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/{user_id}/reset-password")
def get_reset_password_form(
    request: Request,
    user_id: int,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    csrf_token = request.cookies.get("fileforge_csrf", "")
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "user_reset_password.html",
        {
            "user": current_user,
            "target": target,
            "csrf_token": csrf_token,
            "error": None,
        },
    )


@router.post("/{user_id}/reset-password")
def post_reset_password(
    request: Request,
    user_id: int,
    new_password: str = Form(...),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_user_manage(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    target = session.get(AppUser, user_id)
    if target is None or not _can_manage_user(current_user, target):
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    try:
        accounts.reset_password(
            session,
            username=target.username,
            new_password=new_password,
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "user_reset_password.html",
            {
                "user": current_user,
                "target": target,
                "csrf_token": csrf_token,
                "error": str(exc),
            },
            status_code=status.HTTP_200_OK,
        )

    return RedirectResponse(
        url="/admin/users", status_code=status.HTTP_303_SEE_OTHER
    )

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import accounts
from web_admin import auth as auth_service
from web_admin.db import get_session
from web_admin.routes import (
    load_current_user_from_request,
    verify_csrf_from_request,
)


CSRF_COOKIE_NAME = "fileforge_csrf"


router = APIRouter()


def _set_session_cookies(
    response: Response,
    *,
    session_cookie_name: str,
    session_token: str,
    csrf_token: str,
    ttl_seconds: int,
    secure: bool,
) -> None:
    response.set_cookie(
        key=session_cookie_name,
        value=session_token,
        max_age=ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=ttl_seconds,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(
    response: Response,
    *,
    session_cookie_name: str,
) -> None:
    response.delete_cookie(session_cookie_name, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


@router.get("/login")
def get_login(request: Request) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "username": ""},
    )


@router.post("/login")
def post_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
) -> Response:
    settings = request.app.state.settings
    tokens = auth_service.login_user(
        session,
        username=username,
        password=password,
        ttl_seconds=settings.session_ttl_seconds,
    )
    if tokens is None:
        session.rollback()
        templates = request.app.state.templates
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "用户名或密码错误",
                "username": username,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    session.commit()
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    _set_session_cookies(
        response,
        session_cookie_name=settings.session_cookie_name,
        session_token=tokens.session_token,
        csrf_token=tokens.csrf_token,
        ttl_seconds=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
    )
    return response


@router.post("/logout")
def post_logout(
    request: Request,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    settings = request.app.state.settings
    session_token = request.cookies.get(settings.session_cookie_name)

    if not session_token:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    if settings.csrf_enabled:
        if not csrf_token:
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        if not auth_service.verify_csrf_token(
            session,
            session_token=session_token,
            csrf_token=csrf_token,
        ):
            return Response(status_code=status.HTTP_403_FORBIDDEN)

    auth_service.logout_session(session, session_token=session_token)
    session.commit()

    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    _clear_session_cookies(response, session_cookie_name=settings.session_cookie_name)
    return response


SELF_UPDATE_PERMISSION = "account:self_update"


def _render_change_password(
    request: Request,
    *,
    current_user,
    error: Optional[str],
    notice: Optional[str],
) -> Response:
    return request.app.state.templates.TemplateResponse(
        request,
        "account_password.html",
        {
            "user": current_user,
            "csrf_token": request.cookies.get(CSRF_COOKIE_NAME, ""),
            "error": error,
            "notice": notice,
        },
    )


@router.get("/account/password")
def get_change_password(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    current_user = load_current_user_from_request(request, session)
    session.commit()
    if current_user is None:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if SELF_UPDATE_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    return _render_change_password(request, current_user=current_user, error=None, notice=None)


@router.post("/account/password")
def post_change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    current_user = load_current_user_from_request(request, session)
    if current_user is None:
        session.rollback()
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if SELF_UPDATE_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    error: Optional[str] = None
    if new_password != confirm_password:
        error = "两次输入的新密码不一致"
    elif len(new_password) < accounts.MIN_PASSWORD_LENGTH:
        error = f"新密码至少 {accounts.MIN_PASSWORD_LENGTH} 字符"
    elif accounts.authenticate_user(
        session, username=current_user.username, password=current_password
    ) is None:
        error = "当前密码不正确"

    if error is not None:
        session.rollback()
        return _render_change_password(
            request, current_user=current_user, error=error, notice=None
        )

    try:
        accounts.reset_password(
            session, username=current_user.username, new_password=new_password
        )
        session.commit()
    except ValueError as exc:
        session.rollback()
        return _render_change_password(
            request, current_user=current_user, error=str(exc), notice=None
        )

    return _render_change_password(
        request, current_user=current_user, error=None, notice="密码已更新。"
    )

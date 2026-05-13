from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from web_admin import auth as auth_service
from web_admin.db import get_session


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

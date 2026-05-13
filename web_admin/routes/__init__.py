"""Web admin HTTP routes."""

from __future__ import annotations

from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from web_admin import auth as auth_service
from web_admin.auth import CurrentUser


def load_current_user_from_request(
    request: Request,
    session: Session,
) -> Optional[CurrentUser]:
    settings = request.app.state.settings
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    user = auth_service.load_current_user(session, session_token=token)
    session.commit()
    return user


def verify_csrf_from_request(
    request: Request,
    session: Session,
    csrf_token: Optional[str],
) -> bool:
    settings = request.app.state.settings
    if not settings.csrf_enabled:
        return True
    if not csrf_token:
        return False
    session_token = request.cookies.get(settings.session_cookie_name)
    if not session_token:
        return False
    return auth_service.verify_csrf_token(
        session,
        session_token=session_token,
        csrf_token=csrf_token,
    )

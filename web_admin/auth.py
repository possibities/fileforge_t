from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db import accounts
from infrastructure.db.models import (
    AppUser,
    Organization,
    WebSession,
)

from .security import CSRF_TOKEN_BYTES, generate_token, hash_token, verify_token_hash


DEFAULT_SESSION_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class SessionTokens:
    session_token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: Optional[str]
    organization_id: Optional[int]
    roles: list[str]
    permissions: list[str]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _role_codes(session: Session, user_id: int) -> list[str]:
    user = session.get(AppUser, user_id)
    if user is None:
        return []
    return [user.role]


def _permission_codes(session: Session, user_id: int) -> list[str]:
    user = session.get(AppUser, user_id)
    if user is None:
        return []
    role_def = accounts.BUILTIN_ROLES.get(user.role)
    if role_def is None:
        return []
    return sorted(role_def[1])


def _current_user_from_app_user(session: Session, user: AppUser) -> CurrentUser:
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        organization_id=user.organization_id,
        roles=_role_codes(session, user.id),
        permissions=_permission_codes(session, user.id),
    )


def create_session(
    session: Session,
    *,
    user: AppUser,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    now: Optional[datetime] = None,
) -> SessionTokens:
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")

    now = _as_aware_utc(now or _utcnow())
    expires_at = now + timedelta(seconds=ttl_seconds)
    session_token = generate_token()
    csrf_token = generate_token(num_bytes=CSRF_TOKEN_BYTES)
    session.add(
        WebSession(
            user_id=user.id,
            token_hash=hash_token(session_token),
            csrf_token_hash=hash_token(csrf_token),
            expires_at=expires_at,
            last_seen_at=now,
        )
    )
    session.flush()
    return SessionTokens(
        session_token=session_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def login_user(
    session: Session,
    *,
    username: str,
    password: str,
    ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
    now: Optional[datetime] = None,
) -> Optional[SessionTokens]:
    user = accounts.authenticate_user(session, username=username, password=password)
    if user is None:
        return None
    return create_session(session, user=user, ttl_seconds=ttl_seconds, now=now)


def load_current_user(
    session: Session,
    *,
    session_token: str,
    now: Optional[datetime] = None,
) -> Optional[CurrentUser]:
    now = _as_aware_utc(now or _utcnow())
    web_session = session.scalar(
        select(WebSession).where(WebSession.token_hash == hash_token(session_token))
    )
    if web_session is None or web_session.revoked_at is not None:
        return None
    if _as_aware_utc(web_session.expires_at) <= now:
        return None

    user = session.scalar(select(AppUser).where(AppUser.id == web_session.user_id))
    if user is None or user.status != "active":
        return None
    if user.organization_id is not None:
        organization = session.scalar(
            select(Organization).where(Organization.id == user.organization_id)
        )
        if organization is None or organization.status != "active":
            return None

    web_session.last_seen_at = now
    return _current_user_from_app_user(session, user)


def verify_csrf_token(
    session: Session,
    *,
    session_token: str,
    csrf_token: str,
    now: Optional[datetime] = None,
) -> bool:
    now = _as_aware_utc(now or _utcnow())
    web_session = session.scalar(
        select(WebSession).where(WebSession.token_hash == hash_token(session_token))
    )
    # 会话必须存在、未吊销、未过期：陈旧/已登出的会话不应通过 CSRF 校验。
    if web_session is None or web_session.revoked_at is not None:
        return False
    if _as_aware_utc(web_session.expires_at) <= now:
        return False
    # 常量时间比较，避免 csrf hash 比较产生计时旁路。
    return verify_token_hash(csrf_token, web_session.csrf_token_hash)


def logout_session(
    session: Session,
    *,
    session_token: str,
    now: Optional[datetime] = None,
) -> bool:
    web_session = session.scalar(
        select(WebSession).where(WebSession.token_hash == hash_token(session_token))
    )
    if web_session is None or web_session.revoked_at is not None:
        return False
    web_session.revoked_at = _as_aware_utc(now or _utcnow())
    return True


def require_permission(current_user: CurrentUser, permission_code: str) -> None:
    if permission_code not in current_user.permissions:
        raise PermissionError(f"missing permission: {permission_code}")

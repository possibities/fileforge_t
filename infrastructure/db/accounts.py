"""人员/用户管理服务。

本模块只依赖调用方传入的 SQLAlchemy Session,不创建 engine、不 commit。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AppUser,
    Organization,
    ORGANIZATION_STATUS,
    Permission,
    Role,
    RolePermission,
    UserRole,
)


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 390000
MIN_PASSWORD_LENGTH = 12


BUILTIN_PERMISSIONS: dict[str, str] = {
    "organization:manage": "管理单位",
    "project:manage": "管理项目",
    "project:operate": "操作项目",
    "archive:view": "查看档案",
    "archive:correct": "校对或修正 AI 结果",
    "archive:export": "导出结果",
    "batch:manage": "管理批次",
    "user:manage": "管理用户和角色",
    "audit:view": "查看审计日志",
    "account:self_update": "修改个人密码",
}


BUILTIN_ROLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "platform_admin": (
        "平台管理员",
        tuple(BUILTIN_PERMISSIONS),
    ),
    "org_admin": (
        "单位管理员",
        (
            "project:manage",
            "project:operate",
            "archive:view",
            "archive:correct",
            "archive:export",
            "batch:manage",
            "user:manage",
            "audit:view",
            "account:self_update",
        ),
    ),
    "org_operator": (
        "单位操作员",
        (
            "project:operate",
            "archive:view",
            "archive:correct",
            "archive:export",
            "batch:manage",
            "account:self_update",
        ),
    ),
}


@dataclass(frozen=True)
class UserRow:
    id: int
    username: str
    display_name: Optional[str]
    status: str
    organization_id: Optional[int]
    organization_name: Optional[str]
    roles: list[str]
    created_at: datetime


@dataclass(frozen=True)
class OrganizationRow:
    id: int
    name: str
    status: str
    created_at: datetime
    updated_at: datetime


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode((text + padding).encode("ascii"))


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return (
        f"{PASSWORD_SCHEME}${PASSWORD_ITERATIONS}$"
        f"{_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iterations_text, salt_text, digest_text = stored_hash.split("$", 3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        salt = _b64decode(salt_text)
        expected = _b64decode(digest_text)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)


def ensure_builtin_roles(session: Session) -> None:
    permissions: dict[str, Permission] = {}
    for code, description in BUILTIN_PERMISSIONS.items():
        permission = session.scalar(select(Permission).where(Permission.code == code))
        if permission is None:
            permission = Permission(code=code, description=description)
            session.add(permission)
            session.flush()
        permissions[code] = permission

    for code, (name, permission_codes) in BUILTIN_ROLES.items():
        role = session.scalar(select(Role).where(Role.code == code))
        if role is None:
            role = Role(code=code, name=name)
            session.add(role)
            session.flush()
        else:
            role.name = name
        existing = {
            row.permission_id
            for row in session.scalars(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        }
        for permission_code in permission_codes:
            permission_id = permissions[permission_code].id
            if permission_id not in existing:
                session.add(
                    RolePermission(role_id=role.id, permission_id=permission_id)
                )


def list_roles(session: Session) -> list[Role]:
    return list(session.scalars(select(Role).order_by(Role.code.asc())).all())


def create_organization(session: Session, *, name: str) -> Organization:
    if not name.strip():
        raise ValueError("organization name is required")
    existing = session.scalar(select(Organization).where(Organization.name == name))
    if existing is not None:
        raise ValueError(f"organization already exists: {name}")
    org = Organization(name=name)
    session.add(org)
    session.flush()
    return org


def list_organizations(
    session: Session,
    *,
    status_filter: Optional[Iterable[str]] = None,
) -> list[OrganizationRow]:
    """按 name 升序列出单位;status_filter=None 返回全部。"""
    stmt = select(Organization).order_by(Organization.name)
    if status_filter:
        stmt = stmt.where(Organization.status.in_(list(status_filter)))
    rows = session.scalars(stmt).all()
    return [
        OrganizationRow(
            id=o.id,
            name=o.name,
            status=o.status,
            created_at=o.created_at,
            updated_at=o.updated_at,
        )
        for o in rows
    ]


def set_organization_status(
    session: Session,
    *,
    organization_id: int,
    status: str,
) -> None:
    """切换单位 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
    if status not in ORGANIZATION_STATUS:
        raise ValueError(
            f"status 必须为 {ORGANIZATION_STATUS} 之一,实际为 {status}"
        )
    org = session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"organization 不存在: {organization_id}")
    org.status = status


def _get_role_by_code(session: Session, code: str) -> Role:
    role = session.scalar(select(Role).where(Role.code == code))
    if role is None:
        raise ValueError(f"unknown role: {code}")
    return role


def _get_user_by_username(session: Session, username: str) -> Optional[AppUser]:
    return session.scalar(select(AppUser).where(AppUser.username == username))


def create_user(
    session: Session,
    *,
    username: str,
    password: str,
    display_name: Optional[str] = None,
    organization_id: Optional[int] = None,
    role_codes: Optional[list[str]] = None,
) -> AppUser:
    username = username.strip()
    if not username:
        raise ValueError("username is required")
    if _get_user_by_username(session, username) is not None:
        raise ValueError(f"username already exists: {username}")

    roles = [_get_role_by_code(session, code) for code in (role_codes or [])]
    user = AppUser(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        organization_id=organization_id,
        status="active",
    )
    session.add(user)
    session.flush()
    for role in roles:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    session.flush()
    return user


def _role_codes_for_user(session: Session, user_id: int) -> list[str]:
    rows = session.execute(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.code.asc())
    ).all()
    return [row[0] for row in rows]


def list_users(session: Session) -> list[UserRow]:
    users = session.scalars(select(AppUser).order_by(AppUser.username.asc())).all()
    result: list[UserRow] = []
    for user in users:
        result.append(
            UserRow(
                id=user.id,
                username=user.username,
                display_name=user.display_name,
                status=user.status,
                organization_id=user.organization_id,
                organization_name=user.organization.name if user.organization else None,
                roles=_role_codes_for_user(session, user.id),
                created_at=user.created_at,
            )
        )
    return result


def authenticate_user(
    session: Session,
    *,
    username: str,
    password: str,
) -> Optional[AppUser]:
    user = _get_user_by_username(session, username)
    if user is None or user.status != "active":
        return None
    if user.organization is not None and user.organization.status != "active":
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = datetime.now(timezone.utc)
    return user


def disable_user(session: Session, *, username: str) -> AppUser:
    user = _get_user_by_username(session, username)
    if user is None:
        raise ValueError(f"user not found: {username}")
    user.status = "disabled"
    return user


def reset_password(session: Session, *, username: str, new_password: str) -> AppUser:
    user = _get_user_by_username(session, username)
    if user is None:
        raise ValueError(f"user not found: {username}")
    user.password_hash = hash_password(new_password)
    return user


__all__ = [
    "BUILTIN_PERMISSIONS",
    "BUILTIN_ROLES",
    "MIN_PASSWORD_LENGTH",
    "UserRow",
    "OrganizationRow",
    "hash_password",
    "verify_password",
    "ensure_builtin_roles",
    "list_roles",
    "create_organization",
    "list_organizations",
    "set_organization_status",
    "create_user",
    "list_users",
    "authenticate_user",
    "disable_user",
    "reset_password",
]

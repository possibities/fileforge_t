"""项目实体的写侧服务。

Web 后台与 CLI 共用;函数本身不 commit,事务边界由调用方控制。
本模块只读 infrastructure.db.models 中已存在的 ORM,不引入新表。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Organization,
    PROJECT_STATUS,
    Project,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectRow:
    id: int
    project_key: str
    project_name: Optional[str]
    description: Optional[str]
    status: str
    organization_id: Optional[int]
    organization_name: Optional[str]
    created_at: datetime
    updated_at: datetime


def create_project(
    session: Session,
    *,
    project_key: str,
    organization_id: int,
    project_name: Optional[str] = None,
    description: Optional[str] = None,
) -> Project:
    """新建 active 项目。不 commit。

    - project_key 空 / 重复 → ValueError
    - organization_id 不存在 / 单位 disabled → ValueError
    """
    key = (project_key or "").strip()
    if not key:
        raise ValueError("project_key 不能为空")

    existing = session.scalar(select(Project).where(Project.project_key == key))
    if existing is not None:
        raise ValueError(f"project_key 已存在: {key}")

    org = session.get(Organization, organization_id)
    if org is None:
        raise ValueError(f"organization 不存在: {organization_id}")
    if org.status != "active":
        raise ValueError(
            f"organization 状态为 {org.status} (非 active),不能新建项目"
        )

    project = Project(
        project_key=key,
        project_name=(project_name or "").strip() or None,
        description=(description or "").strip() or None,
        organization_id=organization_id,
        status="active",
    )
    session.add(project)
    session.flush()
    return project


def list_projects(
    session: Session,
    *,
    organization_id: Optional[int] = None,
    status_filter: Optional[Iterable[str]] = None,
) -> list[ProjectRow]:
    """按 created_at DESC 列出。

    organization_id=None 不过滤;非 platform_admin 由上层传自己的 org_id。
    organization_name 通过 LEFT JOIN organizations 得到。
    """
    stmt = (
        select(Project, Organization.name)
        .outerjoin(Organization, Project.organization_id == Organization.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    )
    if organization_id is not None:
        stmt = stmt.where(Project.organization_id == organization_id)
    if status_filter:
        stmt = stmt.where(Project.status.in_(list(status_filter)))

    rows = session.execute(stmt).all()
    return [
        ProjectRow(
            id=p.id,
            project_key=p.project_key,
            project_name=p.project_name,
            description=p.description,
            status=p.status,
            organization_id=p.organization_id,
            organization_name=org_name,
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p, org_name in rows
    ]


def set_project_status(
    session: Session,
    *,
    project_id: int,
    status: str,
) -> None:
    """切换项目 status。不 commit。非枚举值或 id 不存在 → ValueError。"""
    if status not in PROJECT_STATUS:
        raise ValueError(f"status 必须为 {PROJECT_STATUS} 之一,实际为 {status}")
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project 不存在: {project_id}")
    project.status = status


__all__ = [
    "ProjectRow",
    "create_project",
    "list_projects",
    "set_project_status",
]

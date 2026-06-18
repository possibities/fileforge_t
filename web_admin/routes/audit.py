"""修订记录与审计路由:单档案修订记录、单档案审计记录(保留但详情页不再入口),
以及全局审计页 `/admin/audit`。拆分自 `archives.py`。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from infrastructure.db import queries
from infrastructure.db.models import ArchiveRecord
from web_admin.db import get_session
from web_admin.routes.archive_common import *  # noqa: F401,F403 (共享依赖,见 __all__)


router = APIRouter()


@router.get("/archives/{archive_id}/revisions")
def list_archive_revisions(
    request: Request,
    archive_id: int,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
        result = queries.list_revisions(
            session,
            archive_id=archive_id,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "revisions_list.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "result": result,
        },
    )


@router.get("/archives/{archive_id}/audit")
def list_archive_audit_logs(
    request: Request,
    archive_id: int,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if AUDIT_VIEW_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    access = _can_access_archive(session, current_user, archive)
    if access is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    batch, project = access

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
        result = queries.list_audit_logs(
            session,
            target_type="archive",
            target_id=archive_id,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "audit_list.html",
        {
            "user": current_user,
            "project": project,
            "batch": batch,
            "archive": archive,
            "result": result,
        },
    )


@router.get("/admin/audit")
def global_audit_log(
    request: Request,
    action: Optional[str] = None,
    page: Optional[str] = None,
    page_size: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    """全局审计记录:跨档案/批次/项目的操作留痕,按单位隔离,可按动作筛选。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if AUDIT_VIEW_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        page_num = _parse_int_query(page, name="page", default=1)
        page_size_num = _parse_int_query(page_size, name="page_size", default=50)
    except ValueError as exc:
        return _bad_request(exc)

    org_id = _scoped_organization_id(current_user)
    clean_action = (action or "").strip() or None
    try:
        result = queries.search_audit_logs(
            session,
            organization_id=org_id,
            action=clean_action,
            page=page_num,
            page_size=page_size_num,
        )
    except ValueError as exc:
        return _bad_request(exc)

    action_choices = queries.audit_action_choices(session, organization_id=org_id)
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "audit_global.html",
        {
            "user": current_user,
            "result": result,
            "action_choices": action_choices,
            "selected_action": clean_action or "",
            "page": page_num,
            "page_size": page_size_num,
        },
    )

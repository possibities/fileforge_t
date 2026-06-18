"""审核路由:审核队列 `/review`、批量标记已审核,以及审核工作台
(`/review/{id}` 浏览、`/save` 保存修正、`/done` 标记并跳下一条)。
审核工作台是元数据的唯一人工编辑入口。拆分自 `archives.py`。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from infrastructure.db import queries
from infrastructure.db.models import ArchiveRecord
from infrastructure.db.repositories import (
    RETENTION_PERIOD_CHOICES,
    ManualCorrectionInput,
    apply_manual_correction,
    record_audit_log,
)
from web_admin.auth import CurrentUser
from web_admin.db import get_session
from web_admin.routes import verify_csrf_from_request
from web_admin.routes.archive_common import *  # noqa: F401,F403 (共享依赖,见 __all__)


router = APIRouter()


@router.get("/review")
def review_queue(
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    """审核队列:列出本权限范围内待人工确认的档案(needs_review 置顶)。"""
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    query = request.query_params
    try:
        page_num = _parse_int_query(query.get("page"), name="page", default=1)
    except ValueError as exc:
        return _bad_request(exc)

    result = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=page_num,
        page_size=50,
    )
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "review_list.html",
        {
            "user": current_user,
            "result": result,
            "page": page_num,
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "prev_url": f"/review?page={page_num - 1}" if page_num > 1 else None,
            "next_url": f"/review?page={page_num + 1}" if result.has_next else None,
        },
    )


@router.post("/review/mark-reviewed")
def post_review_mark_reviewed(
    request: Request,
    archive_id: list[int] = Form(default=[]),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """批量把选中档案标记为已审核(review_status=reviewed),逐条校验组织权限并写审计。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    for aid in archive_id:
        archive = session.get(ArchiveRecord, aid)
        if archive is None:
            continue
        if _can_access_archive(session, current_user, archive) is None:
            continue
        old_status = archive.review_status
        archive.review_status = "reviewed"
        record_audit_log(
            session,
            actor_user_id=current_user.id,
            organization_id=archive.organization_id,
            project_id=archive.project_id,
            action="archive_reviewed",
            target_type="archive",
            target_id=archive.id,
            before_data={"review_status": old_status},
            after_data={"review_status": "reviewed"},
        )
    session.commit()
    return RedirectResponse(url="/review", status_code=status.HTTP_303_SEE_OTHER)


def _render_workstation(
    request: Request,
    *,
    current_user: CurrentUser,
    session: Session,
    archive,
    values: Optional[dict] = None,
    error: Optional[str] = None,
    notice: Optional[str] = None,
) -> Response:
    """渲染审核工作台:左=待审核队列,中=页面图像,右=可改元数据。"""
    queue = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=1,
        page_size=200,
    ).items
    if values is None:
        values = _current_values_from_archive(archive)
    return request.app.state.templates.TemplateResponse(
        request,
        "workstation.html",
        {
            "user": current_user,
            "archive": archive,
            "queue": queue,
            "values": values,
            "retention_choices": list(RETENTION_PERIOD_CHOICES),
            "classification_choices": _CLASSIFICATION_CODE_CHOICES,
            "openness_choices": ["开放", "控制"],
            "csrf_token": request.cookies.get("fileforge_csrf", ""),
            "error": error,
            "notice": notice,
        },
    )


@router.get("/review/{archive_id}")
def review_workstation(
    request: Request,
    archive_id: int,
    notice: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Response:
    current_user, error_response = _require_archive_view(request, session)
    if error_response is not None:
        return error_response
    if ARCHIVE_CORRECT_PERMISSION not in current_user.permissions:
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive_rec = session.get(ArchiveRecord, archive_id)
    if archive_rec is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive_rec) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    detail = queries.get_archive_detail(session, archive_id=archive_id)
    if detail is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    return _render_workstation(
        request,
        current_user=current_user,
        session=session,
        archive=detail,
        notice="已保存修改。" if notice == "saved" else None,
    )


@router.post("/review/{archive_id}/save")
def post_review_save(
    request: Request,
    archive_id: int,
    title: Optional[str] = Form(default=None),
    responsible_party: Optional[str] = Form(default=None),
    classification_code: Optional[str] = Form(default=None),
    retention_period: Optional[str] = Form(default=None),
    openness_status: Optional[str] = Form(default=None),
    archive_year: Optional[str] = Form(default=None),
    document_number: Optional[str] = Form(default=None),
    fonds_unit_name: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """在工作台保存人工修正(题名/责任者/分类号/保管期限/开放状态/年度/文号/立档单位)。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    err: Optional[str] = None
    clean_title, err = _clean_form_field(title, max_len=500, name="题名")
    clean_party = clean_class = clean_retention = clean_reason = None
    if err is None:
        clean_party, err = _clean_form_field(responsible_party, max_len=200, name="责任者")
    if err is None:
        clean_class, err = _clean_form_field(classification_code, max_len=32, name="实体分类号")
    if err is None:
        clean_retention = (retention_period or "").strip()
        if clean_retention not in RETENTION_PERIOD_CHOICES:
            err = f"保管期限必须为 {', '.join(RETENTION_PERIOD_CHOICES)} 之一"
    clean_openness = clean_year = clean_docnum = clean_fonds = None
    if err is None:
        clean_openness = (openness_status or "").strip()
        if clean_openness not in ("开放", "控制"):
            err = "开放状态必须为 开放 或 控制"
    if err is None:
        clean_year, err = _clean_form_field(archive_year, max_len=8, name="归档年度", required=False)
        if err is None and clean_year and not clean_year.isdigit():
            err = "归档年度必须是数字"
    if err is None:
        clean_docnum, err = _clean_form_field(document_number, max_len=128, name="文件编号", required=False)
    if err is None:
        clean_fonds, err = _clean_form_field(fonds_unit_name, max_len=255, name="立档单位名称", required=False)
    if err is None:
        clean_reason, err = _clean_form_field(reason, max_len=500, name="原因", required=False)

    if err is not None:
        detail = queries.get_archive_detail(session, archive_id=archive_id)
        return _render_workstation(
            request,
            current_user=current_user,
            session=session,
            archive=detail,
            values={
                "title": (title or "").strip(),
                "responsible_party": (responsible_party or "").strip(),
                "classification_code": (classification_code or "").strip(),
                "retention_period": (retention_period or "").strip(),
                "openness_status": (openness_status or "").strip(),
                "archive_year": (archive_year or "").strip(),
                "document_number": (document_number or "").strip(),
                "fonds_unit_name": (fonds_unit_name or "").strip(),
                "reason": (reason or "").strip(),
            },
            error=err,
        )

    try:
        apply_manual_correction(
            session,
            archive=archive,
            new_values=ManualCorrectionInput(
                title=clean_title,
                responsible_party=clean_party,
                classification_code=clean_class,
                retention_period=clean_retention,
                classification_name=_CODE_TO_CLASS_NAME.get(clean_class),
                openness_status=clean_openness,
                archive_year=clean_year or "",
                document_number=clean_docnum or "",
                fonds_unit_name=clean_fonds or "",
            ),
            actor_user_id=current_user.id,
            reason=clean_reason or None,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    return RedirectResponse(
        url=f"/review/{archive_id}?notice=saved", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/review/{archive_id}/done")
def post_review_done(
    request: Request,
    archive_id: int,
    csrf_token: Optional[str] = Form(default=None),
    session: Session = Depends(get_session),
) -> Response:
    """标记为已审核并跳到队列中的下一条;队列空则回到审核列表。"""
    current_user, error_response = _require_archive_correct(request, session)
    if error_response is not None:
        return error_response
    if not verify_csrf_from_request(request, session, csrf_token):
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    archive = session.get(ArchiveRecord, archive_id)
    if archive is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if _can_access_archive(session, current_user, archive) is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    old_status = archive.review_status
    archive.review_status = "reviewed"
    record_audit_log(
        session,
        actor_user_id=current_user.id,
        organization_id=archive.organization_id,
        project_id=archive.project_id,
        action="archive_reviewed",
        target_type="archive",
        target_id=archive.id,
        before_data={"review_status": old_status},
        after_data={"review_status": "reviewed"},
    )
    session.commit()

    nxt = queries.verification_queue(
        session,
        organization_id=_scoped_organization_id(current_user),
        page=1,
        page_size=1,
    ).items
    target = f"/review/{nxt[0].id}" if nxt else "/review"
    return RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)

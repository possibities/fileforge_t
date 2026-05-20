from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from web_admin import auth as auth_service
from web_admin.db import get_session
from web_admin.routes import archives as archive_routes
from web_admin.routes import auth as auth_routes
from web_admin.routes import organizations as organizations_routes
from web_admin.routes import users as users_routes
from web_admin.settings import WebAdminSettings


TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"


def _error_message(status_code: int) -> str:
    if status_code == status.HTTP_403_FORBIDDEN:
        return "没有权限访问该页面"
    if status_code == status.HTTP_404_NOT_FOUND:
        return "页面不存在"
    if status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
        return "提交内容不完整或格式不正确"
    return "请求处理失败"


def _render_error(request: Request, status_code: int, message: str | None = None) -> Response:
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status_code,
            "message": message or _error_message(status_code),
            "user": None,
        },
        status_code=status_code,
    )


def create_app(database_url: str | None = None) -> FastAPI:
    settings = WebAdminSettings.from_env(database_url=database_url)
    app = FastAPI(title="FileForge Admin")
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    app.include_router(auth_routes.router)
    app.include_router(users_routes.router)
    app.include_router(organizations_routes.router)
    app.include_router(archive_routes.router)

    @app.exception_handler(StarletteHTTPException)
    def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        if exc.status_code in {
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        }:
            detail = exc.detail if isinstance(exc.detail, str) else None
            return _render_error(request, exc.status_code, detail)
        return _render_error(request, exc.status_code)

    @app.exception_handler(RequestValidationError)
    def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        return _render_error(
            request,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            _error_message(status.HTTP_422_UNPROCESSABLE_ENTITY),
        )

    @app.middleware("http")
    async def html_error_middleware(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            return response
        if response.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
            return _render_error(request, response.status_code)
        return response

    @app.get("/healthz")
    def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    def home(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Response:
        token = request.cookies.get(settings.session_cookie_name)
        user = None
        if token:
            user = auth_service.load_current_user(session, session_token=token)
            session.commit()
        if user is None:
            return RedirectResponse(
                url="/login",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        csrf_token = request.cookies.get(auth_routes.CSRF_COOKIE_NAME, "")
        templates = app.state.templates
        return templates.TemplateResponse(
            request,
            "home.html",
            {"user": user, "csrf_token": csrf_token},
        )

    return app

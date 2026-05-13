from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from web_admin import auth as auth_service
from web_admin.db import get_session
from web_admin.routes import auth as auth_routes
from web_admin.settings import WebAdminSettings


TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(database_url: str | None = None) -> FastAPI:
    settings = WebAdminSettings.from_env(database_url=database_url)
    app = FastAPI(title="FileForge Admin")
    app.state.settings = settings
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app.include_router(auth_routes.router)

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

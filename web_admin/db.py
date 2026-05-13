from __future__ import annotations

from collections.abc import Generator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from infrastructure.db.engine import make_engine, make_session_factory


def make_web_session_factory(database_url: str) -> sessionmaker[Session]:
    return make_session_factory(make_engine(database_url))


def get_session(request: Request) -> Generator[Session, None, None]:
    session_factory = getattr(request.app.state, "session_factory", None)
    if session_factory is None:
        settings = request.app.state.settings
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL 未设置，无法创建 Web 数据库 session")
        session_factory = make_web_session_factory(settings.database_url)
        request.app.state.session_factory = session_factory

    session = session_factory()
    try:
        yield session
    finally:
        session.close()

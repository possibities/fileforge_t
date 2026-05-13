from __future__ import annotations

import os
from dataclasses import dataclass


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


@dataclass(frozen=True)
class WebAdminSettings:
    database_url: str
    session_cookie_name: str = "fileforge_session"
    session_ttl_seconds: int = 8 * 60 * 60
    cookie_secure: bool = False
    csrf_enabled: bool = True

    @classmethod
    def from_env(cls, *, database_url: str | None = None) -> "WebAdminSettings":
        return cls(
            database_url=database_url if database_url is not None else _env_str("DATABASE_URL", ""),
            session_cookie_name=_env_str("WEB_SESSION_COOKIE_NAME", "fileforge_session"),
            session_ttl_seconds=_env_int("WEB_SESSION_TTL_SECONDS", 8 * 60 * 60),
            cookie_secure=_env_bool("WEB_COOKIE_SECURE", False),
            csrf_enabled=_env_bool("WEB_CSRF_ENABLED", True),
        )

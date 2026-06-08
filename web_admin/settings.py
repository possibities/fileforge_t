from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    upload_storage_root: str = ""
    processing_output_root: str = ""
    max_upload_bytes: int = 200 * 1024 * 1024
    max_upload_files: int = 2000

    @classmethod
    def from_env(cls, *, database_url: str | None = None) -> "WebAdminSettings":
        repo_root = Path(__file__).resolve().parent.parent
        return cls(
            database_url=database_url if database_url is not None else _env_str("DATABASE_URL", ""),
            session_cookie_name=_env_str("WEB_SESSION_COOKIE_NAME", "fileforge_session"),
            session_ttl_seconds=_env_int("WEB_SESSION_TTL_SECONDS", 8 * 60 * 60),
            cookie_secure=_env_bool("WEB_COOKIE_SECURE", False),
            csrf_enabled=_env_bool("WEB_CSRF_ENABLED", True),
            upload_storage_root=_env_str(
                "WEB_UPLOAD_STORAGE_ROOT",
                str(repo_root / "input_documents" / "web_uploads"),
            ),
            processing_output_root=_env_str(
                "WEB_PROCESSING_OUTPUT_ROOT",
                str(repo_root / "output_results" / "web_runs"),
            ),
            max_upload_bytes=_env_int("WEB_MAX_UPLOAD_BYTES", 200 * 1024 * 1024),
            max_upload_files=_env_int("WEB_MAX_UPLOAD_FILES", 2000),
        )

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from infrastructure.db.models import UploadedFile
from processors.batch_processor import BatchProcessor


SUPPORTED_IMAGE_EXTS = BatchProcessor.SUPPORTED_FORMATS
ZIP_EXT = ".zip"
COPY_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class UploadIngestResult:
    source_type: str
    file_count: int
    document_count: int
    total_size_bytes: int


def detect_source_type(files: list[UploadFile]) -> str:
    return "zip" if any(_extension(file.filename) == ZIP_EXT for file in files) else "images"


def build_upload_root(storage_root: str, *, project_key: str, now: Optional[datetime] = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return Path(storage_root) / safe_segment(project_key, fallback="project") / timestamp


def safe_segment(value: str, fallback: str = "document") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "").strip(" ._")
    return cleaned[:120] if cleaned else fallback


def ingest_upload_files(
    session: Session,
    *,
    upload_batch_id: int,
    upload_root: Path,
    upload_name: str,
    files: list[UploadFile],
    max_total_bytes: int,
    max_file_count: int,
) -> UploadIngestResult:
    upload_root.mkdir(parents=True, exist_ok=True)
    total_size = 0
    total_files = 0
    image_page_counters: dict[str, int] = {}

    for file in files:
        ext = _extension(file.filename)
        remaining_bytes = max_total_bytes - total_size
        remaining_files = max_file_count - total_files
        if ext == ZIP_EXT:
            added_size, added_files = _extract_zip_upload(
                session,
                upload_batch_id=upload_batch_id,
                upload_root=upload_root,
                file=file,
                max_bytes=remaining_bytes,
                max_files=remaining_files,
            )
        else:
            added_size, added_files = _copy_image_upload(
                session,
                upload_batch_id=upload_batch_id,
                upload_root=upload_root,
                upload_name=upload_name,
                file=file,
                page_counters=image_page_counters,
                max_bytes=remaining_bytes,
            )
        total_size += added_size
        total_files += added_files
        _enforce_limits(
            total_size=total_size,
            file_count=total_files,
            max_total_bytes=max_total_bytes,
            max_file_count=max_file_count,
        )

    rows = session.scalars(
        select(UploadedFile).where(UploadedFile.upload_batch_id == upload_batch_id)
    ).all()
    documents = {row.document_key for row in rows if row.status == "stored"}
    if not rows:
        raise ValueError("未识别到可处理的图片文件")
    return UploadIngestResult(
        source_type=detect_source_type(files),
        file_count=len(rows),
        document_count=len(documents),
        total_size_bytes=total_size,
    )


def _extension(filename: Optional[str]) -> str:
    return Path(filename or "").suffix.lower()


def _is_unsafe_upload_filename(filename: Optional[str]) -> bool:
    raw = (filename or "").replace("\\", "/").strip()
    if not raw:
        return False
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    return (
        raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw) is not None
        or any(part == ".." for part in parts)
    )


def _safe_upload_path_parts(filename: Optional[str]) -> list[str]:
    raw = (filename or "").replace("\\", "/").strip()
    if not raw or _is_unsafe_upload_filename(filename):
        return []
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    return parts


def _document_key_from_upload_parts(parts: list[str], *, fallback: str) -> str:
    if len(parts) >= 3:
        return safe_segment(parts[1], fallback=fallback)
    if len(parts) >= 2:
        return safe_segment(parts[0], fallback=fallback)
    return fallback


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(COPY_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_stream_with_limit(src: BinaryIO, dst: BinaryIO, *, max_bytes: int) -> int:
    if max_bytes < 0:
        raise ValueError("上传文件总大小超过限制")
    written = 0
    while True:
        chunk = src.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
        written += len(chunk)
        if written > max_bytes:
            raise ValueError("上传文件总大小超过限制")
        dst.write(chunk)
    return written


def _enforce_limits(
    *,
    total_size: int,
    file_count: int,
    max_total_bytes: int,
    max_file_count: int,
) -> None:
    if total_size > max_total_bytes:
        raise ValueError("上传文件总大小超过限制")
    if file_count > max_file_count:
        raise ValueError("上传文件数量超过限制")


def _add_uploaded_file(
    session: Session,
    *,
    upload_batch_id: int,
    original_filename: str,
    stored_path: Path,
    mime_type: Optional[str],
    document_key: str,
    page_no: Optional[int],
) -> UploadedFile:
    row = UploadedFile(
        upload_batch_id=upload_batch_id,
        original_filename=original_filename,
        stored_path=str(stored_path),
        file_ext=stored_path.suffix.lower(),
        mime_type=mime_type,
        size_bytes=stored_path.stat().st_size,
        sha256=_sha256_file(stored_path),
        page_no=page_no,
        document_key=document_key,
        status="stored",
    )
    session.add(row)
    session.flush()
    return row


def _copy_image_upload(
    session: Session,
    *,
    upload_batch_id: int,
    upload_root: Path,
    upload_name: str,
    file: UploadFile,
    page_counters: dict[str, int],
    max_bytes: int,
) -> tuple[int, int]:
    if _is_unsafe_upload_filename(file.filename):
        return 0, 0

    fallback_document_key = safe_segment(upload_name, fallback=f"upload_{upload_batch_id}")
    parts = _safe_upload_path_parts(file.filename)
    filename = parts[-1] if parts else Path(file.filename or "").name
    if not filename:
        filename = "page"
    if _extension(filename) not in SUPPORTED_IMAGE_EXTS:
        return 0, 0

    document_key = _document_key_from_upload_parts(
        parts,
        fallback=fallback_document_key,
    )
    page_counters[document_key] = page_counters.get(document_key, 0) + 1
    page_no = page_counters[document_key]
    document_dir = upload_root / document_key
    document_dir.mkdir(parents=True, exist_ok=True)
    stored = document_dir / f"{page_no:04d}_{safe_segment(filename, fallback='page')}"
    with stored.open("wb") as out:
        file.file.seek(0)
        copied = _copy_stream_with_limit(file.file, out, max_bytes=max_bytes)
    _add_uploaded_file(
        session,
        upload_batch_id=upload_batch_id,
        original_filename=file.filename or filename,
        stored_path=stored,
        mime_type=file.content_type,
        document_key=document_key,
        page_no=page_no,
    )
    return copied, 1


def _extract_zip_upload(
    session: Session,
    *,
    upload_batch_id: int,
    upload_root: Path,
    file: UploadFile,
    max_bytes: int,
    max_files: int,
) -> tuple[int, int]:
    total_size = 0
    file_count = 0
    zip_stem = safe_segment(Path(file.filename or "upload").stem, fallback=f"upload_{upload_batch_id}")
    file.file.seek(0)
    with zipfile.ZipFile(file.file) as zf:
        page_counters: dict[str, int] = {}
        for info in zf.infolist():
            if info.is_dir():
                continue
            raw_path = Path(info.filename)
            if raw_path.is_absolute() or ".." in raw_path.parts:
                continue
            if raw_path.suffix.lower() not in SUPPORTED_IMAGE_EXTS:
                continue

            _enforce_limits(
                total_size=total_size + int(info.file_size),
                file_count=file_count + 1,
                max_total_bytes=max_bytes,
                max_file_count=max_files,
            )

            document_key = _document_key_from_zip_path(raw_path, fallback=zip_stem)
            page_counters[document_key] = page_counters.get(document_key, 0) + 1
            page_no = page_counters[document_key]
            document_dir = upload_root / document_key
            document_dir.mkdir(parents=True, exist_ok=True)
            stored = document_dir / f"{page_no:04d}_{safe_segment(raw_path.name, fallback='page')}"

            with zf.open(info) as src, stored.open("wb") as out:
                copied = _copy_stream_with_limit(
                    src,
                    out,
                    max_bytes=max_bytes - total_size,
                )
            total_size += copied
            file_count += 1
            _add_uploaded_file(
                session,
                upload_batch_id=upload_batch_id,
                original_filename=info.filename,
                stored_path=stored,
                mime_type="application/octet-stream",
                document_key=document_key,
                page_no=page_no,
            )
    return total_size, file_count


def _document_key_from_zip_path(raw_path: Path, *, fallback: str) -> str:
    parts = [part for part in raw_path.parts if part not in {"", "."}]
    if len(parts) > 1:
        return safe_segment(parts[0], fallback=fallback)
    return fallback


def remove_upload_root(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)

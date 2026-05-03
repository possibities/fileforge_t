#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch processing for archive classification."""

import json
import logging
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.sequence_generator import SequenceGenerator
try:
    from jsonschema import Draft202012Validator
except Exception:  # pragma: no cover - optional dependency in runtime
    Draft202012Validator = None

logger = logging.getLogger(__name__)


class BatchProcessor:
    SUPPORTED_FORMATS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}
    SUMMARY_SCHEMA_VERSION = "1.0.0"
    SUMMARY_SCHEMA_MAJOR = 1
    SUMMARY_SCHEMA_REF = "config/batch_summary.schema.json"
    SUMMARY_CHANGELOG_REF = "config/batch_summary.schema.changelog.md"
    _SUMMARY_SCHEMA_VALIDATOR = None

    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_ERROR = "error"

    ERROR_NO_IMAGES = "NO_IMAGES"
    ERROR_EMPTY_METADATA = "EMPTY_METADATA"
    ERROR_PROCESS_EXCEPTION = "PROCESS_EXCEPTION"

    def __init__(self, classifier, recorder=None):
        self.classifier = classifier
        self.recorder = recorder

    def scan_directory_structure(
        self,
        root_directory: str,
        max_depth: int = 2,
    ) -> Dict[str, List[str]]:
        archive_dict: Dict[str, List[str]] = {}
        root_path = Path(root_directory)

        if not root_path.exists():
            logger.error("[Error] Directory does not exist: %s", root_directory)
            return {}

        if not root_path.is_dir():
            logger.error("[Error] Path is not a directory: %s", root_directory)
            return {}

        def collect_images(folder: Path) -> List[str]:
            try:
                entries = list(folder.iterdir())
            except OSError as exc:
                logger.error("[Error] Failed to read directory %s: %s", folder, exc)
                return []

            return sorted(
                str(file_path)
                for file_path in entries
                if file_path.is_file() and file_path.suffix.lower() in self.SUPPORTED_FORMATS
            )

        def scan_folder(folder: Path, prefix: str = "", depth: int = 0) -> None:
            if depth >= max_depth:
                return

            try:
                entries = list(folder.iterdir())
            except OSError as exc:
                logger.error("[Error] Failed to scan directory %s: %s", folder, exc)
                return

            subdirs = sorted(
                d for d in entries if d.is_dir() and not d.name.startswith(".")
            )

            for subdir in subdirs:
                images = collect_images(subdir)
                if images:
                    key = f"{prefix}{subdir.name}" if prefix else subdir.name
                    archive_dict[key] = images
                    continue

                next_prefix = f"{prefix}{subdir.name}/" if prefix else f"{subdir.name}/"
                scan_folder(subdir, prefix=next_prefix, depth=depth + 1)

        scan_folder(root_path)
        return archive_dict

    def batch_process_archives(
        self,
        archive_dict: Dict[str, List[str]],
        output_dir: Optional[str] = None,
    ) -> List[Dict]:
        results: List[Dict] = []
        success_count = 0
        fail_count = 0

        total_archives = len(archive_dict)
        total_pages = sum(len(image_paths) for image_paths in archive_dict.values())

        output_path = None
        if output_dir:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

        if self.recorder is not None:
            try:
                self.recorder.on_batch_start(
                    total_archives=total_archives,
                    total_pages=total_pages,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("[Recorder] on_batch_start 异常（忽略，继续走文件路径）: %s", exc)

        sequence_generator = (
            self.recorder.allocator if self.recorder is not None else SequenceGenerator()
        )

        for idx, (archive_name, image_paths) in enumerate(archive_dict.items(), 1):
            archive_key = archive_name
            ctx = None

            if self.recorder is not None:
                try:
                    if self.recorder.should_skip(archive_key):
                        cached = self.recorder.load_previous_success(archive_key)
                        if cached is not None:
                            results.append(cached)
                            success_count += 1
                            if output_path:
                                safe_name = self._sanitize_filename(archive_name)
                                item_name = f"{idx:04d}_{safe_name}_result.json"
                                self._save_json(cached, output_path / item_name)
                            continue
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("[Recorder] skip-success 检查异常: %s", exc)

            source_folder, created_time = self._resolve_source_info(image_paths)
            base_result = {
                "archive_name": archive_name,
                "source_folder": source_folder,
                "page_count": len(image_paths),
                "image_files": image_paths,
                "image_names": [Path(path).name for path in image_paths],
                "processed_time": created_time,
            }

            if self.recorder is not None and image_paths:
                try:
                    ctx = self.recorder.on_archive_start(
                        archive_name=archive_name,
                        archive_key=archive_key,
                        source_folder=source_folder,
                        image_paths=image_paths,
                        processed_time=created_time,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("[Recorder] on_archive_start 异常: %s", exc)

            if not image_paths:
                result = self._build_result(
                    base_result=base_result,
                    status=self.STATUS_FAILED,
                    error_code=self.ERROR_NO_IMAGES,
                    error_message="No image files were found in archive folder.",
                )
            else:
                try:
                    metadata = self.classifier.process_multi_page_document(
                        archive_name,
                        image_paths,
                    )
                    if metadata:
                        metadata["页数"] = len(image_paths)
                        metadata["source_folder"] = source_folder
                        metadata["processed_time"] = created_time
                        metadata = sequence_generator.assign(metadata)

                        result = self._build_result(
                            base_result=base_result,
                            status=self.STATUS_SUCCESS,
                            metadata=metadata,
                        )
                    else:
                        result = self._build_result(
                            base_result=base_result,
                            status=self.STATUS_FAILED,
                            error_code=self.ERROR_EMPTY_METADATA,
                            error_message="Classifier returned empty metadata.",
                        )
                except Exception as exc:  # pragma: no cover - traceback path is tested by behavior
                    result = self._build_result(
                        base_result=base_result,
                        status=self.STATUS_ERROR,
                        error_code=self.ERROR_PROCESS_EXCEPTION,
                        error_message=str(exc),
                        traceback_text=traceback.format_exc(),
                    )

            if result["status"] == self.STATUS_SUCCESS:
                success_count += 1
            else:
                fail_count += 1

            results.append(result)

            result_filename = None
            if output_path:
                safe_name = self._sanitize_filename(archive_name)
                item_name = f"{idx:04d}_{safe_name}_result.json"
                self._save_json(result, output_path / item_name)
                result_filename = item_name

            if self.recorder is not None:
                try:
                    self.recorder.on_archive_complete(
                        ctx,
                        status=result["status"],
                        metadata=result.get("metadata"),
                        error_code=result.get("error_code"),
                        error_message=result.get("error_message"),
                        traceback_text=result.get("traceback"),
                        result_filename=result_filename,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.exception("[Recorder] on_archive_complete 异常: %s", exc)

        failure_breakdown = self._build_failure_breakdown(results)
        if output_path:
            summary_data = {
                "summary_schema_version": self.SUMMARY_SCHEMA_VERSION,
                "summary_schema_ref": self.SUMMARY_SCHEMA_REF,
                "summary_changelog_ref": self.SUMMARY_CHANGELOG_REF,
                "summary_contract": self._build_summary_contract(),
                "batch_time": datetime.now().isoformat(),
                "total_archives": total_archives,
                "total_pages": total_pages,
                "success_count": success_count,
                "fail_count": fail_count,
                "failure_breakdown": failure_breakdown,
                "results": results,
            }
            self._validate_summary_data(summary_data)
            self._save_json(summary_data, output_path / "batch_summary.json")

        success_rate = (success_count / total_archives * 100) if total_archives else 0
        fail_rate = (fail_count / total_archives * 100) if total_archives else 0

        logger.info("\n%s", "=" * 70)
        logger.info("Batch processing completed")
        logger.info("  Total archives: %s", total_archives)
        logger.info("  Success: %s (%.1f%%)", success_count, success_rate)
        logger.info("  Failed:  %s (%.1f%%)", fail_count, fail_rate)
        if fail_count:
            logger.info("  Failure breakdown: %s", failure_breakdown)
        logger.info("  Total images: %s", total_pages)
        logger.info("%s\n", "=" * 70)

        if self.recorder is not None:
            try:
                self.recorder.on_batch_finish(
                    success_count=success_count,
                    fail_count=fail_count,
                    failure_breakdown=failure_breakdown,
                    batch_status="completed",
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("[Recorder] on_batch_finish 异常: %s", exc)

        return results

    def _resolve_source_info(
        self,
        image_paths: List[str],
    ) -> Tuple[Optional[str], Optional[str]]:
        if not image_paths:
            return None, None

        first_image = Path(image_paths[0])
        source_folder = str(first_image.parent)

        try:
            mtime = first_image.stat().st_mtime
        except OSError as exc:
            logger.warning("[Warning] Failed to read file time %s: %s", first_image, exc)
            return source_folder, None

        return source_folder, datetime.fromtimestamp(mtime).isoformat()

    def _save_json(self, data: Dict, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        # Windows 非法: < > : " / \ | ? *；控制符 \x00-\x1f；两端空格和点（Windows 不允许）
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '__', name)
        cleaned = cleaned.strip(' .')
        if not cleaned or re.fullmatch(r'_+', cleaned):
            return "unnamed"
        return cleaned

    @staticmethod
    def _build_result(
        base_result: Dict,
        status: str,
        metadata: Optional[Dict] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        traceback_text: Optional[str] = None,
    ) -> Dict:
        result = {
            **base_result,
            "metadata": metadata,
            "status": status,
            "error_code": error_code,
            "error_message": error_message,
            # Backward compatibility for older consumers.
            "error": error_message,
        }
        if traceback_text:
            result["traceback"] = traceback_text
        return result

    @staticmethod
    def _build_failure_breakdown(results: List[Dict]) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        for result in results:
            if result.get("status") == BatchProcessor.STATUS_SUCCESS:
                continue
            error_code = result.get("error_code") or "UNKNOWN_ERROR"
            breakdown[error_code] = breakdown.get(error_code, 0) + 1
        return breakdown

    @classmethod
    def _get_summary_schema_validator(cls):
        if cls._SUMMARY_SCHEMA_VALIDATOR is False:
            return None
        if cls._SUMMARY_SCHEMA_VALIDATOR is not None:
            return cls._SUMMARY_SCHEMA_VALIDATOR

        schema_path = Path(__file__).resolve().parent.parent / cls.SUMMARY_SCHEMA_REF
        if not schema_path.exists():
            raise RuntimeError(f"Summary schema file not found: {schema_path}")

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if Draft202012Validator is None:
            logger.warning(
                "[Warning] jsonschema is not installed; summary schema validation is skipped."
            )
            cls._SUMMARY_SCHEMA_VALIDATOR = False
            return None

        cls._SUMMARY_SCHEMA_VALIDATOR = Draft202012Validator(schema)
        return cls._SUMMARY_SCHEMA_VALIDATOR

    @classmethod
    def _validate_summary_data(cls, summary_data: Dict) -> None:
        version = summary_data.get("summary_schema_version")
        if not cls._is_supported_summary_version(version):
            raise RuntimeError(
                f"Unsupported summary schema major version: {version!r}. "
                f"Expected major {cls.SUMMARY_SCHEMA_MAJOR}."
            )

        validator = cls._get_summary_schema_validator()
        if validator is None:
            return

        errors = sorted(validator.iter_errors(summary_data), key=lambda err: list(err.path))
        if not errors:
            return

        first = errors[0]
        location = "/".join(str(item) for item in first.path) or "<root>"
        raise RuntimeError(
            f"Generated batch summary does not match schema at '{location}': {first.message}"
        )

    @classmethod
    def _parse_semver(cls, version: str) -> Optional[Tuple[int, int, int]]:
        if not isinstance(version, str):
            return None
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version.strip())
        if not match:
            return None
        return int(match.group(1)), int(match.group(2)), int(match.group(3))

    @classmethod
    def _is_supported_summary_version(cls, version: str) -> bool:
        parsed = cls._parse_semver(version)
        if parsed is None:
            return False
        major, _minor, _patch = parsed
        return major == cls.SUMMARY_SCHEMA_MAJOR

    @classmethod
    def _build_summary_contract(cls) -> Dict[str, object]:
        return {
            "summary_fields": {
                "summary_schema_version": "Version of summary JSON contract.",
                "summary_schema_ref": "Relative path to the JSON schema file.",
                "summary_changelog_ref": "Relative path to schema changelog file.",
                "batch_time": "ISO8601 timestamp when summary was generated.",
                "total_archives": "Number of archives in input batch.",
                "total_pages": "Total number of image pages across archives.",
                "success_count": "Count of archives processed successfully.",
                "fail_count": "Count of archives with failed or error status.",
                "failure_breakdown": "Map of error_code to failure count.",
                "results": "Per-archive processing result list.",
            },
            "result_required_fields": [
                "archive_name",
                "status",
                "metadata",
                "error_code",
                "error_message",
                "error",
                "source_folder",
                "page_count",
                "image_files",
                "image_names",
                "processed_time",
            ],
            "result_field_descriptions": {
                "archive_name": "Archive key/path generated from directory scan.",
                "status": "Processing status: success/failed/error.",
                "metadata": "Extracted metadata object on success, otherwise null.",
                "error_code": "Machine-readable error code for non-success result.",
                "error_message": "Human-readable failure reason.",
                "error": "Backward-compatible alias of error_message.",
                "source_folder": "Original folder path containing archive images.",
                "page_count": "Number of pages in this archive.",
                "image_files": "Absolute/relative image file paths in archive.",
                "image_names": "Image file names in archive.",
                "processed_time": "ISO8601 source file mtime used as processing time.",
            },
            "status_values": [
                cls.STATUS_SUCCESS,
                cls.STATUS_FAILED,
                cls.STATUS_ERROR,
            ],
            "error_codes": [
                cls.ERROR_NO_IMAGES,
                cls.ERROR_EMPTY_METADATA,
                cls.ERROR_PROCESS_EXCEPTION,
                "UNKNOWN_ERROR",
            ],
            "schema_version_policy": {
                "scheme": "semver",
                "compatibility_rule": "Only same major version is guaranteed compatible.",
                "minor_rule": "Minor version increments are backward-compatible additive changes.",
                "patch_rule": "Patch version increments are non-structural fixes or clarifications.",
            },
        }

    def process_directory(
        self,
        directory_path: str,
        output_dir: Optional[str] = None,
    ) -> List[Dict]:
        archive_dict = self.scan_directory_structure(directory_path)
        if not archive_dict:
            logger.warning("[Warning] No archive folders were found under: %s", directory_path)
            return []

        return self.batch_process_archives(archive_dict, output_dir)

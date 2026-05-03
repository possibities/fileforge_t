#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.config import Config
from core.classifier import ArchiveClassifier
from processors.batch_processor import BatchProcessor
from processors.exporter import Exporter

logger = logging.getLogger(__name__)


def _count_by_status(results: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for result in results:
        status = result.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _count_failure_codes(results: List[Dict]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for result in results:
        if result.get("status") == BatchProcessor.STATUS_SUCCESS:
            continue
        code = result.get("error_code") or "UNKNOWN_ERROR"
        counts[code] = counts.get(code, 0) + 1
    return counts


def _build_recorder(input_dir: str, output_dir: str):
    """根据 Config 构造 BatchRecorder；DATABASE_URL 为空时返回 None。

    缺 PROJECT_KEY/BATCH_KEY 或连接失败 → 抛异常,由调用方决定是否退出。
    """
    if not Config.DATABASE_URL:
        return None, None

    if not Config.PROJECT_KEY:
        raise RuntimeError("DATABASE_URL 已设置,但未指定 PROJECT_KEY,启动失败")
    if not Config.BATCH_KEY:
        raise RuntimeError("DATABASE_URL 已设置,但未指定 BATCH_KEY,启动失败")

    from infrastructure.db.engine import make_engine, make_session_factory
    from infrastructure.db.recorder import BatchRecorder

    engine = make_engine(Config.DATABASE_URL)
    session_factory = make_session_factory(engine)
    recorder = BatchRecorder(
        engine=engine,
        session_factory=session_factory,
        project_key=Config.PROJECT_KEY,
        project_name=Config.PROJECT_NAME or None,
        batch_key=Config.BATCH_KEY,
        rerun_policy=Config.DB_RERUN_POLICY,
        summary_schema_version=BatchProcessor.SUMMARY_SCHEMA_VERSION,
        summary_schema_ref=BatchProcessor.SUMMARY_SCHEMA_REF,
        summary_changelog_ref=BatchProcessor.SUMMARY_CHANGELOG_REF,
        input_dir=input_dir,
        output_dir=output_dir,
    )
    return recorder, engine


def main() -> None:
    logger.info("%s", "=" * 70)
    logger.info("Archive Classification System")
    logger.info("%s", "=" * 70)

    logger.info("\n[1/4] Initializing...")
    recorder = None
    db_engine = None
    try:
        Exporter.initialize(Config.EXPORTER_CONFIG_PATH)
        classifier = ArchiveClassifier(
            ocr_lang=Config.OCR_LANG,
            model_name=Config.LLM_MODEL_NAME,
        )
        recorder, db_engine = _build_recorder(Config.INPUT_DIR, Config.OUTPUT_DIR)
        if recorder is not None:
            logger.info(
                "Database recorder enabled: project_key=%s batch_key=%s policy=%s",
                Config.PROJECT_KEY,
                Config.BATCH_KEY,
                Config.DB_RERUN_POLICY,
            )
        batch_processor = BatchProcessor(classifier, recorder=recorder)
    except Exception as exc:
        logger.error("\n[Error] Initialization failed: %s", exc)
        if db_engine is not None:
            from infrastructure.db.engine import dispose_engine
            dispose_engine(db_engine)
        return

    logger.info("Initialization completed")

    logger.info("\n[2/4] Resolving paths...")
    logger.info("Input directory: %s", Config.INPUT_DIR)
    logger.info("Output directory: %s", Config.OUTPUT_DIR)

    input_path = Path(Config.INPUT_DIR)
    output_path = Path(Config.OUTPUT_DIR)

    if not input_path.exists():
        logger.error("\n[Error] Input directory does not exist: %s", input_path)
        return

    output_path.mkdir(parents=True, exist_ok=True)

    logger.info("\n[3/4] Processing archives...")
    results = batch_processor.process_directory(
        directory_path=Config.INPUT_DIR,
        output_dir=Config.OUTPUT_DIR,
    )

    if not results:
        logger.warning("\n[Warning] No archives found or all processing failed")
        logger.warning("  1. Check input directory exists: %s", Config.INPUT_DIR)
        logger.warning("  2. Check directory structure is valid (subfolders/images)")
        logger.warning("  3. Check supported extensions (.jpg/.jpeg/.png/.bmp/.tiff/.tif)")
        return

    logger.info("\n[4/4] Exporting results...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_output = output_path / f"archive_results_{timestamp}.json"
    csv_output = output_path / f"archive_results_{timestamp}.csv"

    try:
        json_written = Exporter.export_to_json(results, str(json_output))
        csv_written = Exporter.export_to_csv(results, str(csv_output))
    except Exception as exc:
        logger.error("\n[Error] Export failed: %s", exc)
        if db_engine is not None:
            from infrastructure.db.engine import dispose_engine
            dispose_engine(db_engine)
        return

    if recorder is not None:
        recorder.record_export(
            export_type="json",
            file_path=str(json_output),
            template_name="default",
            row_count=json_written,
        )
        recorder.record_export(
            export_type="csv",
            file_path=str(csv_output),
            template_name="default",
            row_count=csv_written,
        )

    if json_written == 0:
        logger.warning("[Warning] Export finished but wrote 0 records")
    if json_written != csv_written:
        logger.warning(
            "[Warning] JSON/CSV row count mismatch: json=%s csv=%s",
            json_written,
            csv_written,
        )

    status_counts = _count_by_status(results)
    failure_codes = _count_failure_codes(results)
    total_count = len(results)
    success_count = status_counts.get(BatchProcessor.STATUS_SUCCESS, 0)

    logger.info("\n%s", "=" * 70)
    logger.info("Processing completed")
    logger.info("  JSON summary: %s", json_output)
    logger.info("  CSV summary:  %s", csv_output)
    logger.info("  Per-archive files: %s/*_result.json", Config.OUTPUT_DIR)
    logger.info("\nStatistics:")
    logger.info("  Total archives: %s", total_count)
    logger.info("  Success: %s (%.1f%%)", success_count, success_count / (total_count or 1) * 100)
    logger.info("  Failed: %s", total_count - success_count)
    logger.info("  Exported rows: %s", json_written)
    logger.info("  Status breakdown: %s", status_counts)
    if failure_codes:
        logger.info("  Failure code breakdown: %s", failure_codes)
    logger.info("%s\n", "=" * 70)

    if db_engine is not None:
        from infrastructure.db.engine import dispose_engine
        dispose_engine(db_engine)


if __name__ == "__main__":
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()

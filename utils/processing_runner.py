"""Run an uploaded Web batch from the command line.

This CLI is a small bridge toward a future worker process. It prepares the same
processing batch/jobs as the Web route, then calls the existing upload runner.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from utils._cli_common import add_database_url_arg, resolve_database_url


logger = logging.getLogger("processing_runner")


def _default_output_root() -> str:
    return os.environ.get(
        "WEB_PROCESSING_OUTPUT_ROOT",
        str(Path(__file__).resolve().parents[1] / "output_results" / "web_runs"),
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="processing_runner",
        description="从命令行处理一个已上传的 Web upload_batch",
    )
    parser.add_argument("--upload-batch-id", type=int, required=True, help="upload_batches.id")
    parser.add_argument("--actor-id", type=int, default=None, help="操作者 app_users.id,可选")
    parser.add_argument("--batch-key", default=None, help="处理批次 key;默认自动生成 cli_<upload_id>_<timestamp>")
    parser.add_argument(
        "--output-root",
        default=_default_output_root(),
        help="在线跑批输出根目录,默认 WEB_PROCESSING_OUTPUT_ROOT 或 output_results/web_runs",
    )
    add_database_url_arg(parser)
    return parser.parse_args(argv)


def run(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    database_url = resolve_database_url(args)
    if not database_url:
        logger.error("DATABASE_URL 未设置,且未通过 --database-url 指定")
        return 2

    try:
        from infrastructure.db.engine import (
            check_connectivity,
            dispose_engine,
            make_engine,
            make_session_factory,
        )
        from web_admin.processing import (
            create_upload_processing_batch,
            run_upload_processing_batch,
        )
    except ImportError as exc:
        logger.error("缺少运行依赖: %s", exc)
        return 3

    engine = None
    try:
        engine = make_engine(database_url)
        check_connectivity(engine)
    except Exception as exc:
        logger.error("数据库连接失败: %s", exc)
        if engine is not None:
            dispose_engine(engine)
        return 3

    session_factory = make_session_factory(engine)
    try:
        with session_factory() as session:
            try:
                batch = create_upload_processing_batch(
                    session,
                    upload_batch_id=args.upload_batch_id,
                    output_root=args.output_root,
                    actor_user_id=args.actor_id,
                    batch_key=args.batch_key,
                    batch_key_prefix="cli",
                )
                batch_key = batch.batch_key
                batch_id = batch.id
                session.commit()
            except (LookupError, ValueError) as exc:
                session.rollback()
                logger.error("%s", exc)
                return 4
            except Exception:
                session.rollback()
                raise
    except Exception as exc:
        logger.exception("准备处理批次失败: %s", exc)
        return 9
    finally:
        dispose_engine(engine)

    ok = run_upload_processing_batch(
        database_url=database_url,
        upload_batch_id=args.upload_batch_id,
        batch_key=batch_key,
        output_root=args.output_root,
    )
    if not ok:
        return 9

    print(f"batch_id={batch_id} batch_key={batch_key}")
    return 0


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()

"""命令行触发 force-rerun-rules 的最小可用入口。

用途:
  在 Web 修正 API 上线前,允许人工或脚本对单个档案显式覆盖 final_metadata。
  覆盖会自动写 metadata_revisions(共享 revision_no) + audit_logs。

用法:
  python -m utils.force_rerun_cli \
      --project-key proj_a \
      --batch-key 2026-05-03_run_01 \
      --archive-key folder_a \
      --metadata-file ./new_metadata.json \
      [--reason "schema_v3_realign"] \
      [--actor-id 42]

环境变量:
  DATABASE_URL  必填,与 main.py 同源

退出码:
  0  写入成功(打印 revision_no;无差异时 revision_no=0)
  2  参数缺失/非法
  3  数据库连接失败
  4  archive 不存在
  5  metadata 文件不可读或非合法 JSON
  9  其他异常
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from utils._cli_common import add_database_url_arg, resolve_database_url

logger = logging.getLogger("force_rerun_cli")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="force_rerun_cli",
        description="对单个档案触发 force-rerun-rules,覆盖 final_metadata",
    )
    parser.add_argument("--project-key", required=True, help="项目稳定标识")
    parser.add_argument("--batch-key", required=True, help="批次标识")
    parser.add_argument("--archive-key", required=True, help="档案 key,与 archive_records.archive_key 一致")
    parser.add_argument("--metadata-file", required=True, help="新 metadata 的 JSON 文件路径")
    parser.add_argument("--reason", default="rules_rerun_force", help="审计理由,默认 rules_rerun_force")
    parser.add_argument("--actor-id", type=int, default=None, help="操作者 user id,可选")
    add_database_url_arg(parser)
    return parser.parse_args(argv)


def _load_metadata(path: str) -> dict:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"metadata file not found: {path}")
    text = p.read_text(encoding="utf-8")
    return json.loads(text)


def run(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    database_url = resolve_database_url(args)
    if not database_url:
        logger.error("DATABASE_URL 未设置,且未通过 --database-url 指定")
        return 2

    try:
        new_metadata = _load_metadata(args.metadata_file)
    except FileNotFoundError as exc:
        logger.error("metadata 文件不存在: %s", exc)
        return 5
    except json.JSONDecodeError as exc:
        logger.error("metadata 文件不是合法 JSON: %s", exc)
        return 5

    if not isinstance(new_metadata, dict):
        logger.error("metadata JSON 顶层必须是对象/字典,实际是 %s", type(new_metadata).__name__)
        return 5

    # 延迟 import 数据库依赖,避免在没装 SQLAlchemy 的环境上 import 阶段就崩
    try:
        from sqlalchemy import select
        from infrastructure.db import repositories
        from infrastructure.db.engine import (
            check_connectivity,
            dispose_engine,
            make_engine,
            make_session_factory,
        )
        from infrastructure.db.models import ArchiveRecord, ProcessingBatch, Project
    except ImportError as exc:
        logger.error("缺少数据库依赖: %s。请 pip install -r requirements/db.txt", exc)
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
            archive = session.scalar(
                select(ArchiveRecord)
                .join(ProcessingBatch, ArchiveRecord.batch_id == ProcessingBatch.id)
                .join(Project, ProcessingBatch.project_id == Project.id)
                .where(
                    Project.project_key == args.project_key,
                    ProcessingBatch.batch_key == args.batch_key,
                    ArchiveRecord.archive_key == args.archive_key,
                )
            )
            if archive is None:
                logger.error(
                    "未找到档案: project_key=%s batch_key=%s archive_key=%s",
                    args.project_key,
                    args.batch_key,
                    args.archive_key,
                )
                return 4

            try:
                rev_no = repositories.apply_force_rerun_rules(
                    session,
                    archive=archive,
                    new_metadata=new_metadata,
                    actor_user_id=args.actor_id,
                    reason=args.reason,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        if rev_no == 0:
            print("revision_no=0 (no diff, no-op)")
        else:
            print(f"revision_no={rev_no}")
        return 0
    except Exception as exc:
        logger.exception("force_rerun_rules 失败: %s", exc)
        return 9
    finally:
        dispose_engine(engine)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()

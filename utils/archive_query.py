"""读侧只读查询的命令行入口。

用途:
  在 Web API 上线前,允许人工或自动化通过 CLI 触达 6 个 query 函数。
  输出始终是 JSON;不做表格美化。

用法:
  python -m utils.archive_query batches list   --project-key K [--status running] [--page 1 --page-size 50]
  python -m utils.archive_query batches show   --batch-id ID
  python -m utils.archive_query archives list  --batch-id ID [filter args]
  python -m utils.archive_query archives show  --archive-id ID
  python -m utils.archive_query revisions list --archive-id ID
  python -m utils.archive_query audit list     --target-type archive --target-id ID

环境变量:
  DATABASE_URL  必填,与 main.py 同源

退出码:
  0  成功
  2  参数缺失/非法(含 DATABASE_URL 空、page_size 越界、未知 target_type、subcommand 缺失)
  3  数据库连接失败
  4  资源不存在(get_*_detail 返回 None)
  9  其他未分类异常

设计参考 docs/superpowers/specs/2026-05-04-phase-1c-readside-queries-design.md。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import sys
from typing import Any, Callable, Optional

logger = logging.getLogger("archive_query")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archive_query",
        description="读侧 DB 查询 CLI(JSON 输出)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖 DATABASE_URL 环境变量,通常应通过 env 注入",
    )
    sub = parser.add_subparsers(dest="resource", required=False)

    # ── batches ──
    p_batches = sub.add_parser("batches", help="批次相关查询")
    sub_batches = p_batches.add_subparsers(dest="verb", required=False)
    p_batches_list = sub_batches.add_parser("list", help="列出批次")
    p_batches_list.add_argument("--project-key", required=True)
    p_batches_list.add_argument("--status", action="append", default=[], dest="status_filter",
                                help="可重复;过滤 batch_status")
    p_batches_list.add_argument("--page", type=int, default=1)
    p_batches_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_batches_list.set_defaults(func=_cmd_batches_list)

    p_batches_show = sub_batches.add_parser("show", help="批次详情")
    p_batches_show.add_argument("--batch-id", type=int, required=True, dest="batch_id")
    p_batches_show.set_defaults(func=_cmd_batches_show)

    # ── archives ──
    p_archives = sub.add_parser("archives", help="档案相关查询")
    sub_archives = p_archives.add_subparsers(dest="verb", required=False)
    p_archives_list = sub_archives.add_parser("list", help="列出档案")
    p_archives_list.add_argument("--batch-id", type=int, required=True, dest="batch_id")
    p_archives_list.add_argument("--archive-year", type=int, default=None, dest="archive_year")
    p_archives_list.add_argument("--classification-code", action="append", default=[],
                                 dest="classification_code")
    p_archives_list.add_argument("--retention-period", action="append", default=[],
                                 dest="retention_period")
    p_archives_list.add_argument("--openness-status", default=None, dest="openness_status")
    p_archives_list.add_argument("--processing-status", action="append", default=[],
                                 dest="processing_status")
    p_archives_list.add_argument("--review-status", action="append", default=[],
                                 dest="review_status")
    p_archives_list.add_argument("--correction-status", default=None, dest="correction_status")
    p_archives_list.add_argument("--archive-no", default=None, dest="archive_no")
    p_archives_list.add_argument("--item-no", default=None, dest="item_no")
    p_archives_list.add_argument("--title-like", default=None, dest="title_like")
    p_archives_list.add_argument("--responsible-party-like", default=None,
                                 dest="responsible_party_like")
    p_archives_list.add_argument("--error-code", action="append", default=[], dest="error_code")
    p_archives_list.add_argument("--page", type=int, default=1)
    p_archives_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_archives_list.set_defaults(func=_cmd_archives_list)

    p_archives_show = sub_archives.add_parser("show", help="档案详情")
    p_archives_show.add_argument("--archive-id", type=int, required=True, dest="archive_id")
    p_archives_show.set_defaults(func=_cmd_archives_show)

    # ── revisions ──
    p_revisions = sub.add_parser("revisions", help="档案修正记录")
    sub_revisions = p_revisions.add_subparsers(dest="verb", required=False)
    p_rev_list = sub_revisions.add_parser("list", help="列出修正记录")
    p_rev_list.add_argument("--archive-id", type=int, required=True, dest="archive_id")
    p_rev_list.add_argument("--page", type=int, default=1)
    p_rev_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_rev_list.set_defaults(func=_cmd_revisions_list)

    # ── audit ──
    p_audit = sub.add_parser("audit", help="审计日志")
    sub_audit = p_audit.add_subparsers(dest="verb", required=False)
    p_audit_list = sub_audit.add_parser("list", help="列出审计日志")
    p_audit_list.add_argument("--target-type", required=True, dest="target_type")
    p_audit_list.add_argument("--target-id", type=int, required=True, dest="target_id")
    p_audit_list.add_argument("--page", type=int, default=1)
    p_audit_list.add_argument("--page-size", type=int, default=50, dest="page_size")
    p_audit_list.set_defaults(func=_cmd_audit_list)

    return parser


def _resolve_database_url(args) -> Optional[str]:
    return args.database_url or os.environ.get("DATABASE_URL", "") or None


def _print_json(payload: Any) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    )
    sys.stdout.write("\n")


def _list_result_to_dict(result) -> dict:
    return {
        "items": [dataclasses.asdict(it) for it in result.items],
        "total": result.total,
        "page": result.page,
        "page_size": result.page_size,
        "has_next": result.has_next,
    }


# ── 子命令处理函数 ──────────────────────────────────────────────────────────
def _cmd_batches_list(args, session) -> int:
    from infrastructure.db import queries
    result = queries.list_batches(
        session,
        project_key=args.project_key,
        status_filter=args.status_filter or None,
        page=args.page,
        page_size=args.page_size,
    )
    _print_json(_list_result_to_dict(result))
    return 0


def _cmd_batches_show(args, session) -> int:
    from infrastructure.db import queries
    detail = queries.get_batch_detail(session, batch_id=args.batch_id)
    if detail is None:
        sys.stderr.write(f"not found: batch id={args.batch_id}\n")
        return 4
    _print_json(dataclasses.asdict(detail))
    return 0


def _cmd_archives_list(args, session) -> int:
    raise NotImplementedError


def _cmd_archives_show(args, session) -> int:
    raise NotImplementedError


def _cmd_revisions_list(args, session) -> int:
    raise NotImplementedError


def _cmd_audit_list(args, session) -> int:
    raise NotImplementedError


def run(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse parse_args 在错误时调 sys.exit(2);run() 在 in-process 测试时
        # 想要返回退出码而不是真退出,所以拦截 SystemExit。
        code = exc.code if isinstance(exc.code, int) else 2
        return code

    if not getattr(args, "resource", None) or not getattr(args, "verb", None):
        parser.print_usage(file=sys.stderr)
        sys.stderr.write("error: missing subcommand\n")
        return 2

    func: Optional[Callable] = getattr(args, "func", None)
    if func is None:
        sys.stderr.write("error: missing subcommand handler\n")
        return 2

    database_url = _resolve_database_url(args)
    if not database_url:
        sys.stderr.write("error: DATABASE_URL not set\n")
        return 2

    # 延迟 import,避免没装 SQLAlchemy 的环境 import 阶段就崩
    try:
        from infrastructure.db.engine import (
            check_connectivity,
            dispose_engine,
            make_engine,
            make_session_factory,
        )
    except ImportError as exc:
        sys.stderr.write(
            f"error: missing database dependency: {exc}. "
            "请 pip install -r requirements/db.txt\n"
        )
        return 3

    engine = None
    try:
        engine = make_engine(database_url)
        check_connectivity(engine)
    except Exception as exc:
        sys.stderr.write(f"error: database connection failed: {exc}\n")
        if engine is not None:
            dispose_engine(engine)
        return 3

    session_factory = make_session_factory(engine)
    try:
        with session_factory() as session:
            return func(args, session)
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except Exception as exc:
        logger.exception("unhandled error in subcommand: %s", exc)
        sys.stderr.write(f"error: {exc}\n")
        return 9
    finally:
        dispose_engine(engine)


def main() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(run())


if __name__ == "__main__":
    main()

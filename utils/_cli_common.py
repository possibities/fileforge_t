"""跨 CLI 共用的 argparse 与环境变量助手。

只放与"DB 连接 / 通用入参"相关的最小公共部分,避免每个 CLI 自己复制。
不导入任何 DB / Web 模块,确保 import 这里不会拉起重依赖。
"""

from __future__ import annotations

import argparse
import os
from typing import Optional


def add_database_url_arg(parser: argparse.ArgumentParser) -> None:
    """给 CLI 注册 --database-url 选项,与 main.py / env 同源。"""
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖 DATABASE_URL 环境变量,通常应通过 env 注入",
    )


def resolve_database_url(args: argparse.Namespace) -> Optional[str]:
    """优先取 --database-url,其次 env;空字符串归 None,便于 `if not url` 判定。"""
    return args.database_url or os.environ.get("DATABASE_URL", "") or None

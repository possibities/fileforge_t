"""数据库 Engine 与 Session 工厂。

设计要点：
  - 不在 import 时建连接；所有连接通过显式调用建立。
  - pool_pre_ping=True，避免长跑批次时拿到失效连接。
  - check_connectivity() 在 main.py 启动阶段做一次 SELECT 1，缺连/认证错可立即报错。
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


def make_engine(url: str, *, echo: bool = False) -> Engine:
    if not url:
        raise ValueError("DATABASE_URL 为空，无法创建 engine")
    engine = create_engine(
        url,
        pool_pre_ping=True,
        echo=echo,
        future=True,
    )
    logger.info("[DB] engine 已创建（dialect=%s）", engine.dialect.name)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )


def check_connectivity(engine: Engine, *, statement: str = "SELECT 1") -> None:
    with engine.connect() as conn:
        conn.execute(text(statement))
    logger.info("[DB] 连通性检查通过")


def dispose_engine(engine: Optional[Engine]) -> None:
    if engine is None:
        return
    try:
        engine.dispose()
    except Exception as exc:
        logger.warning("[DB] engine.dispose 失败: %s", exc)

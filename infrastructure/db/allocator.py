"""件号分配器：内存版与数据库版实现共同接口。

BatchProcessor 不再直接持有 SequenceGenerator 实例，
而是从 BatchRecorder 拿 allocator；recorder 不存在时回退到 InMemoryAllocator。
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, Optional, Protocol

from sqlalchemy.orm import Session

from core.sequence_generator import SequenceGenerator

from . import repositories

logger = logging.getLogger(__name__)


class SequenceAllocator(Protocol):
    def assign(self, metadata: Dict) -> Dict: ...


class InMemoryAllocator:
    """直接复用 core.SequenceGenerator，保留进程内 defaultdict 行为。"""

    def __init__(self) -> None:
        self._inner = SequenceGenerator()

    def assign(self, metadata: Dict) -> Dict:
        return self._inner.assign(metadata)


class DatabaseAllocator:
    """从 sequence_counters 行锁递增分配件号；保留尾部新发号策略。

    重要：每次 assign() 单独开短事务并 commit；
    与 BatchRecorder 的单档案大事务解耦，防止件号锁横跨整个 OCR/LLM 阶段。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        project_id: int,
    ) -> None:
        self._session_factory = session_factory
        self._project_id = project_id

    def assign(self, metadata: Dict) -> Dict:
        year_str, classification_code, period_code = self._resolve(metadata)
        if not all([year_str, classification_code, period_code]):
            logger.warning(
                "[件号生成] 跳过：字段不完整 (年度=%s 分类=%s 期限=%s)",
                year_str,
                classification_code,
                period_code,
            )
            metadata["件号"] = None
            metadata["档号"] = None
            return metadata

        with self._session_factory() as session:
            try:
                item_no, archive_no = repositories.assign_sequence(
                    session,
                    project_id=self._project_id,
                    archive_year=year_str,
                    classification_code=classification_code,
                    retention_period_code=period_code,
                )
                session.commit()
            except Exception:
                session.rollback()
                raise

        logger.info("[件号生成] 档号: %s", archive_no)
        metadata["件号"] = item_no
        metadata["档号"] = archive_no
        return metadata

    @staticmethod
    def _resolve(metadata: Dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
        year_str = str(metadata.get("归档年度") or "").strip()
        if not year_str:
            return None, None, None
        try:
            year = int(year_str)
        except ValueError:
            return None, None, None

        classification_code = str(metadata.get("实体分类号") or "").strip() or None
        if not classification_code:
            return year_str, None, None

        period_raw = str(metadata.get("保管期限") or "").strip()
        mapping = (
            SequenceGenerator._PERIOD_CODE_NEW
            if year >= SequenceGenerator._CUTOFF_YEAR
            else SequenceGenerator._PERIOD_CODE_OLD
        )
        period_code = mapping.get(period_raw)
        if not period_code:
            logger.warning(
                "[件号生成] 无法映射保管期限: '%s' (年份=%s)", period_raw, year
            )
            return year_str, classification_code, None
        return year_str, classification_code, period_code


__all__ = ["SequenceAllocator", "InMemoryAllocator", "DatabaseAllocator"]

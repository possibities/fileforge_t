#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
档案智能分类系统 - 件号与档号生成器

件号规则：
  同一归档年度 + 同一实体分类号 + 同一保管期限代码 下的顺序编号（4位补零）

档号规则：
  {归档年度}-{实体分类号}-{保管期限代码}-{件号}

保管期限代码：
  2007年（含）至今：永久=Y  30年=D30  10年=D10
  2006年（含）之前：永久=Y  长期=C    短期=D（并兼容新词 30年=D30/10年=D10，详见 _PERIOD_CODE_OLD）
"""

from collections import defaultdict
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SequenceGenerator:
    """
    批次级件号生成器。

    使用方式：
      - 在批处理循环外创建一次实例
      - 对每份 metadata 调用 assign()
      - 每次程序运行自动从 0001 开始（无需手动 reset）
    """

    # 2007年（含）至今
    _PERIOD_CODE_NEW: Dict[str, str] = {
        "永久": "Y",
        "30年": "D30",
        "10年": "D10",
    }

    # 2006年（含）之前。
    # 本项目分类方案(新增完善指令)的期限词统一为 永久/30年/10年，全程不含 长期/短期，
    # 且规则引擎只会产出这三者。因此 2007 年前档案的期限码必须同时接受这两套词汇——
    # 否则 归档年度<2007 且期限为 30年/10年 的档案映射失败，件号/档号 会被写成 None。
    # 永久/30年/10年 沿用与新表一致的 Y/D30/D10；长期/短期 仅作历史兜底保留。
    _PERIOD_CODE_OLD: Dict[str, str] = {
        "永久": "Y",
        "长期": "C",
        "短期": "D",
        "30年": "D30",
        "10年": "D10",
    }

    # 分界年份：>= 2007 用新编码
    _CUTOFF_YEAR: int = 2007

    def __init__(self):
        # key: (归档年度str, 实体分类号, 保管期限代码)
        # value: 当前计数（从1开始）
        self._counters: Dict[tuple, int] = defaultdict(int)

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def assign(self, metadata: Dict) -> Dict:
        """
        为单份文件分配件号和档号，原地修改并返回 metadata。

        必须在 RulesEngine.apply_all() 之后调用，确保以下字段已最终确定：
          - 归档年度
          - 实体分类号
          - 保管期限

        任一字段缺失或无法解析时，件号和档号均写入 None 并打印警告。
        """
        year_str, classification_code, period_code = self._resolve_fields(metadata)

        if not all([year_str, classification_code, period_code]):
            logger.warning(
                f"[件号生成] 跳过：字段不完整 "
                f"(年度={year_str}, 分类={classification_code}, "
                f"期限代码={period_code})"
            )
            metadata["件号"] = None
            metadata["档号"] = None
            return metadata

        key = (year_str, classification_code, period_code)
        self._counters[key] += 1
        seq = self._counters[key]

        serial = f"{seq:04d}"
        doc_id = f"{year_str}-{classification_code}-{period_code}-{serial}"

        logger.info(f"[件号生成] 档号: {doc_id}")
        metadata["件号"] = serial
        metadata["档号"] = doc_id

        return metadata

    # ── 私有方法 ──────────────────────────────────────────────────────────────

    def _resolve_fields(
        self, metadata: Dict
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        从 metadata 解析三元组 (归档年度, 实体分类号, 保管期限代码)。
        任一字段无效返回对应位置为 None。
        """
        # ── 归档年度 ──────────────────────────────────────────────────────────
        year_str = str(metadata.get("归档年度") or "").strip()
        if not year_str:
            return None, None, None

        try:
            year = int(year_str)
        except ValueError:
            return None, None, None

        # ── 实体分类号 ────────────────────────────────────────────────────────
        classification_code = str(metadata.get("实体分类号") or "").strip()
        if not classification_code:
            return year_str, None, None

        # ── 保管期限代码 ──────────────────────────────────────────────────────
        period_raw = str(metadata.get("保管期限") or "").strip()
        period_map = (
            self._PERIOD_CODE_NEW
            if year >= self._CUTOFF_YEAR
            else self._PERIOD_CODE_OLD
        )
        period_code = period_map.get(period_raw)

        if not period_code:
            logger.warning(
                f"[件号生成] 无法映射保管期限: '{period_raw}' "
                f"(年份={year}, 可用值={list(period_map.keys())})"
            )
            return year_str, classification_code, None

        return year_str, classification_code, period_code

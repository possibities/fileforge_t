#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import logging
from pathlib import Path
from typing import Dict, List

from config.config import Config
from constants import METADATA_SCHEMA
from core.rules_engine import RulesEngine
from infrastructure.llm_client import LlmClient
from infrastructure.ocr_client import OcrClient
from utils.file import get_file_creation_time

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
logger = logging.getLogger(__name__)


class ArchiveClassifier:
    """
    档案智能分类器

    职责：
      - 协调OCR、LLM、规则引擎三个子系统
      - 构建提示词
      - 组装最终元数据
    """

    def __init__(
        self,
        ocr_lang: str = Config.OCR_LANG,
        model_name: str = Config.LLM_MODEL_NAME,
    ):
        self.ocr_client = OcrClient(lang=ocr_lang)
        self.llm_client = LlmClient(model_name=model_name)
        self.rules_engine = RulesEngine()
        self.metadata_schema = METADATA_SCHEMA
        self.extraction_prompt = self._build_extraction_prompt()
        self.briefing_rewrite_prompt = self._load_prompt_file("briefing_rewrite.txt")
        # 暴露最近一次抽取的 LLM trace 给 BatchProcessor → BatchRecorder 落库
        self.last_extraction_trace = None

    # ── 公开接口 ───────────────────────────────────────────────────────────────

    def process_multi_page_document(
        self, archive_name: str, image_paths: List[str]
    ) -> Dict:
        """处理多页档案文件"""
        logger.info(f"\n{'='*70}")
        logger.info(f"处理档案: {archive_name}")
        logger.info(f"页数: {len(image_paths)} 页")
        logger.info(f"{'='*70}\n")

        self.last_extraction_trace = None
        ocr_text = self.ocr_client.extract_text_from_images(image_paths)

        if not ocr_text:
            logger.error("[错误] OCR未识别到任何文字")
            return {}

        logger.info("[OCR结果预览]")
        logger.info("-" * 70)
        preview_length = Config.OCR_PREVIEW_LENGTH
        logger.info(
            ocr_text[:preview_length] + f"\n...(共{len(ocr_text)}字符)"
            if len(ocr_text) > preview_length
            else ocr_text
        )
        logger.info("-" * 70)
        logger.info("")

        metadata = self._extract_metadata_from_text(ocr_text)

        if metadata:
            metadata['数字化时间'] = get_file_creation_time(image_paths[0])
            metadata['档案文件夹'] = archive_name

        return metadata

    # ── 私有方法 ───────────────────────────────────────────────────────────────

    def _extract_metadata_from_text(self, ocr_text: str) -> Dict:
        """使用LLM从OCR文本中提取元数据，并应用规则修正"""
        metadata = self.llm_client.extract_metadata(ocr_text, self.extraction_prompt)
        # 把 LlmClient 本次调用的 trace 透传出去,允许 None
        self.last_extraction_trace = getattr(self.llm_client, "last_trace", None)

        if not metadata:
            return {}

        metadata = self.rules_engine.apply_all(metadata, ocr_text)

        # 规则 11 在标题异常时会设 _需重构简报题名=True，此处是唯一消费者
        if metadata.pop("_需重构简报题名", False):
            self._rewrite_briefing_title(metadata, ocr_text)

        logger.info(
            f"[LLM] 成功提取 "
            f"{len([v for v in metadata.values() if v is not None])} 个有效字段"
        )
        return metadata

    def _rewrite_briefing_title(self, metadata: Dict, ocr_text: str) -> None:
        """
        对文学性简报题名做二次 LLM 重写。

        成功  → 直接改写 metadata["题名"]，不落备注
        失败  → 不改题名，在 metadata["备注"] 追加"待核查"警告，便于人工复核

        所有分支都必须稳，不得抛异常把整条档案处理流程带崩。
        """
        current_title = str(metadata.get("题名") or "").strip()
        responsible_party = str(metadata.get("责任者") or "").strip()

        new_title = ""
        try:
            new_title = self.llm_client.rewrite_briefing_title(
                ocr_text=ocr_text,
                current_title=current_title,
                responsible_party=responsible_party,
                prompt=self.briefing_rewrite_prompt,
            )
        except Exception as exc:
            logger.exception(f"[题名重写] 二次调用抛异常: {exc}")

        if new_title and "简报" in new_title and new_title != current_title:
            logger.info(f"[题名重写] {current_title!r} → {new_title!r}")
            metadata["题名"] = new_title
            return

        if not new_title:
            reason = "二次调用未返回有效题名"
        elif "简报" not in new_title:
            reason = f"模型返回不含'简报'二字: {new_title!r}"
        else:
            reason = f"模型返回与原题名相同: {new_title!r}"
        logger.warning(f"[题名重写失败] {reason}，保留原题名")

        warning = f"【待核查】简报题名疑为文学性标题，需补充责任者及活动事由: {current_title}"
        existing = (metadata.get("备注") or "").strip()
        metadata["备注"] = f"{existing} {warning}".strip() if existing else warning

    def _load_prompt_file(self, filename: str) -> str:
        """加载单个规则文件，文件不存在时快速失败"""
        path = PROMPTS_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"[Prompt文件缺失] {path}")
        return path.read_text(encoding="utf-8").strip()

    def _load_examples(self) -> str:
        """从examples.json加载few-shot示例，转换为prompt字符串"""
        path = PROMPTS_DIR / "examples.json"
        if not path.exists():
            raise FileNotFoundError(f"[示例文件缺失] {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        blocks = []
        for i, ex in enumerate(data["examples"], 1):
            label = ex["label"]
            output = json.dumps(ex["output"], ensure_ascii=False, indent=2)
            blocks.append(f"【JSON输出示例{i} - {label}】\n{output}")

        return "\n\n".join(blocks)

    def _build_extraction_prompt(self) -> str:
        """拼装完整提示词，所有规则内容从外部文件加载"""
        fields_desc = "\n".join(
            [f"- {k}: {v}" for k, v in self.metadata_schema.items()]
        )

        rules_priority = self._load_prompt_file("rules_priority.txt")
        rules_category = self._load_prompt_file("rules_category.txt")
        rules_title    = self._load_prompt_file("rules_title.txt")
        rules_openness = self._load_prompt_file("rules_openness.txt")
        rules_fields   = self._load_prompt_file("rules_fields.txt")
        checklist      = self._load_prompt_file("checklist.txt")
        examples       = self._load_examples()

        return f"""你是专业档案整理员。你的任务是从OCR文本中提取档案元数据，以JSON格式输出。

【输出格式要求 - 最高优先级】
- 只输出一个JSON对象，不得包含任何其他文字、解释、markdown
- 不得输出规则说明、著录指南或任何非JSON内容
- 第一个字符必须是 {{，最后一个字符必须是 }}
- JSON的key必须与下方【需提取的字段】完全一致，禁止使用其他字段名

{rules_priority}

{rules_category}

{rules_openness}

{rules_title}

{rules_fields}

【需提取的字段】（key名称必须与此处完全一致）
{fields_desc}

【合法key列表】（JSON只能包含以下key，不得新增或改名）
{chr(10).join(f'- {k}' for k in self.metadata_schema.keys())}

【OCR识别文本】
{{ocr_text}}

{checklist}

{examples}

再次强调：直接输出JSON对象，第一个字符是 {{，不得有任何前置文字：
"""

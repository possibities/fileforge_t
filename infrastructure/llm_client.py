#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
档案智能分类系统 - LLM客户端（vLLM OpenAI 兼容接口）

本模块只持有一个 OpenAI SDK 客户端，实际推理在外部 vLLM server 中执行。
启动 server 的方式见 docs/vllm_server.md。
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

from config.config import Config
from constants import METADATA_SCHEMA

logger = logging.getLogger(__name__)


# 解析路径标签，与数据库列 archive_records.llm_parse_strategy 取值一致
PARSE_STRATEGY_JSON = "json"
PARSE_STRATEGY_REPAIRED = "repaired"
PARSE_STRATEGY_REGEX = "regex"
PARSE_STRATEGY_FAILED = "failed"


@dataclass
class ExtractionTrace:
    """单次 extract_metadata 调用的可审计快照。

    raw_response       — vLLM 返回的原始 message.content
    cleaned_response   — 去掉 ``` 包裹和 {...} 截取后的字符串
    parse_strategy     — json/repaired/regex/failed,与数据契约 §4.4 对齐
    """

    raw_response: str
    cleaned_response: str
    parse_strategy: str


class LlmClient:
    """
    通过 OpenAI 兼容 API 调用远端 vLLM 服务。

    与旧版 llama-cpp 内嵌推理的差异：
      - 不再加载模型文件，只持有 HTTP 客户端
      - `model` 字段传服务端 `--served-model-name`，非本地路径
      - JSON 强制依赖 vLLM 的 `response_format={"type": "json_object"}`
      - Qwen3 系列通过 `chat_template_kwargs.enable_thinking=False` 关闭思考模式
    """

    def __init__(
        self,
        base_url: str = Config.LLM_BASE_URL,
        api_key: str = Config.LLM_API_KEY,
        model_name: str = Config.LLM_MODEL_NAME,
        timeout: float = Config.LLM_REQUEST_TIMEOUT,
    ):
        if OpenAI is None:
            raise RuntimeError(
                "openai SDK is not installed. Install with: pip install openai"
            )
        self.metadata_schema = METADATA_SCHEMA
        self.model_name = model_name

        logger.info(f"[LLM初始化] vLLM endpoint: {base_url}")
        logger.info(f"[LLM初始化] 模型名: {model_name}")

        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        logger.info("[LLM初始化] OpenAI 客户端就绪")

        # 最近一次 extract_metadata 调用的可审计快照；
        # 调用方(classifier→batch_processor→recorder)可读后落库。
        self.last_trace: Optional[ExtractionTrace] = None

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def extract_metadata(self, ocr_text: str, prompt: str) -> dict:
        logger.info("[LLM] 正在分析文本并提取元数据...")
        self.last_trace = None
        if not ocr_text or not ocr_text.strip():
            logger.warning("[LLM警告] OCR文本为空，跳过LLM提取")
            return {}

        try:
            formatted_prompt = prompt.replace("{ocr_text}", ocr_text)
            response = self._generate(formatted_prompt)

            logger.info(f"[LLM响应] 原始响应长度: {len(response)} 字符")

            cleaned = self._clean_response(response)

            preview = (
                cleaned[:Config.LLM_RESPONSE_PREVIEW_LENGTH]
                if len(cleaned) > Config.LLM_RESPONSE_PREVIEW_LENGTH
                else cleaned
            )
            logger.info(f"[JSON清理后] {preview}...")

            metadata, strategy = self._parse_json(cleaned)
            self.last_trace = ExtractionTrace(
                raw_response=response,
                cleaned_response=cleaned,
                parse_strategy=strategy,
            )
            return metadata

        except Exception as e:
            logger.exception(f"[LLM错误] {str(e)}")
            self.last_trace = ExtractionTrace(
                raw_response="",
                cleaned_response="",
                parse_strategy=PARSE_STRATEGY_FAILED,
            )
            return {}

    def rewrite_briefing_title(
        self,
        ocr_text: str,
        current_title: str,
        responsible_party: str,
        prompt: str,
        ocr_char_limit: int = 3000,
    ) -> str:
        """
        二次调用 LLM，重写"文学性简报题名"为规范题名。

        返回：
          - 重写成功 → 新题名字符串（调用方仍需校验是否以"简报"结尾）
          - LLM 认为信息不足（返回与原题名相同）/ 解析失败 / 异常 → 空字符串
        """
        if not current_title:
            return ""

        excerpt = ocr_text[:ocr_char_limit] if ocr_text else ""
        formatted = (
            prompt
            .replace("{current_title}", current_title)
            .replace("{responsible_party}", responsible_party or "未知")
            .replace("{ocr_text}", excerpt)
        )

        logger.info("[LLM] 二次调用：重写文学性简报题名...")
        try:
            response = self._generate(formatted)
            response = self._clean_response(response)
            parsed, _ = self._parse_json(response)
        except Exception as e:
            logger.exception(f"[LLM重写异常] {e}")
            return ""

        new_title = str(parsed.get("题名") or "").strip() if parsed else ""
        if not new_title:
            logger.warning("[LLM重写] 未解析出题名字段")
            return ""
        if new_title == current_title:
            logger.info("[LLM重写] 模型认为无法重写，保留原题名")
            return ""
        return new_title

    # ── 推理调用 ──────────────────────────────────────────────────────────────

    def _generate(self, prompt: str) -> str:
        """
        调用 vLLM OpenAI 兼容 chat completions 接口。

        - response_format=json_object：vLLM 0.6+ 原生支持 guided JSON
        - chat_template_kwargs.enable_thinking：Qwen3 专有开关，JSON 场景强制 False
        """
        extra_body = {
            "chat_template_kwargs": {
                "enable_thinking": Config.LLM_ENABLE_THINKING,
            }
        }

        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": "你是专业档案整理员，只输出JSON格式的元数据，不输出任何其他内容。",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            temperature=Config.LLM_TEMPERATURE,
            max_tokens=Config.LLM_MAX_TOKENS,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        return response.choices[0].message.content or ""

    # ── 响应清洗与解析（与推理后端无关，逻辑保留）──────────────────────────────

    def _clean_response(self, response: str) -> str:
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        elif response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        if '{' in response and '}' in response:
            start_idx = response.find('{')
            end_idx = response.rfind('}')
            response = response[start_idx:end_idx + 1]
        return response.strip()

    def _parse_json(self, response: str) -> tuple[dict, str]:
        """
        解析 LLM 返回的 JSON 响应,返回 (metadata, parse_strategy)。

        vLLM `response_format={"type": "json_object"}` 保证返回合法 JSON，
        但为防 guided JSON 失效或超长截断，保留一次引号/尾逗号修复重试。
        修复仍失败时，降级为按字段逐个抽取的兜底解析。

        parse_strategy 取值与数据契约 §4.4 的 archive_records.llm_parse_strategy 列对齐:
          - json:     首次 json.loads 直接成功
          - repaired: 修复引号/尾逗号后 json.loads 成功
          - regex:    走逐字段正则兜底成功提取出至少一个字段
          - failed:   全部失败,返回空 dict
        """
        try:
            metadata = json.loads(response)
            return self._filter_metadata_keys(metadata), PARSE_STRATEGY_JSON
        except json.JSONDecodeError as e:
            logger.warning(f"[JSON解析失败] {str(e)}")
            logger.info("[尝试修复JSON格式...]")

        # 仅替换 JSON 结构位的单引号：key 周围（{'k': / ,'k':）与简单 value 位置（: 'v',）
        # 不做全局 replace，避免破坏字符串值中合法的单引号
        fixed = re.sub(r"([{,]\s*)'([^'\n]+?)'(\s*:)", r'\1"\2"\3', response)
        fixed = re.sub(r"(:\s*)'([^'\n]*?)'(\s*[,}])", r'\1"\2"\3', fixed)
        fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)
        try:
            metadata = json.loads(fixed)
            logger.info("[修复成功] 成功提取字段")
            return self._filter_metadata_keys(metadata), PARSE_STRATEGY_REPAIRED
        except Exception:
            metadata = self._extract_fields_by_regex(fixed)
            if metadata:
                logger.info("[JSON兜底] 通过逐字段抽取恢复部分字段")
                return metadata, PARSE_STRATEGY_REGEX
            logger.warning("[JSON修复失败] 完整响应:")
            logger.warning("-" * 70)
            logger.warning(response)
            logger.warning("-" * 70)
            return {}, PARSE_STRATEGY_FAILED

    def _filter_metadata_keys(self, metadata: dict[str, Any]) -> dict:
        return {k: v for k, v in metadata.items() if k in self.metadata_schema}

    def _extract_fields_by_regex(self, response: str) -> dict:
        """
        在整体 JSON 解析失败时，按允许字段逐个定位并解析值。

        该兜底路径主要覆盖以下场景：
          - 顶层 JSON 被截断，但前部字段仍然完整
          - 模型输出额外噪声，导致整体对象无法一次性解析
        """
        decoder = json.JSONDecoder()
        metadata: dict[str, Any] = {}

        for key in self.metadata_schema:
            pattern = rf'"{re.escape(key)}"\s*:'
            match = re.search(pattern, response)
            if not match:
                continue

            value_text = response[match.end():].lstrip()
            try:
                value, _ = decoder.raw_decode(value_text)
            except json.JSONDecodeError:
                continue
            metadata[key] = value

        return metadata

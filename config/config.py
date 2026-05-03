#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
from pathlib import Path


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _env_path(name: str, default: Path) -> str:
    value = os.getenv(name)
    if not value:
        return str(default)
    return str(Path(value).expanduser())


class Config:

    # ── OCR ──────────────────────────────────────────────────────────────────
    OCR_LANG: str = _env_str("OCR_LANG", "ch")
    OCR_USE_GPU: bool = _env_bool("OCR_USE_GPU", True)
    OCR_USE_ANGLE_CLS: bool = _env_bool("OCR_USE_ANGLE_CLS", True)
    OCR_SHOW_LOG: bool = _env_bool("OCR_SHOW_LOG", False)
    OCR_DROP_SCORE: float = _env_float("OCR_DROP_SCORE", 0.1)
    OCR_DET_DB_THRESH: float = _env_float("OCR_DET_DB_THRESH", 0.2)
    OCR_DET_DB_BOX_THRESH: float = _env_float("OCR_DET_DB_BOX_THRESH", 0.45)
    OCR_DET_DB_UNCLIP_RATIO: float = _env_float("OCR_DET_DB_UNCLIP_RATIO", 1.8)
    OCR_ENABLE_PREPROCESS: bool = _env_bool("OCR_ENABLE_PREPROCESS", True)
    OCR_RETRY_MIN_TEXT_CHARS: int = _env_int("OCR_RETRY_MIN_TEXT_CHARS", 24)
    OCR_RETRY_LOW_AVG_CONFIDENCE: float = _env_float(
        "OCR_RETRY_LOW_AVG_CONFIDENCE",
        0.82,
    )
    OCR_RETRY_LOW_CONF_RATIO: float = _env_float("OCR_RETRY_LOW_CONF_RATIO", 0.35)
    OCR_PREPROCESS_SCALE: float = _env_float("OCR_PREPROCESS_SCALE", 1.5)
    OCR_PREPROCESS_MAX_SIDE: int = _env_int("OCR_PREPROCESS_MAX_SIDE", 2800)
    OCR_PREPROCESS_CONTRAST: float = _env_float("OCR_PREPROCESS_CONTRAST", 1.25)

    # ── LLM (vLLM OpenAI 兼容服务) ──────────────────────────────────────────
    # 客户端向 vLLM server 发 HTTP 请求，不再在本进程加载模型。
    # 服务启动方式见 docs/vllm_server.md。
    LLM_BASE_URL: str = _env_str("LLM_BASE_URL", "http://localhost:8000/v1")
    LLM_API_KEY: str = _env_str("LLM_API_KEY", "EMPTY")  # vLLM 默认不校验
    LLM_MODEL_NAME: str = _env_str("LLM_MODEL_NAME", "qwen3-32b-awq")
    LLM_TEMPERATURE: float = _env_float("LLM_TEMPERATURE", 0.1)
    LLM_MAX_TOKENS: int = _env_int("LLM_MAX_TOKENS", 512)
    LLM_REQUEST_TIMEOUT: float = _env_float("LLM_REQUEST_TIMEOUT", 300.0)
    # Qwen3 思考模式会在输出前追加 <think>...</think>，JSON 抽取场景必须关闭
    LLM_ENABLE_THINKING: bool = _env_bool("LLM_ENABLE_THINKING", False)

    # ── 日志 ─────────────────────────────────────────────────────────────────
    OCR_PREVIEW_LENGTH: int = _env_int("OCR_PREVIEW_LENGTH", 500)
    LLM_RESPONSE_PREVIEW_LENGTH: int = _env_int("LLM_RESPONSE_PREVIEW_LENGTH", 200)

    # ── 路径配置 ─────────────────────────────────────────────────────────────
    _BASE = Path(__file__).resolve().parent.parent
    EXPORTER_CONFIG_PATH: str = _env_path(
        "EXPORTER_CONFIG_PATH",
        _BASE / "config" / "exporter.json",
    )
    INPUT_DIR: str = _env_path("INPUT_DIR", _BASE / "input_documents")
    OUTPUT_DIR: str = _env_path("OUTPUT_DIR", _BASE / "output_results")

    # ── 数据库（旁路写库，未配置时整条管线行为不变） ─────────────────────────
    # DATABASE_URL 留空 → 跳过任何数据库写入，main.py 走纯文件路径
    # 设置后必须显式指定 PROJECT_KEY 与 BATCH_KEY，否则启动失败
    DATABASE_URL: str = _env_str("DATABASE_URL", "")
    PROJECT_KEY: str = _env_str("PROJECT_KEY", "")
    PROJECT_NAME: str = _env_str("PROJECT_NAME", "")
    BATCH_KEY: str = _env_str("BATCH_KEY", "")
    # skip-success / rerun-failed-only / rerun-all / force-renumber
    DB_RERUN_POLICY: str = _env_str("DB_RERUN_POLICY", "skip-success")

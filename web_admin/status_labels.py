"""状态码 → 中文展示文本。

数据库里状态字段统一存英文枚举(见 infrastructure/db/models.py),前端展示时
通过 Jinja 过滤器 ``status_label`` 翻成中文。CSS class 仍沿用英文原值
(``status-success`` 等),所以只翻显示文本、不动配色。

某些枚举值在不同域里含义不同(例如 ``pending`` 在处理域是“等待”、在复核域是
“待复核”),因此 ``status_label`` 接受一个可选的 ``domain`` 参数做消歧;不传时
回退到通用映射。"""

from __future__ import annotations

from typing import Optional

# 各业务域的专属翻译(优先级高于通用映射)。
_DOMAIN_LABELS: dict[str, dict[str, str]] = {
    # ArchiveRecord.processing_status / ProcessingJob.status
    # 多个“进行中”细分状态统一显示为“处理中”,只保留一个“…中”,避免过度细化。
    "processing": {
        "pending": "排队中",
        "queued": "排队中",
        "running": "处理中",
        "ocr_running": "处理中",
        "llm_running": "处理中",
        "rules_running": "处理中",
        "exporting": "处理中",
        "success": "成功",
        "failed": "失败",
        "cancelled": "已取消",
        "error": "错误",
    },
    # ArchiveRecord.review_status —— 统一用“审核”,不再出现“复核”。
    "review": {
        "pending": "待审核",
        "needs_review": "重点审核",
        "reviewed": "已审核",
        "not_required": "无需审核",
        "in_review": "审核中",
        "confirmed": "已审核",
    },
    # ArchiveRecord.correction_status
    "correction": {
        "none": "未修正",
        "corrected": "已修正",
    },
    # ProcessingBatch.batch_status
    "batch": {
        "queued": "排队中",
        "running": "处理中",
        "success": "成功",
        "partial_failed": "部分失败",
        "failed": "失败",
        "cancelled": "已取消",
        "completed": "已完成",
        "aborted": "已中止",
    },
    # UploadBatch.status / UploadedFile.status
    "upload": {
        "uploading": "上传中",
        "uploaded": "已上传",
        "validated": "已校验",
        "processing": "处理中",
        "processed": "已处理",
        "failed": "失败",
        "stored": "已存储",
        "invalid": "无效",
    },
    # Organization / AppUser / Project.status
    "account": {
        "active": "启用",
        "disabled": "已停用",
        "archived": "已归档",
    },
}

# 不指定 domain 时的通用回退(取各域并集,冲突键挑最常见义)。
_COMMON_LABELS: dict[str, str] = {}
for _domain, _mapping in _DOMAIN_LABELS.items():
    for _key, _label in _mapping.items():
        _COMMON_LABELS.setdefault(_key, _label)


def status_label(value: Optional[str], domain: Optional[str] = None) -> str:
    """把英文状态码翻成中文;未知值原样返回。"""
    if value is None:
        return ""
    if domain:
        domain_map = _DOMAIN_LABELS.get(domain)
        if domain_map and value in domain_map:
            return domain_map[value]
    return _COMMON_LABELS.get(value, value)

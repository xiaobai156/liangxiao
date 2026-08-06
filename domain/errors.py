from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    NETWORK_TIMEOUT = "网络请求超时"
    SSL_FAILURE = "SSL连接失败"
    HTTP_FAILURE = "HTTP请求失败"
    CONTENT_NOT_PUBLISHED = "内容尚未发布"
    STRUCTURE_CHANGED = "页面结构已变化"
    ANCHOR_MISSING = "未找到专属锚点"
    TARGET_MISSING = "未找到指定目标"
    FIELD_VALIDATION = "字段校验未通过"
    DATA_CONFLICT = "数据存在冲突"
    ADAPTIVE_REJECTED = "自适应匹配被拒绝"
    BROWSER_FAILURE = "浏览器渲染失败"
    PROFILE_UNAVAILABLE = "结构档案不可用"


class ScrapeFailure(ValueError):
    def __init__(self, category: ErrorCategory, detail: str = "") -> None:
        self.category = category
        self.detail = detail.strip()
        super().__init__(str(self))

    def __str__(self) -> str:
        return self.category.value if not self.detail else f"{self.category.value}：{self.detail}"


class CandidateConflict(ScrapeFailure):
    def __init__(self, detail: str, candidates: list[Any]) -> None:
        self.candidates = tuple(candidates)
        super().__init__(ErrorCategory.DATA_CONFLICT, detail)


class ConfigurationError(ValueError):
    """Raised before startup when V2 configuration is invalid."""

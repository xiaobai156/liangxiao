from __future__ import annotations

import re

import requests

from domain.errors import ErrorCategory, ScrapeFailure


def format_failure(exc: BaseException) -> str:
    if isinstance(exc, ScrapeFailure):
        return str(exc)
    detail = str(exc)
    if isinstance(exc, requests.exceptions.Timeout):
        category = ErrorCategory.NETWORK_TIMEOUT
    elif isinstance(exc, requests.exceptions.SSLError):
        category = ErrorCategory.SSL_FAILURE
    elif isinstance(exc, requests.RequestException):
        category = ErrorCategory.HTTP_FAILURE
    elif "浏览器渲染" in detail:
        category = ErrorCategory.BROWSER_FAILURE
    elif re.search(r"(?:top|bottom)\s*(?:列表)?候选内未找到\s*\d{3}\s*期", detail):
        category = ErrorCategory.CONTENT_NOT_PUBLISHED
    elif "冲突" in detail:
        category = ErrorCategory.DATA_CONFLICT
    elif "锚点" in detail and "未找到" in detail:
        category = ErrorCategory.ANCHOR_MISSING
    elif any(marker in detail for marker in ("未抓到有效候选", "未找到指定期数", "未找到对应后台文章")):
        category = ErrorCategory.TARGET_MISSING
    elif any(marker in detail for marker in ("目标脚本", "页面结构", "详情页脚本")):
        category = ErrorCategory.STRUCTURE_CHANGED
    else:
        category = ErrorCategory.FIELD_VALIDATION
    prefix = f"{category.value}："
    return detail if detail.startswith(prefix) else prefix + detail


def progress_summary_line(
    done: int,
    total: int,
    success: int,
    failed: int,
    elapsed: float,
    site_name: str,
    ok: bool,
    error: str = "",
) -> str:
    percent = done * 100 // total if total else 0
    state = "成功" if ok else f"失败 原因：{error or '未知原因'}"
    return f"[进度 {done}/{total} {percent}% 成功 {success} 失败 {failed} 用时 {elapsed:.1f}s] 当前：{site_name} {state}"

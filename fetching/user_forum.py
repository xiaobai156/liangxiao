from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import cast
from urllib.parse import urlparse

import requests


def user_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"(?:^|/)users/(\d+)", parsed.fragment or parsed.path)
    if not match:
        raise ValueError(f"无法识别用户 ID：{url}")
    return match.group(1)


def user_forums_api_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/users/{user_id(url)}/forums"


def user_forum_detail_url(url: str, item_id: object) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/forums/{item_id}"


def enrich_user_forums(
    items: list[dict[str, object]],
    base_url: str,
    fetch: Callable[[str], str],
    limit: int = 1,
) -> tuple[list[dict[str, object]], str]:
    """用帖子详情接口补全最新几条正文（列表接口只返回正文前缀）。"""
    link_reference = ""
    for item in items[:limit]:
        item_id = item.get("id")
        if item_id in (None, ""):
            continue
        detail_url = user_forum_detail_url(base_url, item_id)
        try:
            source = str(fetch(detail_url))
            data = json.loads(source)
        except (requests.RequestException, ValueError):
            continue
        payload = data.get("data", data) if isinstance(data, dict) else None
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if not isinstance(payload, dict):
            continue
        content = payload.get("content")
        if not isinstance(content, str) or not content:
            continue
        if str(payload.get("topic") or "") != str(item.get("topic") or ""):
            continue
        if len(content) <= len(str(item.get("content") or "")):
            continue
        item["content"] = content
        link_reference = urlparse(detail_url).path
    return items, link_reference


def filter_user_forums(source: str, expected_user_id: str) -> tuple[list[dict[str, object]], int]:
    data = json.loads(source)
    if not isinstance(data, list) or not data:
        raise ValueError("用户接口没有有效论坛记录")
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("用户接口记录结构无效")
        nested_user = item.get("user")
        nested_id = nested_user.get("id") if isinstance(nested_user, dict) else None
        item_user_id = str(item.get("user_id") or nested_id or "")
        if item_user_id != expected_user_id:
            raise ValueError(
                f"用户ID边界冲突：目标用户{expected_user_id}，记录用户{item_user_id or '缺失'}"
            )
    return cast(list[dict[str, object]], data), len(data)

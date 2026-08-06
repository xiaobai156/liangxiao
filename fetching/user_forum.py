from __future__ import annotations

import json
import re
from urllib.parse import urlparse


def user_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"(?:^|/)users/(\d+)", parsed.fragment or parsed.path)
    if not match:
        raise ValueError(f"无法识别用户 ID：{url}")
    return match.group(1)


def user_forums_api_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/api/v1/users/{user_id(url)}/forums"


def filter_user_forums(source: str, expected_user_id: str) -> tuple[list[dict[str, object]], int]:
    data = json.loads(source)
    if not isinstance(data, list) or not data:
        raise ValueError("用户接口没有有效论坛记录")
    records: list[dict[str, object]] = []
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
        records.append(item)
    return records, len(data)

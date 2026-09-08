from __future__ import annotations

import json

from domain.models import Record, Site
from fetching.user_forum import filter_user_forums, user_id
from parsers.new_sites_235 import parse_xiaosuan_bottom_records
from validation.direction import select_record


def select_target_record(source: str, site: Site, period: int) -> Record:
    items, _count = filter_user_forums(source, user_id(site.url))
    matches = [
        item
        for item in items
        if item.get("draw") == period
        and item.get("status") == "published"
        and str(item.get("topic") or "").strip() == "杀肖"
        and isinstance(item.get("user"), dict)
        and str(item["user"].get("nickname") or "") == site.name
    ]
    if len(matches) != 1:
        raise ValueError(f"{period}期目标帖子数量不是1：{len(matches)}")
    records = parse_xiaosuan_bottom_records(
        json.dumps(matches, ensure_ascii=False),
        site,
    )
    if not records:
        raise ValueError(f"{period}期目标帖子内没有有效二肖候选")
    return select_record(records, period, site)

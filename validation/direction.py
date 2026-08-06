from __future__ import annotations

from domain.models import Record, Site
from validation.records import (
    merge_equivalent_records,
    record_value_signature,
)


def _merge_adjacent_equivalent(records: list[Record]) -> list[Record]:
    merged: list[Record] = []
    for record in records:
        if (
            merged
            and merged[-1].period == record.period
            and record_value_signature(merged[-1]) == record_value_signature(record)
        ):
            merged[-1] = merge_equivalent_records([merged[-1], record])
        else:
            merged.append(record)
    return merged


def direction_window(records: list[Record], site: Site) -> list[Record]:
    logical_records = _merge_adjacent_equivalent(records)
    return logical_records[:3] if site.pick == "top" else logical_records[-3:]


def select_record(records: list[Record], period: int, site: Site) -> Record:
    if not records:
        raise ValueError("未抓到有效候选")
    window = direction_window(records, site)
    matches = [record for record in window if record.period == period]
    if not matches:
        actual = "、".join(
            f"{record.period}期{record.zodiac}@位置{record.position}"
            for record in window
        ) or "无"
        raise ValueError(
            f"{site.pick} 候选内未找到 {period} 期；实际近3条：{actual}"
        )
    signatures = {record_value_signature(record) for record in matches}
    if len(signatures) > 1:
        details = "、".join(
            f"{record.zodiac}@位置{record.position}"
            for record in sorted(matches, key=lambda item: (item.position, item.zodiac))
        )
        raise ValueError(f"数据存在冲突：{period}期出现多个候选：{details}")
    return merge_equivalent_records(matches, prefer_last=site.pick == "bottom")

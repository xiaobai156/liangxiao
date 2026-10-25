from __future__ import annotations

from domain.models import Record, Site
from validation.records import (
    merge_equivalent_records,
    record_signature,
)

PERIOD_WRAP = 365
# 窗口只取 3 条；目标期与窗口边界的距离超过这个数，说明目标期不在本块。
MAX_WINDOW_DISTANCE = 3


def _period_delta(previous: int, current: int) -> int:
    """相邻期号步进，365<->001 按跨年回绕处理。"""
    if previous == PERIOD_WRAP and current == 1:
        return 1
    if previous == 1 and current == PERIOD_WRAP:
        return -1
    return current - previous


def _merge_adjacent_equivalent(records: list[Record]) -> list[Record]:
    merged: list[Record] = []
    for record in records:
        if (
            merged
            and merged[-1].period == record.period
            and record_signature(merged[-1]) == record_signature(record)
        ):
            merged[-1] = merge_equivalent_records([merged[-1], record])
        else:
            merged.append(record)
    return merged


def _target_belongs_to_block(records: list[Record], period: int, site: Site) -> bool:
    """目标期必须落在区块对应端点的窗口距离内，否则说明取到的是邻块的值。

    跨期借值的典型形态：目标期不在本块里，但邻块恰好包含它，
    方向窗口（前3/后3）把邻块的值带了进来。此时目标期与本块端点的
    期号距离会超出窗口长度；按距离判定即可拒绝。

    距离用「端点 -> 目标期」的有符号步进再取绝对值：top 站端点是最新期，
    目标期通常比它小（delta 为负），bottom 站端点是末尾期，方向相反。
    这里只关心距离，不关心方向，因此统一取绝对值。
    """
    if not records:
        return False
    edge = records[0].period if site.pick == "top" else records[-1].period
    return abs(_period_delta(edge, period)) <= MAX_WINDOW_DISTANCE


def direction_window(records: list[Record], site: Site) -> list[Record]:
    logical_records = _merge_adjacent_equivalent(records)
    return logical_records[:3] if site.pick == "top" else logical_records[-3:]


def select_record(records: list[Record], period: int, site: Site) -> Record:
    if not records:
        raise ValueError("未抓到有效候选")
    logical = _merge_adjacent_equivalent(records)
    if not _target_belongs_to_block(logical, period, site):
        edge = logical[0] if site.pick == "top" else logical[-1]
        raise ValueError(
            f"{site.pick} 候选内未找到 {period} 期；"
            f"目标期不在本区块窗口内（块端点 {edge.period}期），"
            f"疑似跨块借值，已拒绝"
        )
    window = direction_window(logical, site)
    matches = [record for record in window if record.period == period]
    if not matches:
        actual = "、".join(
            f"{record.period}期{record.zodiac}@位置{record.position}"
            for record in window
        ) or "无"
        raise ValueError(
            f"{site.pick} 候选内未找到 {period} 期；实际近3条：{actual}"
        )
    signatures = {record_signature(record) for record in matches}
    if len(signatures) > 1:
        details = "、".join(
            f"{record.zodiac}@位置{record.position}"
            for record in sorted(matches, key=lambda item: (item.position, item.zodiac))
        )
        raise ValueError(f"数据存在冲突：{period}期出现多个候选：{details}")
    return merge_equivalent_records(matches, prefer_last=site.pick == "bottom")

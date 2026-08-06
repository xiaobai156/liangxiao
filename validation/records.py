from __future__ import annotations

import re
from dataclasses import replace

from domain.models import POSITION_KIND_VISIBLE_TEXT, Record


ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"
ZODIAC_SET = set(ZODIACS)


def normalize_zodiac(value: str) -> str:
    return re.sub(r"[\s\-－.。·、,，]+", "", value)


def record_signature(record: Record) -> tuple[str, int]:
    return normalize_zodiac(record.zodiac), record.position


def record_value_signature(record: Record) -> str:
    return normalize_zodiac(record.zodiac)


def merge_equivalent_records(records: list[Record], *, prefer_last: bool = False) -> Record:
    selected = records[-1] if prefer_last else records[0]
    positions = sorted(
        {
            position
            for record in records
            for position in (record.source_positions or (record.position,))
            if position >= 0
        }
    )
    return replace(selected, source_positions=tuple(positions))


def validate_selected_record(record: Record, period: int) -> bool:
    zodiac = normalize_zodiac(record.zodiac)
    return (
        record.period == period
        and len(zodiac) == 2
        and len(set(zodiac)) == 2
        and all(value in ZODIAC_SET for value in zodiac)
        and record.position >= 0
        and record.position_kind == POSITION_KIND_VISIBLE_TEXT
    )

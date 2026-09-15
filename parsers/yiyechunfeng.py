from __future__ import annotations

import re

from domain.models import DOCUMENT_BOUNDARY, Record, Site
from parsers.helpers import ZODIACS, ZODIAC_SET, clean_zodiac, select_target_block

YIYECHUNFENG_NAME = "一夜春风"
YIYECHUNFENG_URL = "https://62139c.com/62.html"
YIYECHUNFENG_TITLE = r"【\s*码友一夜春风\s*】"
YIYECHUNFENG_STOP = r"【\s*码友书阳\s*】"

YIYECHUNFENG_RECORD = re.compile(
    rf"(?P<period>\d{{3}})\s*期\s*杀\s*"
    rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
    rf"(?P<open>[√×✓✗]?)"
)


def _is_exact_yiyechunfeng(site: Site) -> bool:
    return (
        site.name == YIYECHUNFENG_NAME
        and site.pick == "bottom"
        and site.url == YIYECHUNFENG_URL
    )


def _invalid_zodiac(zodiac: str) -> bool:
    return len(zodiac) != 2 or zodiac[0] == zodiac[1] or any(item not in ZODIAC_SET for item in zodiac)


def parse_yiyechunfeng_bottom_records(source: str, site: Site) -> list[Record]:
    """码友一夜春风：只取《码友一夜春风》到《码友书阳》之间的杀二肖记录。"""
    if not _is_exact_yiyechunfeng(site) or DOCUMENT_BOUNDARY in source:
        return []
    selection = select_target_block(source, site)
    if selection is None or not selection.text:
        return []
    records: list[Record] = []
    seen: set[tuple[int, str, int]] = set()
    for match in YIYECHUNFENG_RECORD.finditer(selection.text):
        zodiac = clean_zodiac(match.group("zodiac"))
        if _invalid_zodiac(zodiac):
            continue
        period = int(match.group("period"))
        position = selection.block_start + match.start()
        key = (period, zodiac, position)
        if key in seen:
            continue
        seen.add(key)
        records.append(
            Record(
                period,
                zodiac,
                match.group("open"),
                match.group(0),
                position,
                anchor_text=selection.anchor_text,
                anchor_offset=selection.anchor_offset,
                block_id=selection.block_id,
                block_start=selection.block_start,
                block_end=selection.block_end,
                source_positions=(position,),
            )
        )
    return records

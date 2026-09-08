from __future__ import annotations

import re

from domain.models import Record, Site
from parsers.helpers import (
    ZODIACS,
    html_to_text,
    records_from_pattern,
)

def parse_yanyu_fusu_list_detail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if not re.search(r"\d{3}\s*期\s*[:：]\s*[【〖]\s*烟雨扶苏\s*[】〗].{0,40}?绝\s*杀\s*(?:二|两|2|２|②)\s*肖", text):
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*➹\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*➹\s*_\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*_\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)

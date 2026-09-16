from __future__ import annotations

import re

from domain.models import Record, Site
from parsers.helpers import ZODIACS, ZODIAC_SET, clean_zodiac, html_to_text

ZHUANXIN_NAME = "专心致志"
ZHUANXIN_URL = "https://xoxtpupt.bvptr-i3mv8-pbjxin.work:17455/topic/226532.html"
ZHUANXIN_AUTHOR = r"作者\s*[:：]\s*专心致志"

ZHUANXIN_RECORD = re.compile(
    rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
    rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
    rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
)


def _is_exact_zhuanxin(site: Site) -> bool:
    return (
        site.name == ZHUANXIN_NAME
        and site.pick == "top"
        and site.url == ZHUANXIN_URL
    )


def parse_zhuanxin_zhizhi_records(source: str, site: Site) -> list[Record]:
    """专心致志：必须锁定本站作者锚点「作者:专心致志」，只解析其后的绝杀二肖记录。"""
    if not _is_exact_zhuanxin(site):
        return []
    text = html_to_text(source)
    anchors = list(re.finditer(ZHUANXIN_AUTHOR, text))
    if len(anchors) != 1:
        # 站点锚点缺失或重复：不得解析，避免与页面其它栏目串站
        return []
    anchor = anchors[0]
    tail = text[anchor.start():]
    records: list[Record] = []
    seen: set[tuple[int, str, int]] = set()
    for match in ZHUANXIN_RECORD.finditer(tail):
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or zodiac[0] == zodiac[1] or any(z not in ZODIAC_SET for z in zodiac):
            continue
        period = int(match.group("period"))
        position = anchor.start() + match.start()
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
                anchor_text=anchor.group(0),
                anchor_offset=anchor.start(),
                source_positions=(position,),
            )
        )
    return records

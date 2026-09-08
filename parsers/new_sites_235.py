from __future__ import annotations

import json
import re

from domain.models import DOCUMENT_BOUNDARY, POSITION_KIND_VISIBLE_TEXT, Record, Site
from parsers.helpers import (
    ZODIACS,
    clean_zodiac,
    html_to_text,
    parse_site_scoped_two_zodiac_records,
    records_from_pattern,
)


EXACT_TOPIC_SITES = {
    "上官研之": ("top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/479502.html"),
    "杯中之水": ("top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/453967.html"),
    "林间语屑": ("bottom", "https://citsyvlm.5ujgl-2q1ig-bszkno.work:17466/topic/682028.html"),
    "恍然大悟": ("top", "https://cxmdjok.8wtwc-boven-glylvt.xyz:16677/topic/437767.html"),
    "粉骨糜身": ("top", "https://qhfsrngx.x9mcp-vrrqh-dzqfro.xyz:16677/topic/451425.html"),
    "安宅正路": ("top", "https://uhaxnrzx.q76gf-deec8-zqckeo.xyz:16677/topic/615690.html"),
    "坐怀不乱": ("top", "https://gabqzzz.ahv8c-6713g-otpeiy.xyz:16677/topic/530161.html"),
    "市区幸福": ("top", "https://yelsagvn.1iwwa-jwubd-szbnwd.work:17466/topic/551397.html"),
    "燃萁煎豆": ("bottom", "https://kjqba.ipvrs-9mw2c-wdnuvo.work/topic/213294.html"),
}


def parse_new_topic_235_records(source: str, site: Site) -> list[Record]:
    expected = EXACT_TOPIC_SITES.get(site.name)
    if (
        expected != (site.pick, site.url)
        or site.payload != "page_and_scripts"
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    return parse_site_scoped_two_zodiac_records(source, site)


def parse_zhougong_shensuan_records(source: str, site: Site) -> list[Record]:
    if (
        (site.name, site.pick, site.url, site.payload)
        != (
            "周公神算",
            "top",
            "https://xxn08n.w2jqr-rbl71-ngvkmq.work/",
            "page_and_scripts",
        )
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(html_to_text(source), pattern)


def parse_feilong_qishi_records(source: str, site: Site) -> list[Record]:
    if (
        (site.name, site.pick, site.url, site.payload)
        != (
            "飞龙骑士",
            "bottom",
            "https://bbs02.836280.cyou/bbs/topic.php?id=18357",
            "page_and_scripts",
        )
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    visible = html_to_text(source)
    boundary = re.search(r"(?:下一贴|上一篇)\s*[：:]", visible)
    if boundary:
        visible = visible[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*[【〖\[]\s*飞龙骑士\s*[】〗\]]"
        rf"[\s\S]{{0,40}}?绝\s*杀\s*(?:二|两|2|２|②)\s*肖[\s\S]{{0,20}}?"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(visible, pattern)


def parse_tuoni_kulmonika_records(source: str, site: Site) -> list[Record]:
    if (
        (site.name, site.pick, site.url, site.payload)
        != (
            "托尼库尔莫妮卡",
            "top",
            "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245",
            "tuku_user_forums",
        )
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    records: list[Record] = []
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        if not isinstance(user, dict) or str(user.get("nickname") or "") != site.name:
            continue
        topic = str(item.get("topic") or "")
        match = re.fullmatch(
            rf"杀\s*[:：]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])",
            topic,
        )
        try:
            period = int(item.get("draw"))
        except (TypeError, ValueError):
            continue
        zodiac = clean_zodiac(match.group("zodiac")) if match else ""
        if not 1 <= period <= 365 or len(zodiac) != 2 or zodiac[0] == zodiac[1]:
            continue
        raw = f'"draw": {period}, "topic": {json.dumps(topic, ensure_ascii=False)}'
        position = source.find(raw)
        if position < 0:
            continue
        records.append(
            Record(
                period,
                zodiac,
                "",
                raw,
                position,
                position_kind=POSITION_KIND_VISIBLE_TEXT,
                source_positions=(position,),
            )
        )
    return records


def parse_xiaosuan_bottom_records(source: str, site: Site) -> list[Record]:
    if (
        (site.name, site.pick, site.url, site.payload)
        != (
            "小算算",
            "bottom",
            "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
            "tuku_user_forums",
        )
        or DOCUMENT_BOUNDARY in source
    ):
        return []
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    primaries = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("topic") or "").strip() == "杀肖"
        and isinstance(item.get("user"), dict)
        and str(item["user"].get("nickname") or "") == site.name
    ]
    if len(primaries) != 1:
        return []
    primary = primaries[0]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*(?:杀\s*)?"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])"
        rf"(?!\s*[{ZODIACS}])\s*(?P<open>[✓✔√❌？?]*)"
    )
    return records_from_pattern(html_to_text(str(primary.get("content") or "")), pattern)

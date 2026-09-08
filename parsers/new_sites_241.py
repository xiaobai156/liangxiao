from __future__ import annotations

import re

from domain.models import DOCUMENT_BOUNDARY, Record, Site
from parsers.helpers import ZODIACS, html_to_text, records_from_pattern

LUJIU_WENSHA_URL = "https://xclpqth.4zsn8-rzqg9-ulwfyu.work:17477/topic/800282.html"
LUJIU_HOME_URL = "https://xclpqth.4zsn8-rzqg9-ulwfyu.work:17477/"
YUEKU_ZHIXIAO_URL = "https://jtrmhar.cwdc3-r5vqn-qzqasa.work:17455/topic/741219.html"
JIUJIE_LIANGFENG_URL = "https://ygaazoi.80f2d-qi0ig-gkygze.work:17488/topic/251180.html"
BUBU_GAOSHENG_URL = "https://azyqhiut.9kzow-3yq4j-hlwmev.work:17477/"
TIANLANG_SHAXING_URL = (
    "https://etmchyhq.cee5t-w30bf-xgqspr.work:17477/topic/248728.html"
)


def _records_after_heading(
    source: str,
    heading_pattern: str,
    record_pattern: re.Pattern[str],
) -> list[Record]:
    if DOCUMENT_BOUNDARY in source:
        return []
    text = html_to_text(source)
    headings = list(re.finditer(heading_pattern, text))
    if len(headings) != 1:
        return []
    heading = headings[0]
    return records_from_pattern(
        text[heading.end() :], record_pattern, base_offset=heading.end()
    )


STEADY_KILL_PATTERN = re.compile(
    rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*☆?\s*稳\s*杀\s*(?:二|两|2|２|②)\s*肖\s*☆?\s*"
    rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
    rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
)
ABSOLUTE_KILL_PATTERN = re.compile(
    rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝\s*杀\s*(?:二|贰|两|2|２|②)\s*肖\s*"
    rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
    rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
)


def parse_lujiu_wensha_bottom_records(source: str, site: Site) -> list[Record]:
    if site.name != "卢九稳杀" or site.pick != "bottom" or site.url != LUJIU_WENSHA_URL:
        return []
    return _records_after_heading(
        source,
        r"\d{3}\s*期\s*[:：]\s*49卢九\s*[【〖]\s*稳杀二肖\s*[】〗]\s*优秀推荐",
        STEADY_KILL_PATTERN,
    )


def parse_lujiu_home_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "卢九" or site.pick != "top" or site.url != LUJIU_HOME_URL:
        return []
    return _records_after_heading(
        source,
        r"49卢九\s*[【〖]\s*稳杀二肖\s*[】〗]",
        STEADY_KILL_PATTERN,
    )


def parse_yueku_zhixiao_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "月窟织绡" or site.pick != "top" or site.url != YUEKU_ZHIXIAO_URL:
        return []
    return _records_after_heading(
        source,
        r"绝杀料\s*\d{3}\s*期\s*[:：]\s*[【〖]\s*绝杀贰肖\s*[】〗]\s*创造百万\s*月窟织绡",
        ABSOLUTE_KILL_PATTERN,
    )


def parse_jiujie_liangfeng_top_records(source: str, site: Site) -> list[Record]:
    if (
        site.name != "旧街凉风"
        or site.pick != "top"
        or site.url != JIUJIE_LIANGFENG_URL
    ):
        return []
    return _records_after_heading(
        source,
        r"高手料\s*\d{3}\s*期\s*[:：]\s*旧街凉风\s*[【〖]\s*稳杀二肖\s*[】〗]",
        STEADY_KILL_PATTERN,
    )


def parse_bubu_gaosheng_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "步步高升" or site.pick != "top" or site.url != BUBU_GAOSHENG_URL:
        return []
    return _records_after_heading(
        source,
        r"[【〖]\s*稳杀二肖\s*[】〗]\s*步步高升",
        STEADY_KILL_PATTERN,
    )


TIANLANG_KILL_PATTERN = re.compile(
    rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*→\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*←\s*"
    rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
    rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
)


def parse_tianlang_shaxing_bottom_records(source: str, site: Site) -> list[Record]:
    if (
        site.name != "天狼杀星"
        or site.pick != "bottom"
        or site.url != TIANLANG_SHAXING_URL
    ):
        return []
    return _records_after_heading(
        source,
        r"精英贴\s*\d{3}\s*期\s*天狼杀星\s*[【〖]\s*绝杀二肖\s*[】〗]\s*已上料",
        TIANLANG_KILL_PATTERN,
    )

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


def _truncate_at_direction_break(records: list[Record]) -> list[Record]:
    """期数必须保持同一方向；出现反转即认为已跨到别的区块，从反转处截断。

    允许缺期（相邻两条差值可以为 2、3 等），只拒绝方向反转：
    这样既不会把后面别的块的记录并进来，也不会因历史缺期误删有效记录。
    """
    if len(records) < 3:
        return records
    step = 1 if records[1].period > records[0].period else -1
    kept = [records[0], records[1]]
    for record in records[2:]:
        if (record.period - kept[-1].period) * step <= 0:
            break
        kept.append(record)
    return kept


def _records_after_heading(
    source: str,
    heading_pattern: str,
    record_pattern: re.Pattern[str],
    *,
    stop_pattern: str = "",
    stop_at_direction_break: bool = False,
) -> list[Record]:
    if DOCUMENT_BOUNDARY in source:
        return []
    text = html_to_text(source)
    headings = list(re.finditer(heading_pattern, text))
    if len(headings) != 1:
        return []
    heading = headings[0]
    tail = text[heading.end() :]
    if stop_pattern:
        stop = re.search(stop_pattern, tail)
        if stop:
            tail = tail[: stop.start()]
    records = records_from_pattern(tail, record_pattern, base_offset=heading.end())
    if stop_at_direction_break:
        records = _truncate_at_direction_break(records)
    return records


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
    # 站方把标题前缀从「NNN期」改成固定文案「我期」（293 期实测），
    # 标题只用于定位区块；期数与取值仍由 STEADY_KILL_PATTERN 从记录行内取得。
    # 结束边界：记录段之后紧跟「鸿蒙娱乐城APP 授权…」广告区；再加期数方向反转截断，
    # 防止窗口滑到页面下方别的「稳杀二肖」块而取到跨块/跨期的值。
    return _records_after_heading(
        source,
        r"(?:\d{3}\s*期|我期)\s*[:：]\s*49卢九\s*[【〖]\s*稳杀二肖\s*[】〗]\s*优秀推荐",
        STEADY_KILL_PATTERN,
        stop_pattern=r"鸿蒙娱乐城APP|授权\s*[:：]\s*免费软件",
        stop_at_direction_break=True,
    )


def parse_lujiu_home_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "卢九" or site.pick != "top" or site.url != LUJIU_HOME_URL:
        return []
    return _records_after_heading(
        source,
        r"49卢九\s*[【〖]\s*稳杀二肖\s*[】〗]",
        STEADY_KILL_PATTERN,
        stop_pattern=r"鸿蒙娱乐城APP|授权\s*[:：]\s*免费软件",
        stop_at_direction_break=True,
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

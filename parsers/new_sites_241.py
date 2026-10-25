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


MAX_BLOCK_RECORDS = 400
PERIOD_WRAP = 365
# 历史缺期是允许的（站方有时跳过若干期），但同一区块内相邻两条的期数间隔
# 不会大到几十期；超过这个阈值说明已经跨到页面下方另一个区块。
MAX_PERIOD_GAP = 40


def _period_step(previous: int, current: int) -> int:
    """返回相邻两条的期数步进，365->001 视为 +1（跨年回绕）。

    期号体系是 1..365 循环，跨年时 365 的下一期是 001，不能当成方向反转。
    """
    if current > previous:
        return current - previous
    if previous == PERIOD_WRAP and current == 1:
        return 1
    if previous == 1 and current == PERIOD_WRAP:
        return -1
    return current - previous


def _heading_period(heading_text: str) -> int:
    """从标题文本里取期号；取不到返回 0（该站标题不含期号，跳过锚点校验）。"""
    match = re.search(r"(?<!\d)(\d{3})\s*期", heading_text)
    if not match:
        return 0
    period = int(match.group(1))
    return period if 1 <= period <= PERIOD_WRAP else 0


def _truncate_at_direction_break(records: list[Record]) -> list[Record]:
    """只保留「第一个同方向连续段」，遇到方向反转或期数间隔异常即截断。

    判定原则（失败优先，宁缺勿借）：
    - 方向由前两条**不同期数**的记录确定；前两条同期数（重复展示行）不作为基准。
    - 允许历史缺期，但相邻两条的正向间隔不得超过 MAX_PERIOD_GAP：
      间隔过大说明已经跨到页面下方的另一个区块。
    - 365->001 属正常跨年回绕，不算反转。
    """
    if len(records) < 2:
        return records

    # 方向基准：取前两条不同期数的记录，但间隔超限时不认这个方向，
    # 否则「单条 + 远距离邻块」会被邻块带偏方向而整段并入。
    step_sign = 0
    for index in range(1, len(records)):
        delta = _period_step(records[index - 1].period, records[index].period)
        if delta == 0:
            continue
        if abs(delta) > MAX_PERIOD_GAP:
            # 间隔过大：说明第一条之后已经跨块，只保留第一条
            return records[:1]
        step_sign = 1 if delta > 0 else -1
        kept = records[: index + 1]
        start = index + 1
        break
    else:
        # 全部同期数：只保留第一条，避免后续被当成多条候选
        return records[:1]

    for record in records[start:]:
        delta = _period_step(kept[-1].period, record.period)
        if delta * step_sign <= 0:
            break
        if abs(delta) > MAX_PERIOD_GAP:
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
    max_records: int = MAX_BLOCK_RECORDS,
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
        # 只认「记录段开始之后」出现的广告/分隔文案；若它出现在第一条记录之前，
        # 说明标题与记录段之间夹着广告，此时不能整段截空，改为不截断。
        first_record = record_pattern.search(tail)
        search_from = first_record.end() if first_record else 0
        stop = re.search(stop_pattern, tail[search_from:])
        if stop:
            tail = tail[: search_from + stop.start()]
    records = records_from_pattern(tail, record_pattern, base_offset=heading.end())
    if stop_at_direction_break:
        records = _truncate_at_direction_break(records)
    if len(records) > max_records:
        records = records[:max_records]
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
        stop_at_direction_break=True,
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
        stop_at_direction_break=True,
    )


def parse_bubu_gaosheng_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "步步高升" or site.pick != "top" or site.url != BUBU_GAOSHENG_URL:
        return []
    return _records_after_heading(
        source,
        r"[【〖]\s*稳杀二肖\s*[】〗]\s*步步高升",
        STEADY_KILL_PATTERN,
        stop_at_direction_break=True,
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
    # 该站历史记录会跨年回绕（365 -> 001），_truncate_at_direction_break 已把
    # 365->001 归一为 +1，因此跨年不会误截断。
    return _records_after_heading(
        source,
        r"精英贴\s*\d{3}\s*期\s*天狼杀星\s*[【〖]\s*绝杀二肖\s*[】〗]\s*已上料",
        TIANLANG_KILL_PATTERN,
        stop_at_direction_break=True,
    )

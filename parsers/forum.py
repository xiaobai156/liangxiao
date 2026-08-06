from __future__ import annotations

import json
import re

from domain.errors import CandidateConflict, ErrorCategory, ScrapeFailure
from domain.models import DOCUMENT_BOUNDARY, Record, Site
from parsers.helpers import (
    ZODIACS,
    ZODIAC_SET,
    clean_zodiac,
    detail_record_identity,
    html_to_text,
    merge_record_sources,
    records_from_pattern,
    ten_unique_zodiacs,
)


def _linked_source_parts(source: str, anchor_pattern: str) -> tuple[str, str] | None:
    parts = source.split(DOCUMENT_BOUNDARY)
    if len(parts) < 2:
        return None
    anchor = html_to_text(DOCUMENT_BOUNDARY.join(parts[:-1]))
    if not re.search(anchor_pattern, anchor, flags=re.I):
        return None
    return anchor, html_to_text(parts[-1])

def parse_yichou_mozhan_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "一筹莫展" or site.pick != "top":
        return []
    text = html_to_text(source)
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]\s*一筹莫展\s*[「『【]\s*绝杀二肖\s*[」』】]",
        text,
    )
    if not heading:
        return []
    author = re.search(r"一筹莫展\s*发表于", text[heading.end() :])
    if not author:
        return []
    tail = text[heading.end() + author.end() :]
    boundary = re.search(r"(?:上一篇|下一篇|Copyright|免责声明)", tail, flags=re.I)
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀二肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(tail, pattern)


def parse_zhongduo_feiyi_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "众多非一" or site.pick != "top":
        return []
    text = html_to_text(source)
    heading = re.search(
        r"第(?P<period>\d{3})\s*期\s*[:：]\s*众多非一\s*[【〖\[]\s*稳杀二肖\s*[】〗\]]\s*专业服务",
        text,
    )
    if not heading:
        return []
    author = re.search(r"众多非一\s*发表于", text[heading.end() :])
    if not author:
        return []
    tail = text[heading.end() + author.end() :]
    boundary = re.search(r"(?:上一篇|下一篇|Copyright|免责声明)", tail, flags=re.I)
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*稳杀二肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    records = records_from_pattern(tail, pattern)
    heading_period = int(heading.group("period"))
    if not records or records[0].period != heading_period:
        return []
    return records


def parse_jingzhongbaoguo_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if not re.search(r"精忠报国\s*[【\[]\s*定杀二肖\s*[】\]]", text):
        return []
    pattern = re.compile(rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*[【\[]\s*定杀二肖\s*[】\]]\s*[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*开\s*[:：]?\s*(?P<open>[^\s准中错]+)")
    return records_from_pattern(text, pattern)


def parse_nuwabutiantail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if not re.search(r"女娲补天\s*[【\[]\s*稳杀两肖\s*[】\]]", text):
        return []
    pattern = re.compile(rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*稳杀两肖\s*[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*开\s*[:：]?\s*(?P<open>[^\s准中错]+)")
    return records_from_pattern(text, pattern)


def parse_xiangfu_ercheng_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]\s*[【\[]\s*绝杀二肖\s*[】\]]\s*相辅而成",
        text,
    )
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"上一篇\s*[:：]", tail)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*★\s*绝杀二肖\s*★\s*"
        rf"[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错]+)"
    )
    block_start = heading.end()
    records = [
        Record(
            int(match.group("period")),
            clean_zodiac(match.group("zodiac")),
            match.group("open"),
            match.group(0),
            block_start + match.start(),
        )
        for match in pattern.finditer(block)
    ]
    heading_period = int(heading.group("period"))
    if len(records) < 365:
        return []
    current_cycle = records[-365:]
    if heading_period not in {current_cycle[0].period, current_cycle[-1].period}:
        return []
    if any(
        len(record.zodiac) != 2
        or record.zodiac[0] == record.zodiac[1]
        or any(item not in ZODIAC_SET for item in record.zodiac)
        for record in current_cycle
    ):
        return []
    if any(
        current.period != (previous.period % 365) + 1
        for previous, current in zip(current_cycle, current_cycle[1:])
    ):
        return []
    return records


def parse_baishou_qijia_tail_records(source: str, site: Site) -> list[Record]:
    if site.name != "白手起家":
        return []
    text = html_to_text(source)
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]?\s*白手起家\s*【\s*稳杀二肖\s*】",
        text,
    )
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"(?:上一篇|下一篇)\s*[:：]", tail)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*杀二肖\s*"
        rf"【\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*】\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢]+)"
    )
    records: list[Record] = []
    for match in pattern.finditer(block):
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or zodiac[0] == zodiac[1] or any(item not in ZODIAC_SET for item in zodiac):
            return []
        records.append(
            Record(
                int(match.group("period")),
                zodiac,
                match.group("open"),
                match.group(0),
                heading.end() + match.start(),
            )
        )
    if len(records) < 365 or records[-1].period != int(heading.group("period")):
        return []
    current_cycle = records[-365:]
    if any(
        current.period != (previous.period % 365) + 1
        for previous, current in zip(current_cycle, current_cycle[1:])
    ):
        return []
    return records


def parse_taxue_wuhen_bottom_records(source: str, site: Site) -> list[Record]:
    if site.name != "踏雪无痕":
        return []
    text = html_to_text(source)
    heading = re.search(r"澳门\s*踏雪无痕\s*『\s*绝\s*杀\s*二\s*肖\s*』", text)
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"点击投注", tail)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*绝\s*杀\s*二\s*肖\s*"
        rf"【\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*】\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    descending: list[Record] = []
    seen: dict[tuple[int, str], int] = {}
    for match in pattern.finditer(block):
        period = int(match.group("period"))
        zodiac = clean_zodiac(match.group("zodiac"))
        signature = (period, zodiac)
        if len(zodiac) != 2 or zodiac[0] == zodiac[1]:
            return []
        candidate = Record(period, zodiac, match.group("open"), match.group(0), heading.end() + match.start())
        if signature in seen:
            descending[seen[signature]] = merge_record_sources(descending[seen[signature]], candidate)
            continue
        seen[signature] = len(descending)
        descending.append(candidate)
    if len(descending) < 3:
        return []
    if any(current.period != previous.period - 1 for previous, current in zip(descending, descending[1:])):
        return []
    return descending


def parse_taxue_wuhen_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "踏雪无痕" or site.pick != "top":
        return []
    text = html_to_text(source)
    headings = list(
        re.finditer(
            r"澳门\s*踏雪无痕\s*『\s*绝\s*杀\s*二\s*肖\s*』",
            text,
            flags=re.I,
        )
    )
    if len(headings) != 1:
        return []
    heading = headings[0]
    tail = text[heading.end() :]
    boundary = re.search(r"点击投注", tail, flags=re.I)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝\s*杀\s*二\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    records = records_from_pattern(block, pattern, base_offset=heading.end())
    if len(records) < 3:
        return []
    return records


def parse_zhuangyuan_red_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "狀元紅" or site.pick != "top":
        return []
    text = html_to_text(source)
    headings = list(
        re.finditer(
            r"澳门\s*状元红\s+狀元紅\s*⊙\s*[『〖【]\s*绝杀②肖\s*[』〗】]",
            text,
            flags=re.I,
        )
    )
    if len(headings) != 1:
        return []
    heading = headings[0]
    tail = text[heading.end() :]
    boundary = re.search(
        r"永久域名|(?:上一篇|下一篇|Copyright|免责声明|返回首页)",
        tail,
        flags=re.I,
    )
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀②肖\s*❇\s*"
        rf"[〖【]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[〗】]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    records = records_from_pattern(tail, pattern, base_offset=heading.end())
    if not records:
        return []
    return records


def parse_yiben_wanli_sisha_bottom_records(source: str, site: Site) -> list[Record]:
    if site.name != "一本万利死杀":
        return []
    text = html_to_text(source)
    heading = re.search(r"一本万利\s*[【〖\[]\s*绝\s*杀\s*二\s*肖\s*[】〗\]]", text)
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"(?:上一篇|下一篇|返回首页|Copyright|免责声明)", tail, flags=re.I)
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*"
        rf"(?:❄️\s*)?绝\s*杀\s*二\s*肖(?:\s*❄️)?\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)\s*(?:准|中|错|赢|对)\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】〗\]]"
    )
    records: list[Record] = []
    seen: dict[tuple[int, str], int] = {}
    for match in pattern.finditer(tail):
        period = int(match.group("period"))
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or zodiac[0] == zodiac[1]:
            return []
        signature = (period, zodiac)
        candidate = Record(period, zodiac, match.group("open"), match.group(0), heading.end() + match.start())
        if signature in seen:
            records[seen[signature]] = merge_record_sources(records[seen[signature]], candidate)
            continue
        seen[signature] = len(records)
        records.append(candidate)
    if len(records) < 3:
        return []
    return records


def parse_baijie_shujinguang_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "白姐輸盡光":
        return []
    text = html_to_text(source)
    heading = re.search(r"澳门\s*姜太公\s*☛\s*白姐\s*輸\s*盡\s*光\s*☚", text)
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"澳门\s*姜太公\s*☛", tail)
    if not boundary:
        boundary = re.search(r"最具\s*权\s*威\s*港\s*澳六合彩|58\s*倍\s*967\.cc", tail)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})期\s*[【〖\[]\s*白姐\s*[输輸]\s*尽光\s*[】〗\]]\s*"
        rf"[开開](?:[發发])?\s*[^\s]+\s*今期\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[输輸]\s*尽光"
    )
    records: list[Record] = []
    seen: dict[tuple[int, str], int] = {}
    for match in pattern.finditer(block):
        period = int(match.group("period"))
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or zodiac[0] == zodiac[1]:
            return []
        signature = (period, zodiac)
        candidate = Record(period, zodiac, "", match.group(0), heading.end() + match.start())
        if signature in seen:
            records[seen[signature]] = merge_record_sources(records[seen[signature]], candidate)
            continue
        seen[signature] = len(records)
        records.append(candidate)
    if len(records) < 3:
        return []
    if any(
        current.period != previous.period and current.period != previous.period - 1
        for previous, current in zip(records, records[1:])
    ):
        return []
    return records


def parse_liuhe_toutiao_top_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if site.name != "六合头条" or not re.search(r"澳[门門]\s*六合头条|六合头条论坛", text):
        return []
    heading = re.search(r"精品帖\s*\d{3}\s*期\s*[【〖]\s*绝杀两肖\s*[】〗]\s*绝对好料", text)
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*绝杀两肖\s*[【〖]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())


def parse_guangxizai_top_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if site.name != "广西仔" or not re.search(r"澳[门門]\s*广西仔", text):
        return []
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]\s*本站推荐\s*[【〖]\s*绝杀二肖\s*[】〗]",
        text,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*绝杀二肖\s*[-－]\s*[（(]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[）)]\s*[-－]\s*[开開]\s*[:：]?\s*"
        rf"(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())
    if not records or records[0].period != int(heading.group("period")):
        return []
    return records


def parse_guangdong_baerzhan_top_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if site.name != "广东" or not re.search(r"澳[门門]\s*广东八二站|广东八二站", text):
        return []
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]\s*[【〖]\s*绝杀二肖\s*[】〗]",
        text,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[（(]\s*必杀二肖\s*[）)]\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】〗]\s*[开開]\s*[:：]?\s*"
        rf"(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())
    if not records or records[0].period != int(heading.group("period")):
        return []
    return records


def parse_liuhe_toutiao_linked_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "六合头条" or site.pick != "top":
        return []
    parts = _linked_source_parts(source, r"澳[门門]\s*六合头条|六合头条论坛")
    if parts is None:
        return []
    _anchor, text = parts
    heading = re.search(
        r"精品帖\s*(?P<period>\d{3})\s*期\s*[【〖]\s*绝杀两肖\s*[】〗]\s*绝对好料",
        text,
        flags=re.I,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*绝杀两肖\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())
    heading_period = int(heading.group("period"))
    if not records or records[0].period not in {
        heading_period,
        (heading_period - 1) if heading_period > 1 else 365,
    }:
        return []
    return records


def parse_jiulong_forum_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "九龙论坛" or site.pick != "top":
        return []
    text = html_to_text(source)
    heading = re.search(r"九龙论坛\s*『\s*绝杀二肖\s*』", text, flags=re.I)
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"九龙论坛\s*『(?!\s*绝杀二肖\s*』)", tail, flags=re.I)
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*[〖【]\s*杀\s*(?:二|两|2|２|②)\s*肖\s*[→>]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[〗】]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=heading.end())


def parse_shouqi_daoluo_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "手起刀落" or site.pick != "top":
        return []
    text = html_to_text(source)
    heading = re.search(
        r"手起刀落\s*[【〖『]\s*绝杀二肖\s*[】〗』]",
        text,
        flags=re.I,
    )
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(
        r"过分耀眼\s*[【〖『]\s*必杀\s*(?:2|２|二|两)\s*尾",
        tail,
        flags=re.I,
    )
    if boundary:
        tail = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*绝杀两肖\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=heading.end())


def parse_wulin_gaoshou_linked_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "武林高手" or site.pick != "top":
        return []
    parts = _linked_source_parts(
        source,
        r"武林高手\s*(?P<period>\d{3})\s*期\s*《\s*绝杀两肖\s*》",
    )
    if parts is None:
        return []
    anchor, body = parts
    heading = re.search(
        r"武林高手\s*(?P<period>\d{3})\s*期\s*《\s*绝杀两肖\s*》",
        anchor,
        flags=re.I,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀两肖\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(body, pattern)
    if not records or records[0].period != int(heading.group("period")):
        return []
    return records


def parse_guangxizai_linked_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "广西仔" or site.pick != "top":
        return []
    parts = _linked_source_parts(source, r"澳[门門]\s*广西仔")
    if parts is None:
        return []
    _anchor, text = parts
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]\s*本站推荐\s*[【〖]\s*绝杀二肖\s*[】〗]",
        text,
        flags=re.I,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀二肖\s*[-－]\s*[（(]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[）)]\s*[-－]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())
    if not records or records[0].period != int(heading.group("period")):
        return []
    return records


def parse_guangdong_linked_top_records(source: str, site: Site) -> list[Record]:
    if site.name != "广东" or site.pick != "top":
        return []
    parts = _linked_source_parts(source, r"澳[门門]\s*广东八二站|广东八二站")
    if parts is None:
        return []
    _anchor, text = parts
    heading = re.search(
        r"(?P<period>\d{3})\s*期\s*[:：]?\s*[【〖]\s*绝杀二肖\s*[】〗]",
        text,
        flags=re.I,
    )
    if not heading:
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[（(]\s*必杀二肖\s*[）)]\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、，,。.]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    records = records_from_pattern(text[heading.end() :], pattern, base_offset=heading.end())
    if not records or records[0].period != int(heading.group("period")):
        return []
    return records


def parse_dengtang_rushi_bottom_records(source: str, site: Site) -> list[Record]:
    if (
        site.name != "登堂入室"
        or site.pick != "bottom"
        or detail_record_identity(site.url) != "topic.php:912"
    ):
        return []
    text = html_to_text(source)
    heading = re.search(r"澳彩总站\s*[【〖]\s*绝杀二肖\s*[】〗]", text)
    if not heading:
        return []
    tail = text[heading.end() :]
    boundary = re.search(r"提示！|六合优秀站点收录|Copyright", tail, flags=re.I)
    if not boundary:
        return []
    block = tail[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*《\s*登堂入室\s*》\s*🥫\s*"
        rf"绝杀二肖\s*🥫\s*[【\[]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[.。·、，,－-]\s*[{ZODIACS}])\s*[】\]]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    records: list[Record] = []
    seen_periods: dict[int, tuple[str, int]] = {}
    for match in pattern.finditer(block):
        period = int(match.group("period"))
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or zodiac[0] == zodiac[1] or any(item not in ZODIAC_SET for item in zodiac):
            raise ScrapeFailure(ErrorCategory.FIELD_VALIDATION, f"登堂入室{period}期生肖字段无效")
        candidate = Record(period, zodiac, match.group("open"), match.group(0), heading.end() + match.start())
        previous = seen_periods.get(period)
        if previous is not None:
            if previous[0] != zodiac:
                conflict = [
                    *records,
                    candidate,
                ]
                raise CandidateConflict(
                    f"登堂入室{period}期出现{previous[0]}和{zodiac}两个候选",
                    conflict,
                )
            record_index = previous[1]
            records[record_index] = merge_record_sources(records[record_index], candidate)
            continue
        seen_periods[period] = (zodiac, len(records))
        records.append(candidate)
    if len(records) < 10:
        raise ScrapeFailure(ErrorCategory.FIELD_VALIDATION, "登堂入室目标块有效记录不足10条")
    if any(
        current.period != (previous.period % 365) + 1
        for previous, current in zip(records, records[1:])
    ):
        raise ScrapeFailure(ErrorCategory.FIELD_VALIDATION, "登堂入室目标块期数不连续")
    return records


def parse_jinduobao_second_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    if not re.search(r"金多宝\s*[【\[]\s*杀\s*[2２二两]\s*肖\s*[2２二两]\s*尾\s*[】\]]", text):
        return []
    pattern = re.compile(rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*杀\s*[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*肖\s*杀\s*[【\[]\s*\d{{1,2}}\s*[】\]]\s*尾\s*\|?\s*开\s*[:：]?\s*(?P<open>[^\s准中错]+)")
    return records_from_pattern(text, pattern)


def parse_liuhe_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    text = re.split(r"(?:上一篇|下一篇)\s*[:：]", text, maxsplit=1)[0]
    pattern = re.compile(rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*[【\[]\s*绝杀两肖\s*[】\]]\s*[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*[开開]\s*[:：]?\s*(?P<open>[^\s准中错]+)")
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    return records_from_pattern(text, pattern)


def parse_nuyanmeigu_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    boundary = re.search(r"站长宣言\s*[:：]", text)
    if not boundary:
        return []
    text = text[: boundary.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]\s*绝杀二肖\s*"
        rf"[【\[]\s*(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[】\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错]+)"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    return records_from_pattern(text, pattern)


def parse_ziranziran_top_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    anchor_pattern = re.compile(r"\d{3}\s*期\s*[:：]\s*生财有道\s*[【\[]\s*绝杀两肖\s*[】\]]\s*免费(?:公开|公開)\s*自然而然")
    anchor = anchor_pattern.search(text)
    if not anchor:
        return []
    tail = text[anchor.end() :]
    next_anchor = anchor_pattern.search(tail)
    if next_anchor:
        tail = tail[: next_anchor.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*《\s*绝杀两肖\s*》\s*[☆★]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[{ZODIACS}])\s*[☆★]\s*开\s*"
        rf"(?P<open>[^\s准中错]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=anchor.end())


def parse_liuhebadian_tail_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"六合宝典\s*『\s*杀\s*二\s*肖\s*[一二两2２]?\s*尾\s*』", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"六合宝典\s*『(?!\s*杀\s*二\s*肖\s*[一二两2２]?\s*尾\s*』)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]

    pattern = re.compile(
        rf"第?\s*(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝\s*杀\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"\+\s*\d+\s*尾\s*[】〗]\s*[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_wuyou_wulv_topic_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"\d{3}\s*期\s*[【〖]\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*[】〗]\s*作者\s*[:：]?\s*无忧无虑", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"(?:稳赚一肖|①肖①码|三期六码|Copyright|返回首页)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*➹\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*➹\s*_\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"_\s*[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_bufeng_zhuoying_topic_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"\d{3}\s*期\s*[:：]\s*[【〖]\s*绝杀二肖\s*[】〗]\s*独家提供\s*作者\s*[:：]?\s*捕风捉影", text)
    if not start:
        return []
    tail = text[start.start() :]
    boundary_positions: list[int] = []
    stop = re.search(r"(?:上一篇|下一篇|★★|Copyright)", tail)
    if stop:
        boundary_positions.append(stop.start())
    body = tail[len(start.group(0)) :]
    next_heading = re.compile(
        r"\d{3}\s*期\s*[:：]\s*[【〖]\s*绝杀二肖\s*[】〗]\s*独家提供\s*"
        r"作者\s*[:：]?\s*(?P<author>[^\s]+)"
    )
    for match in next_heading.finditer(body):
        if match.group("author") != "捕风捉影":
            boundary_positions.append(len(start.group(0)) + match.start())
            break
    if boundary_positions:
        tail = tail[: min(boundary_positions)]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_zhenlong_fankui_topic_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"\d{3}\s*期\s*[:：]\s*鬼谷子\s*[【〖]\s*猛杀二肖\s*[】〗].{0,80}?作者\s*[:：]?\s*振聋发聩", text)
    if not start:
        return []
    tail = text[start.start() :]
    boundary_positions: list[int] = []
    stop = re.search(r"(?:鸿蒙娱乐城|澳门永利|Android版|iPhone版|Copyright)", tail)
    if stop:
        boundary_positions.append(stop.start())
    body = tail[len(start.group(0)) :]
    next_heading = re.compile(
        r"\d{3}\s*期\s*[:：]\s*鬼谷子\s*[【〖]\s*猛杀二肖\s*[】〗].{0,80}?"
        r"作者\s*[:：]?\s*(?P<author>[^\s]+)"
    )
    for match in next_heading.finditer(body):
        if match.group("author") != "振聋发聩":
            boundary_positions.append(len(start.group(0)) + match.start())
            break
    if boundary_positions:
        tail = tail[: min(boundary_positions)]
    else:
        tail = tail[:2000]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*杀\s*[２2二两②]\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"[-－—]*\s*开\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_gaohuo_zhifei_topic_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"绝杀料\s*\d{3}\s*期\s*[:：]\s*[【〖]\s*稳杀二肖\s*[】〗]\s*作者\s*[:：]?\s*膏火之费", text)
    if not start:
        return []
    tail = text[start.start() :]
    boundary_positions: list[int] = []
    stop = re.search(r"(?:★★|博彩必备|点击进入官网投注|Copyright)", tail)
    if stop:
        boundary_positions.append(stop.start())
    body = tail[len(start.group(0)) :]
    next_heading = re.compile(
        r"绝杀料\s*\d{3}\s*期\s*[:：]\s*[【〖]\s*稳杀二肖\s*[】〗]\s*"
        r"作者\s*[:：]?\s*(?P<author>[^\s]+)"
    )
    for match in next_heading.finditer(body):
        if match.group("author") != "膏火之费":
            boundary_positions.append(len(start.group(0)) + match.start())
            break
    if boundary_positions:
        tail = tail[: min(boundary_positions)]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*☆\s*稳\s*杀\s*(?:二|两|2|２|②)\s*肖\s*☆\s*"
        rf"[（(]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[）)]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())

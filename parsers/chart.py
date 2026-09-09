from __future__ import annotations

import json
import re
from dataclasses import replace

from domain.errors import CandidateConflict
from domain.models import Record, Site
from parsers.helpers import (
    ZODIACS,
    ZODIAC_SET,
    clean_zodiac,
    html_to_text,
    records_from_pattern,
    ten_unique_zodiacs,
)

def parse_wangzhejiudian_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"《\s*王者九点网\s*㊣\s*杀\s*二\s*肖\s*》", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"《\s*王者九点网\s*㊣(?!\s*杀\s*二\s*肖\s*》)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]

    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*杀\s*二\s*肖\s*"
        rf"[【〖]\s*★?\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*★?\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_wangzhejiudian_stat_chart_records(source: str, field_name: str) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"《\s*王者九点网\s*㊣\s*杀\s*禁\s*统计\s*》", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"《\s*王者九点网\s*㊣(?!\s*杀\s*禁\s*统计\s*》)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]

    records: list[Record] = []
    seen: dict[tuple[int, str, int], int] = {}
    period_pattern = re.compile(r"(?P<period>\d{3})\s*期\s*杀\s*肖\s*统计")
    starts = list(period_pattern.finditer(tail))
    field_pattern = re.compile(
        rf"{re.escape(field_name)}\s*[:：]\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗]"
    )
    for index, match in enumerate(starts):
        period = int(match.group("period"))
        end = starts[index + 1].start() if index + 1 < len(starts) else len(tail)
        chunk = tail[match.start() : end]
        open_match = re.search(r"特\s*开\s*(?P<open>[^)\s]+)", chunk)
        for field in field_pattern.finditer(chunk):
            zodiac = clean_zodiac(field.group("zodiac"))
            if len(zodiac) != 2 or zodiac[0] == zodiac[1] or any(item not in ZODIAC_SET for item in zodiac):
                continue
            candidate = Record(
                period,
                zodiac,
                open_match.group("open") if open_match else "",
                field.group(0),
                start.start() + match.start() + field.start(),
            )
            signature = (period, zodiac, candidate.position)
            if signature in seen:
                continue
            seen[signature] = len(records)
            records.append(candidate)
    return records


def parse_shuqhbq_forbidden_records(source: str, field_name: str) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"禁\s*两\s*肖", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"(?:短期规律专区|长期规律专区|热门专区|精品专区|精华帖)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]

    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*{re.escape(field_name)}\s*[【〖]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"特\s*[开開]\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def parse_zhuque_forbidden_material_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"177792a\.com\s*『\s*禁\s*用\s*资\s*料\s*』|禁\s*用\s*资\s*料", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"(?:禁\s*两\s*肖|短期规律专区|长期规律专区|热门专区|精品专区)", tail[len(start.group(0)) :])
    if stop:
        tail = tail[: len(start.group(0)) + stop.start()]

    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[→>＞\-—–✈]*\s*禁\s*用\s*二\s*肖\s*[→>＞\-—–✈]*\s*"
        rf"[【〖]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗]\s*"
        rf"[开開]\s*(?P<open>[^\s准中错赢对]+)"
    )
    return records_from_pattern(tail, pattern, base_offset=start.start())


def forum_snapshot_period(item: dict[str, object]) -> int | None:
    try:
        draw = int(item.get("draw"))
    except (TypeError, ValueError):
        draw = 0
    if 1 <= draw <= 365:
        return draw
    topic_match = re.search(r"(?<!\d)(\d{3})\s*期", str(item.get("topic") or ""))
    return int(topic_match.group(1)) if topic_match else None


def records_from_forum_snapshots(
    items: list[object],
    site: Site,
    matches_topic,
    pattern: re.Pattern[str],
) -> list[Record]:
    snapshots: list[tuple[int, int | None, list[Record]]] = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict) or not matches_topic(item):
            continue
        records = records_from_pattern(html_to_text(str(item.get("content") or "")), pattern)
        if records:
            snapshots.append((item_index, forum_snapshot_period(item), records))
    if not snapshots:
        return []

    primary_index, primary_period, primary_records = snapshots[0]
    primary_values: dict[int, set[str]] = {}
    primary_by_period: dict[int, list[Record]] = {}
    for record in primary_records:
        primary_values.setdefault(record.period, set()).add(clean_zodiac(record.zodiac))
        primary_by_period.setdefault(record.period, []).append(record)
    conflicting_records: list[Record] = []
    conflicting_periods: set[int] = set()
    for _snapshot_index, snapshot_period, records in snapshots[1:]:
        if snapshot_period != primary_period:
            continue
        for record in records:
            expected = primary_values.get(record.period)
            signature = clean_zodiac(record.zodiac)
            if expected is not None and signature not in expected:
                conflicting_periods.add(record.period)
                conflicting_records.extend(primary_by_period[record.period])
                conflicting_records.append(record)
    if conflicting_records:
        details = "；".join(
            f"{period}期 "
            + "、".join(
                sorted(
                    {
                        clean_zodiac(record.zodiac)
                        for record in conflicting_records
                        if record.period == period
                    }
                )
            )
            for period in sorted(conflicting_periods)
        )
        unique_conflicts: list[Record] = []
        seen_conflicts: set[tuple[int, str, int]] = set()
        for record in conflicting_records:
            key = (record.period, clean_zodiac(record.zodiac), record.position)
            if key not in seen_conflicts:
                seen_conflicts.add(key)
                unique_conflicts.append(record)
        remaining = [record for record in primary_records if record.period not in conflicting_periods]
        raise CandidateConflict(f"用户论坛同一快照结果不同：{details}", unique_conflicts + remaining)
    return [
        replace(
            record,
            record_path=f"root[{primary_index}].content",
            record_count=1,
            source_positions=(record.position,),
        )
        for record in primary_records
    ]


def parse_tuku_user_forums_two_zodiac_records(source: str, site: Site) -> list[Record]:
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    topic_title = site.title or "绝杀二肖"
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_forum_snapshots(
        items,
        site,
        lambda item: str(item.get("topic") or "").strip() == topic_title,
        pattern,
    )


def parse_tuku_user_forums_short_kill_records(source: str, site: Site) -> list[Record]:
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*杀\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*(?P<open>[{ZODIACS}？?0-9０-９]*)"
    )
    return records_from_forum_snapshots(
        items,
        site,
        lambda item: "杀" in str(item.get("topic") or "")
        and not any(word in str(item.get("topic") or "") for word in ("三肖", "六码", "波", "尾")),
        pattern,
    )


def parse_tuku_user_forums_precise_two_zodiac_records(source: str, site: Site) -> list[Record]:
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*精\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_forum_snapshots(
        items,
        site,
        lambda item: "精杀二肖" in str(item.get("topic") or "")
        and "三肖" not in str(item.get("topic") or ""),
        pattern,
    )


def parse_tuku_user_forums_shizhuang_cut_records(source: str, site: Site) -> list[Record]:
    try:
        items = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*砍\s*[-－]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])"
        rf"\s*(?P<open>[✓✔√]?)"
    )
    records: list[Record] = []
    seen: dict[tuple[int, str, int], int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "")
        nickname = str((item.get("user") or {}).get("nickname") if isinstance(item.get("user"), dict) else "")
        if site.name not in nickname:
            continue
        if "砍" not in topic or "②肖" not in topic:
            continue
        text = html_to_text(str(item.get("content") or ""))
        for match in pattern.finditer(text):
            zodiac = clean_zodiac(match.group("zodiac"))
            if len(zodiac) != 2 or any(value not in ZODIAC_SET for value in zodiac):
                continue
            period = int(match.group("period"))
            candidate = Record(period, zodiac, match.group("open"), match.group(0), match.start())
            signature = (period, zodiac, candidate.position)
            if signature in seen:
                continue
            seen[signature] = len(records)
            records.append(candidate)
    return records


def parse_laodazhu_ten_zodiac_complement_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = text.find("【老大主十肖】")
    if start < 0:
        return []
    end = text.find("【老二主四头】", start)
    block = text[start:] if end < 0 else text[start:end]
    pattern = re.compile(rf"(?P<period>\d{{3}})\s*期\s*[【〖\[]\s*(?P<body>[{ZODIACS}\s]+)\s*[】〗\]]")
    records: list[Record] = []
    seen: dict[tuple[int, str, int], int] = {}
    for match in pattern.finditer(block):
        period = int(match.group("period"))
        picked = ten_unique_zodiacs(match.group("body"))
        if len(picked) != 10:
            continue
        missing = "".join(zodiac for zodiac in ZODIACS if zodiac not in picked)
        if len(missing) != 2:
            continue
        candidate = Record(period, missing, "", match.group(0), start + match.start())
        signature = (period, missing, candidate.position)
        if signature in seen:
            continue
        seen[signature] = len(records)
        records.append(candidate)
    return records


def parse_suiyin_jiliang_ten_zodiac_complement_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    start = re.search(r"\d{3}\s*期\s*[:：]\s*碎银几两\s*[【〖]\s*必中十肖\s*[】〗].{0,80}?作者\s*[:：]\s*碎银几两", text)
    if not start:
        return []
    tail = text[start.start() :]
    stop = re.search(r"(?:上一篇|下一篇|Copyright|免责声明|返回首页)", tail)
    if stop:
        tail = tail[: stop.start()]
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[【〖]\s*必中十肖\s*[】〗]\s*"
        rf"[【〖]\s*(?P<body>[{ZODIACS}\s]+)\s*[】〗]\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    records: list[Record] = []
    seen: dict[tuple[int, str, int], int] = {}
    for match in pattern.finditer(tail):
        period = int(match.group("period"))
        picked = ten_unique_zodiacs(match.group("body"))
        if len(picked) != 10:
            continue
        missing = "".join(zodiac for zodiac in ZODIACS if zodiac not in picked)
        if len(missing) != 2:
            continue
        candidate = Record(period, missing, match.group("open"), match.group(0), start.start() + match.start())
        signature = (period, missing, candidate.position)
        if signature in seen:
            continue
        seen[signature] = len(records)
        records.append(candidate)
    return records

def parse_wangzhejiudian_forbidden_chart_records(source: str, site: Site) -> list[Record]:
    return parse_wangzhejiudian_stat_chart_records(source, "禁二肖图")


def parse_wangzhejiudian_kill_chart_records(source: str, site: Site) -> list[Record]:
    return parse_wangzhejiudian_stat_chart_records(source, "杀二肖图")


def parse_shuqhbq_xuanji_forbidden_records(source: str, site: Site) -> list[Record]:
    return parse_shuqhbq_forbidden_records(source, "玄机网禁两肖")


def parse_shuqhbq_macau_kill_chart_records(source: str, site: Site) -> list[Record]:
    return parse_shuqhbq_forbidden_records(source, "澳门杀两肖图")


def parse_shuqhbq_macau_gallery_forbidden_records(source: str, site: Site) -> list[Record]:
    return parse_shuqhbq_forbidden_records(source, "澳门图库禁肖")

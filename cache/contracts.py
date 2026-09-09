from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from domain.models import POSITION_KIND_VISIBLE_TEXT, Record, Site
from validation.records import normalize_zodiac, validate_selected_record

CACHE_SCHEMA_VERSION = 2
CACHE_POSITION_KIND = POSITION_KIND_VISIBLE_TEXT
CACHE_WINDOW_SIZE = 10
CONFIG_FINGERPRINT_NAMESPACE = "杀两肖-V2"
CONFIG_FINGERPRINT_FIELDS = (
    "name",
    "pick",
    "url",
    "parser",
    "title",
    "record",
    "stop",
    "payload",
    "api_url",
    "keywords",
    "profile_id",
    "linked_document_pattern",
)


def recent_periods(current_period: int, count: int = CACHE_WINDOW_SIZE) -> list[int]:
    if not 1 <= current_period <= 365:
        raise ValueError(f"期数必须在1-365内：{current_period}")
    if count < 1:
        return []
    return [((current_period - offset - 1) % 365) + 1 for offset in range(count)]


def validate_issue_window(raw_issues: object) -> list[int]:
    if not isinstance(raw_issues, list) or len(raw_issues) != CACHE_WINDOW_SIZE:
        raise ValueError("缓存文件期数窗口必须恰好包含10期，已拒绝判定")
    if any(isinstance(period, bool) or not isinstance(period, int) for period in raw_issues):
        raise ValueError("缓存文件期数窗口格式无效，已拒绝判定")
    issues = list(raw_issues)
    if len(set(issues)) != CACHE_WINDOW_SIZE or issues != recent_periods(issues[0]):
        raise ValueError("缓存文件期数窗口必须为连续倒序10期，已拒绝判定")
    return issues


def config_fingerprint(sites: list[Site]) -> str:
    definitions: list[dict[str, object]] = []
    for site in sites:
        definition = {
            field: list(getattr(site, field)) if field == "keywords" else getattr(site, field)
            for field in CONFIG_FINGERPRINT_FIELDS
        }
        for field in ("allowed_redirect_origins", "allowed_document_origins"):
            value = getattr(site, field)
            if value:
                definition[field] = list(value)
        definitions.append(definition)
    payload = {"project": CONFIG_FINGERPRINT_NAMESPACE, "sites": definitions}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cache_identity(entry: Mapping[str, object], index: int) -> tuple[str, str, str]:
    name = entry.get("name")
    url = entry.get("url")
    pick = entry.get("pick")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"缓存文件第{index + 1}个站点身份无效：name不能为空")
    if not isinstance(url, str) or not url.strip():
        raise ValueError(f"缓存文件第{index + 1}个站点身份无效：url不能为空")
    if pick not in {"top", "bottom"}:
        raise ValueError(f"缓存文件第{index + 1}个站点身份无效：pick必须为top或bottom")
    return name, url, pick


def _period_value(raw_period: object, field: str, name: str) -> int:
    if isinstance(raw_period, bool):
        raise ValueError(f"缓存位置结构无效：{name} {field}期号必须为1-365整数")
    if isinstance(raw_period, int):
        period = raw_period
    elif isinstance(raw_period, str) and re.fullmatch(r"[0-9]+", raw_period):
        period = int(raw_period)
    else:
        raise ValueError(f"缓存位置结构无效：{name} {field}期号必须为1-365整数")
    if not 1 <= period <= 365:
        raise ValueError(f"缓存位置结构无效：{name} {period}期号必须在1-365内")
    return period


def _validated_zodiac(value: object, period: int, field: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"缓存位置结构无效：{name} {period}期{field} zodiac不是字符串")
    candidate = Record(
        period=period,
        zodiac=value,
        open_result="",
        raw="",
        position=0,
        position_kind=CACHE_POSITION_KIND,
    )
    if not validate_selected_record(candidate, period):
        raise ValueError(f"缓存位置结构无效：{name} {period}期{field} zodiac不符合两肖合法值")
    return normalize_zodiac(value)


def _period_mapping(
    entry: Mapping[str, object], field: str, name: str, issue_set: set[int]
) -> dict[int, object]:
    raw = entry.get(field, {})
    if not isinstance(raw, Mapping):
        raise ValueError(f"缓存位置结构无效：{name} {field}必须为对象")
    values: dict[int, object] = {}
    for raw_period, value in raw.items():
        period = _period_value(raw_period, field, name)
        if period not in issue_set:
            raise ValueError(f"缓存位置结构无效：{name} {period}期号不在issues窗口")
        if period in values:
            raise ValueError(f"缓存位置结构无效：{name} {field}包含重复期数{period}")
        if field == "values":
            _validated_zodiac(value, period, field, name)
        values[period] = value
    return values


def _validate_entry_positions(entry: Mapping[str, object], name: str, issue_set: set[int]) -> None:
    values = _period_mapping(entry, "values", name, issue_set)
    positions = _period_mapping(entry, "positions", name, issue_set)
    if set(values) != set(positions):
        raise ValueError(f"缓存位置结构无效：{name} values与positions期数不一致")
    for period, position in positions.items():
        if isinstance(position, bool) or not isinstance(position, int) or position < 0:
            raise ValueError(f"缓存位置结构无效：{name} {period}期position必须为非负整数")

    raw_records = entry.get("records", [])
    if not isinstance(raw_records, list):
        raise ValueError(f"缓存位置结构无效：{name} records必须为列表")
    records: dict[int, Mapping[str, object]] = {}
    for record in raw_records:
        if not isinstance(record, Mapping) or record.get("period") is None:
            raise ValueError(f"缓存位置结构无效：{name} records记录无效")
        period = _period_value(record["period"], "records", name)
        record_zodiac = _validated_zodiac(record.get("zodiac"), period, "records", name)
        if period in records:
            raise ValueError(f"缓存位置结构无效：{name} records包含重复期数{period}")
        if period not in values:
            raise ValueError(f"缓存位置结构无效：{name} records包含孤立期数{period}")
        record_position = record.get("position")
        if isinstance(record_position, bool) or not isinstance(record_position, int) or record_position < 0:
            raise ValueError(f"缓存位置结构无效：{name} {period}期记录position必须为非负整数")
        if record.get("position_kind") != CACHE_POSITION_KIND:
            raise ValueError(f"缓存位置语义不兼容：{name} {period}期记录缺少统一position_kind；已拒绝混写")
        if record_zodiac != normalize_zodiac(values[period]) or record_position != positions[period]:
            raise ValueError(f"缓存位置结构无效：{name} {period}期记录与values/positions不匹配")
        records[period] = record
    if set(records) != set(values):
        raise ValueError(f"缓存位置结构无效：{name} values缺少匹配records记录")


def validate_cache_position_contract(cache: Mapping[str, object]) -> None:
    schema = cache.get("schema")
    position_kind = str(cache.get("position_kind") or "")
    if schema != CACHE_SCHEMA_VERSION or position_kind != CACHE_POSITION_KIND:
        raise ValueError(
            "缓存位置语义不兼容："
            f"当前schema={schema!r}、position_kind={position_kind or '缺失'}；"
            f"正式链要求schema={CACHE_SCHEMA_VERSION}、position_kind={CACHE_POSITION_KIND}。"
            "旧缓存只能只读审计，未经确认迁移不得混写"
        )
    if cache.get("window_size") != CACHE_WINDOW_SIZE:
        raise ValueError("缓存文件window_size必须为10，已拒绝判定")
    # config_fingerprint 仅保留历史信息，不再绑定缓存与当前配置。
    issues = validate_issue_window(cache.get("issues"))
    issue_set = set(issues)

    raw_sites = cache.get("sites")
    if not isinstance(raw_sites, list):
        raise ValueError("缓存文件站点列表结构无效，已拒绝判定")
    identities: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(raw_sites):
        if not isinstance(entry, Mapping):
            raise ValueError(f"缓存文件第{index + 1}个站点结构无效，已拒绝判定")
        identity = cache_identity(entry, index)
        if identity in identities:
            raise ValueError(f"缓存文件包含重复站点身份：{identity[0]} {identity[1]} {identity[2]}")
        identities.add(identity)
        name = identity[0]
        entry_kind = str(entry.get("position_kind") or "")
        if entry_kind != CACHE_POSITION_KIND:
            raise ValueError(
                f"缓存位置语义不兼容：{name} position_kind={entry_kind or '缺失'}，"
                f"要求{CACHE_POSITION_KIND}；已拒绝混写"
            )
        _validate_entry_positions(entry, name, issue_set)

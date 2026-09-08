from __future__ import annotations

from collections.abc import Mapping

from cache.contracts import (
    cache_identity,
    config_fingerprint,
    validate_cache_position_contract,
)
from domain.models import CacheCoverage, DuplicateFinding, Site
from validation.records import normalize_zodiac


def cache_key(name: str, url: str, pick: str) -> tuple[str, str, str]:
    return name, url, pick


def values_from_entry(entry: Mapping[str, object]) -> dict[str, str]:
    values = entry.get("values")
    if isinstance(values, dict):
        return {str(period): str(value) for period, value in values.items() if value}
    records = entry.get("records")
    if not isinstance(records, list):
        return {}
    result: dict[str, str] = {}
    for record in records:
        if isinstance(record, dict) and record.get("period") is not None and record.get("zodiac"):
            result[str(record["period"])] = str(record["zodiac"])
    return result


def positions_from_entry(entry: Mapping[str, object]) -> dict[str, int]:
    positions = entry.get("positions")
    if isinstance(positions, dict):
        result: dict[str, int] = {}
        for period, value in positions.items():
            try:
                result[str(period)] = int(value)
            except (TypeError, ValueError):
                continue
        return result
    records = entry.get("records")
    if not isinstance(records, list):
        return {}
    result = {}
    for record in records:
        if not isinstance(record, dict) or record.get("period") is None or record.get("position") is None:
            continue
        try:
            result[str(record["period"])] = int(record["position"])
        except (TypeError, ValueError):
            continue
    return result


def article_ids_from_entry(entry: Mapping[str, object]) -> dict[str, str]:
    article_ids = entry.get("article_ids")
    if not isinstance(article_ids, dict):
        return {}
    return {str(period): str(value) for period, value in article_ids.items() if value}


def detect_duplicate_findings(cache: Mapping[str, object]) -> list[DuplicateFinding]:
    validate_cache_position_contract(cache)
    raw_issues = cache.get("issues")
    issues = list(dict.fromkeys(int(period) for period in raw_issues)) if isinstance(raw_issues, list) else []
    raw_entries = cache.get("sites")
    entries = [entry for entry in raw_entries if isinstance(entry, dict)] if isinstance(raw_entries, list) else []
    findings: list[DuplicateFinding] = []
    for left_index, left in enumerate(entries):
        left_values = values_from_entry(left)
        left_positions = positions_from_entry(left)
        for right in entries[left_index + 1 :]:
            right_values = values_from_entry(right)
            right_positions = positions_from_entry(right)
            current: list[int] = []
            longest: list[int] = []
            previous: int | None = None
            for period in issues:
                if previous is not None and period != ((previous - 2) % 365) + 1:
                    current = []
                key = str(period)
                left_position = left_positions.get(key, -1)
                right_position = right_positions.get(key, -1)
                matches = (
                    key in left_values
                    and key in right_values
                    and normalize_zodiac(left_values[key]) == normalize_zodiac(right_values[key])
                    and left_position >= 0
                    and left_position == right_position
                )
                current = [*current, period] if matches else []
                if len(current) > len(longest):
                    longest = current
                previous = period
            if len(longest) >= 3:
                findings.append(
                    DuplicateFinding(
                        str(left.get("name") or ""),
                        str(right.get("name") or ""),
                        longest[0],
                        longest[-1],
                        len(longest),
                        "duplicate" if len(longest) >= 6 else "suspicious",
                    )
                )
    return findings


def audit_cache_coverage(cache: Mapping[str, object], sites: list[Site]) -> CacheCoverage:
    validate_cache_position_contract(cache)
    raw_entries = cache.get("sites")
    if not isinstance(raw_entries, list):
        raise ValueError("缓存文件站点列表结构无效，已拒绝判定")
    cache_keys = [
        cache_identity(entry, index)
        for index, entry in enumerate(raw_entries)
        if isinstance(entry, Mapping)
    ]
    if len(cache_keys) != len(raw_entries):
        raise ValueError("缓存文件站点结构无效，已拒绝判定")
    site_keys = [(site.name, site.url, site.pick) for site in sites]
    if cache_keys != site_keys:
        raise ValueError("缓存站点身份或顺序与当前配置不一致，已拒绝判定")
    if cache.get("config_fingerprint") != config_fingerprint(sites):
        raise ValueError("缓存config_fingerprint缺失或不匹配，已拒绝判定")
    raw_issues = cache.get("issues")
    issues = list(dict.fromkeys(int(period) for period in raw_issues))[:10] if isinstance(raw_issues, list) else []
    entries: dict[tuple[str, str, str], Mapping[str, object]] = {}
    for entry in raw_entries:
        assert isinstance(entry, Mapping)
        entries[cache_key(str(entry["name"]), str(entry["url"]), str(entry["pick"]))] = entry
    missing: list[str] = []
    incomplete: list[str] = []
    for site in sites:
        entry = entries.get(cache_key(site.name, site.url, site.pick))
        if entry is None:
            missing.append(site.name)
            continue
        values = values_from_entry(entry)
        positions = positions_from_entry(entry)
        valid = [period for period in issues if str(period) in values and positions.get(str(period), -1) >= 0]
        if len(issues) < 10 or len(valid) < 10:
            incomplete.append(site.name)
    return CacheCoverage(tuple(missing), tuple(incomplete))

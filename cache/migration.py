from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from cache.contracts import CACHE_POSITION_KIND
from cache.duplicates import article_ids_from_entry, values_from_entry
from cache.repository import recent_periods
from domain.models import HistoryResult, Site
from validation.records import normalize_zodiac


def _legacy_entries(cache: Mapping[str, object]) -> dict[tuple[str, str, str], Mapping[str, object]]:
    if cache.get("schema") != 1:
        raise ValueError("只允许从schema:1缓存执行只读迁移")
    raw_sites = cache.get("sites")
    if not isinstance(raw_sites, list):
        raise ValueError("旧缓存站点列表结构无效，拒绝迁移")
    entries: dict[tuple[str, str, str], Mapping[str, object]] = {}
    for entry in raw_sites:
        if not isinstance(entry, Mapping):
            raise ValueError("旧缓存站点结构无效，拒绝迁移")
        key = (
            str(entry.get("name") or ""),
            str(entry.get("url") or ""),
            str(entry.get("pick") or "top").lower(),
        )
        if key in entries:
            raise ValueError(f"旧缓存包含重复站点，拒绝迁移：{key[0]}")
        entries[key] = entry
    return entries


def _empty_entry(result: HistoryResult, error: str) -> dict[str, object]:
    return {
        "name": result.site.name,
        "url": result.site.url,
        "pick": result.site.pick,
        "position_kind": CACHE_POSITION_KIND,
        "values": {},
        "positions": {},
        "source_positions": {},
        "records": [],
        "fingerprint": "",
        "error": error,
    }


def migrate_legacy_cache(
    legacy: Mapping[str, object],
    results: Sequence[HistoryResult],
    current_period: int,
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Build a schema-2 cache without treating legacy candidate indexes as offsets.

    A complete live history is required for a populated entry. Any old-value or
    dynamic-article-ID disagreement quarantines the whole site entry instead of
    choosing either side. The caller decides when the returned payload may be
    committed.
    """
    old_entries = _legacy_entries(legacy)
    issues = recent_periods(current_period)
    issue_keys = {str(period) for period in issues}
    seen: set[tuple[str, str, str]] = set()
    output_sites: list[dict[str, object]] = []
    conflicts: list[str] = []

    for result in results:
        key = (result.site.name, result.site.url, result.site.pick)
        if key in seen:
            raise ValueError(f"历史抓取结果包含重复站点，拒绝迁移：{result.site.name}")
        seen.add(key)
        old = old_entries.get(key, {})
        old_values = values_from_entry(old)
        old_article_ids = article_ids_from_entry(old)
        complete = (
            result.ok
            and len(result.records) == len(issues)
            and [record.period for record in result.records] == issues
            and all(record.position >= 0 for record in result.records)
            and all(record.position_kind == CACHE_POSITION_KIND for record in result.records)
        )
        if not complete:
            output_sites.append(
                _empty_entry(
                    result,
                    result.error or "历史抓取未完整通过，已隔离且未迁移旧值",
                )
            )
            continue

        value_conflicts: list[str] = []
        article_conflicts: list[str] = []
        for record in result.records:
            period = str(record.period)
            old_value = old_values.get(period)
            if old_value is not None and normalize_zodiac(old_value) != normalize_zodiac(record.zodiac):
                value_conflicts.append(f"{record.period}期旧={old_value}新={record.zodiac}")
            old_article_id = old_article_ids.get(period)
            if old_article_id and old_article_id != record.record_id:
                article_conflicts.append(
                    f"{record.period}期旧ID={old_article_id}新ID={record.record_id or '缺失'}"
                )
        if value_conflicts or article_conflicts:
            details = [*value_conflicts, *article_conflicts]
            conflict_kind = "生肖冲突" if value_conflicts else "文章ID冲突"
            message = f"缓存迁移冲突（{conflict_kind}）：{'；'.join(details)}；旧position未比较"
            conflicts.append(f"{result.site.name}：{message}")
            output_sites.append(_empty_entry(result, message))
            continue

        values = {str(record.period): record.zodiac for record in result.records if str(record.period) in issue_keys}
        positions = {str(record.period): record.position for record in result.records if str(record.period) in issue_keys}
        source_positions = {
            str(record.period): list(record.source_positions or (record.position,))
            for record in result.records
            if str(record.period) in issue_keys
        }
        records = [
            {
                "period": record.period,
                "zodiac": record.zodiac,
                "position": record.position,
                "source_positions": source_positions[str(record.period)],
                "position_kind": CACHE_POSITION_KIND,
            }
            for record in result.records
            if str(record.period) in issue_keys
        ]
        entry: dict[str, object] = {
            "name": result.site.name,
            "url": result.site.url,
            "pick": result.site.pick,
            "position_kind": CACHE_POSITION_KIND,
            "values": values,
            "positions": positions,
            "source_positions": source_positions,
            "records": records,
            "fingerprint": "".join(values[str(period)] for period in issues),
        }
        article_ids = {
            str(record.period): record.record_id
            for record in result.records
            if result.site.payload == "admin_article_api" and record.record_id
        }
        if article_ids:
            entry["article_ids"] = article_ids
        output_sites.append(entry)

    for key, old in old_entries.items():
        if key in seen:
            continue
        missing_site = Site(
            name=key[0] or str(old.get("name") or "未知站点"),
            pick=key[2],
            url=key[1],
        )
        output_sites.append(
            _empty_entry(
                HistoryResult(missing_site, (), "未获得真实历史抓取结果，旧缓存已隔离且未迁移"),
                "未获得真实历史抓取结果，旧缓存已隔离且未迁移",
            )
        )

    return (
        {
            "schema": 2,
            "position_kind": CACHE_POSITION_KIND,
            "description": "杀两肖重复检测最近10期基准数据；由真实历史迁移生成，旧候选编号未迁移。",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "window_size": 10,
            "issues": issues,
            "sites": output_sites,
        },
        tuple(conflicts),
    )

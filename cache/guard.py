from __future__ import annotations

from collections.abc import Mapping

from cache.contracts import CACHE_POSITION_KIND, validate_cache_position_contract
from cache.duplicates import article_ids_from_entry, cache_key, positions_from_entry, values_from_entry
from domain.models import Result
from validation.records import normalize_zodiac


def guard_results_against_cache(
    results: list[Result],
    cache: Mapping[str, object],
    current_period: int,
) -> list[Result]:
    validate_cache_position_contract(cache)
    raw_entries = cache.get("sites")
    if not isinstance(raw_entries, list):
        raise ValueError("缓存文件结构无效，已拒绝判定")
    entries: dict[tuple[str, str, str], Mapping[str, object]] = {}
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise ValueError("缓存文件站点结构无效，已拒绝判定")
        key = cache_key(str(entry.get("name") or ""), str(entry.get("url") or ""), str(entry.get("pick") or "top"))
        if key in entries:
            raise ValueError(f"缓存文件包含重复站点，已拒绝判定：{key[0]} {key[1]} {key[2]}")
        entries[key] = entry

    adjusted: list[Result] = []
    period_key = str(current_period)
    for result in results:
        if not result.ok or result.record is None:
            adjusted.append(result)
            continue
        if result.record.position_kind != CACHE_POSITION_KIND:
            raise ValueError(
                f"缓存位置语义不兼容：{result.site.name}抓取结果position_kind="
                f"{result.record.position_kind or '缺失'}，要求{CACHE_POSITION_KIND}"
            )
        entry = entries.get(cache_key(result.site.name, result.site.url, result.site.pick))
        if entry is None:
            adjusted.append(result)
            continue
        values = values_from_entry(entry)
        positions = positions_from_entry(entry)
        article_ids = article_ids_from_entry(entry)
        expected = values.get(period_key)
        expected_position = positions.get(period_key, -1)
        expected_article_id = article_ids.get(period_key, "")
        current_article_id = result.record.record_id if result.site.payload == "admin_article_api" else ""
        value_conflict = expected is not None and normalize_zodiac(expected) != normalize_zodiac(result.record.zodiac)
        position_conflict = (
            expected is not None
            and expected_position >= 0
            and result.record.position >= 0
            and expected_position != result.record.position
        )
        article_conflict = bool(
            result.site.payload == "admin_article_api"
            and expected_article_id
            and expected_article_id != current_article_id
        )
        if not (value_conflict or position_conflict or article_conflict):
            adjusted.append(result)
            continue
        article_detail = (
            f"，文章ID为{expected_article_id}，本次ID为{current_article_id or '缺失'}"
            if article_conflict
            else ""
        )
        adjusted.append(
            Result(
                result.site,
                None,
                f"缓存数据冲突：{current_period}期缓存为{expected}@位置{expected_position}，"
                f"本次抓到{result.record.zodiac}@位置{result.record.position}{article_detail}；已拒绝写入成功结果",
            )
        )
    return adjusted

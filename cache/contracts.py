from __future__ import annotations

from collections.abc import Mapping

from domain.models import POSITION_KIND_VISIBLE_TEXT


CACHE_SCHEMA_VERSION = 2
CACHE_POSITION_KIND = POSITION_KIND_VISIBLE_TEXT


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

    raw_sites = cache.get("sites")
    if raw_sites is None:
        return
    if not isinstance(raw_sites, list):
        raise ValueError("缓存文件站点列表结构无效，已拒绝判定")
    for index, entry in enumerate(raw_sites):
        if not isinstance(entry, Mapping):
            raise ValueError(f"缓存文件第{index + 1}个站点结构无效，已拒绝判定")
        name = str(entry.get("name") or f"第{index + 1}个站点")
        entry_kind = str(entry.get("position_kind") or "")
        if entry_kind != CACHE_POSITION_KIND:
            raise ValueError(
                f"缓存位置语义不兼容：{name} position_kind={entry_kind or '缺失'}，"
                f"要求{CACHE_POSITION_KIND}；已拒绝混写"
            )
        records = entry.get("records")
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping) or record.get("position") is None:
                continue
            record_kind = str(record.get("position_kind") or "")
            if record_kind != CACHE_POSITION_KIND:
                period = record.get("period", "未知")
                raise ValueError(
                    f"缓存位置语义不兼容：{name} {period}期记录缺少统一position_kind；已拒绝混写"
                )

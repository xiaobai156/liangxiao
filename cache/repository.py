from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

from cache.contracts import (
    CACHE_POSITION_KIND,
    CACHE_SCHEMA_VERSION,
    validate_cache_position_contract,
)
from cache.duplicates import article_ids_from_entry, cache_key, positions_from_entry, values_from_entry
from domain.models import HistoryResult, Result


def recent_periods(current_period: int, count: int = 10) -> list[int]:
    if not 1 <= current_period <= 365:
        raise ValueError(f"期数必须在1-365内：{current_period}")
    if count < 1:
        return []
    return [((current_period - offset - 1) % 365) + 1 for offset in range(count)]


def _validate_issues(cache: Mapping[str, object]) -> list[int]:
    raw_issues = cache.get("issues")
    if not isinstance(raw_issues, list) or not raw_issues:
        raise ValueError("缓存文件缺少有效期数窗口，已拒绝覆盖")
    try:
        issues = [int(period) for period in raw_issues]
    except (TypeError, ValueError) as exc:
        raise ValueError("缓存文件期数窗口无效，已拒绝覆盖") from exc
    if any(not 1 <= period <= 365 for period in issues) or issues != recent_periods(issues[0], len(issues)):
        raise ValueError("缓存文件期数窗口顺序无效，已拒绝覆盖")
    return issues


class RecentCacheRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[str, object]:
        if not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"缓存文件损坏，已拒绝覆盖：{self.path}") from exc
        except OSError as exc:
            raise ValueError(f"缓存文件读取失败，已拒绝覆盖：{self.path}：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"缓存文件结构无效，已拒绝覆盖：{self.path}")
        return loaded

    def prepare_update(self, results: list[Result], current_period: int) -> dict[str, object] | None:
        cache = self.read()
        existing_issues: list[int] = []
        if cache:
            validate_cache_position_contract(cache)
            existing_issues = _validate_issues(cache)
            if not isinstance(cache.get("sites"), list):
                raise ValueError(f"缓存文件站点列表结构无效，已拒绝覆盖：{self.path}")

        if existing_issues:
            next_period = 1 if existing_issues[0] == 365 else existing_issues[0] + 1
            if current_period == next_period:
                issues = recent_periods(current_period)
            elif current_period in existing_issues:
                issues = existing_issues
            else:
                return None
        else:
            issues = recent_periods(current_period)
        issue_keys = {str(period) for period in issues}

        existing: dict[tuple[str, str, str], Mapping[str, object]] = {}
        identities: dict[tuple[str, str, str], dict[str, str]] = {}
        ordered_keys: list[tuple[str, str, str]] = []
        raw_entries = cache.get("sites", [])
        assert isinstance(raw_entries, list)
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise ValueError(f"缓存文件站点结构无效，已拒绝覆盖：{self.path}")
            key = cache_key(str(entry.get("name") or ""), str(entry.get("url") or ""), str(entry.get("pick") or "top"))
            if key in existing:
                raise ValueError(f"缓存文件包含重复站点，已拒绝覆盖：{key[0]} {key[1]} {key[2]}")
            existing[key] = entry
            identities[key] = {"name": key[0], "url": key[1], "pick": key[2]}
            ordered_keys.append(key)

        result_by_key: dict[tuple[str, str, str], Result] = {}
        for result in results:
            key = cache_key(result.site.name, result.site.url, result.site.pick)
            if key in result_by_key:
                raise ValueError(f"抓取结果包含重复站点，已拒绝覆盖：{result.site.name}")
            result_by_key[key] = result
            if key not in identities:
                identities[key] = {"name": key[0], "url": key[1], "pick": key[2]}
                ordered_keys.append(key)

        sites: list[dict[str, object]] = []
        period_key = str(current_period)
        for key in ordered_keys:
            entry = existing.get(key, {})
            values = values_from_entry(entry)
            positions = positions_from_entry(entry)
            article_ids = article_ids_from_entry(entry)
            result = result_by_key.get(key)
            if result is not None and result.ok and result.record is not None:
                record = result.record
                if record.position_kind != CACHE_POSITION_KIND:
                    raise ValueError(
                        f"缓存位置语义不兼容：{result.site.name}抓取结果position_kind="
                        f"{record.position_kind or '缺失'}，要求{CACHE_POSITION_KIND}"
                    )
                record_article_id = record.record_id if result.site.payload == "admin_article_api" else ""
                values[period_key] = record.zodiac
                positions[period_key] = record.position
                if record_article_id:
                    article_ids[period_key] = record_article_id
                else:
                    article_ids.pop(period_key, None)

            values = {str(period): values[str(period)] for period in issues if str(period) in values and str(period) in issue_keys}
            positions = {period: positions[period] for period in values if period in positions}
            article_ids = {period: article_ids[period] for period in values if period in article_ids}
            records = [
                {
                    "period": int(period),
                    "zodiac": zodiac,
                    "position": positions.get(period, -1),
                    "source_positions": [positions[period]] if period in positions else [],
                    "position_kind": CACHE_POSITION_KIND,
                }
                for period, zodiac in values.items()
            ]
            output: dict[str, object] = {
                **identities[key],
                "position_kind": CACHE_POSITION_KIND,
                "values": values,
                "positions": positions,
                "records": records,
                "fingerprint": "".join(values[str(period)] for period in issues if str(period) in values),
            }
            error = result.error if result is not None else str(entry.get("error") or "")
            if result is not None and not result.ok:
                output["status"] = "failed"
            if error:
                output["error"] = error
            if article_ids:
                output["article_ids"] = article_ids
            sites.append(output)

        return {
            "schema": CACHE_SCHEMA_VERSION,
            "position_kind": CACHE_POSITION_KIND,
            "description": "杀两肖重复检测最近10期基准数据；由每日指定期抓取自动覆盖更新。",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "window_size": 10,
            "issues": issues,
            "sites": sites,
        }

    def commit(self, payload: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        temporary_path = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def prepare_history_update(
        self,
        results: list[HistoryResult],
        current_period: int,
    ) -> dict[str, object]:
        cache = self.read()
        if cache:
            validate_cache_position_contract(cache)
        issues = recent_periods(current_period)
        existing: dict[tuple[str, str, str], Mapping[str, object]] = {}
        raw_entries = cache.get("sites", []) if cache else []
        if not isinstance(raw_entries, list):
            raise ValueError(f"缓存文件站点列表结构无效，已拒绝覆盖：{self.path}")
        for entry in raw_entries:
            if not isinstance(entry, dict):
                raise ValueError(f"缓存文件站点结构无效，已拒绝覆盖：{self.path}")
            key = cache_key(str(entry.get("name") or ""), str(entry.get("url") or ""), str(entry.get("pick") or "top"))
            if key in existing:
                raise ValueError(f"缓存文件包含重复站点，已拒绝覆盖：{key[0]} {key[1]} {key[2]}")
            existing[key] = entry
        sites: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        issue_keys = {str(period) for period in issues}
        for result in results:
            key = cache_key(result.site.name, result.site.url, result.site.pick)
            if key in seen:
                raise ValueError(f"历史抓取结果包含重复站点，已拒绝写入：{result.site.name}")
            seen.add(key)
            old = existing.get(key, {})
            values = {period: value for period, value in values_from_entry(old).items() if period in issue_keys}
            positions = {period: value for period, value in positions_from_entry(old).items() if period in issue_keys}
            article_ids = {period: value for period, value in article_ids_from_entry(old).items() if period in issue_keys}
            for record in result.records if result.ok else ():
                if record.position_kind != CACHE_POSITION_KIND:
                    raise ValueError(
                        f"缓存位置语义不兼容：{result.site.name}抓取结果position_kind="
                        f"{record.position_kind or '缺失'}，要求{CACHE_POSITION_KIND}"
                    )
                period = str(record.period)
                record_article_id = record.record_id if result.site.payload == "admin_article_api" else ""
                if period not in issue_keys:
                    continue
                values[period] = record.zodiac
                positions[period] = record.position
                if record_article_id:
                    article_ids[period] = record_article_id
                else:
                    article_ids.pop(period, None)
            ordered_values = {str(period): values[str(period)] for period in issues if str(period) in values}
            ordered_positions = {period: positions[period] for period in ordered_values if period in positions}
            ordered_articles = {period: article_ids[period] for period in ordered_values if period in article_ids}
            records = [
                {
                    "period": int(period),
                    "zodiac": zodiac,
                    "position": ordered_positions.get(period, -1),
                    "source_positions": [ordered_positions[period]] if period in ordered_positions else [],
                    "position_kind": CACHE_POSITION_KIND,
                }
                for period, zodiac in ordered_values.items()
            ]
            entry: dict[str, object] = {
                "name": result.site.name,
                "url": result.site.url,
                "pick": result.site.pick,
                "position_kind": CACHE_POSITION_KIND,
                "values": ordered_values,
                "positions": ordered_positions,
                "records": records,
                "fingerprint": "".join(ordered_values.values()),
                "error": result.error,
            }
            if not result.ok:
                entry["status"] = "failed"
            if ordered_articles:
                entry["article_ids"] = ordered_articles
            sites.append(entry)
        return {
            "schema": CACHE_SCHEMA_VERSION,
            "position_kind": CACHE_POSITION_KIND,
            "description": "杀两肖重复检测最近10期基准数据；由每日指定期抓取自动覆盖更新。",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "window_size": 10,
            "issues": issues,
            "sites": sites,
        }

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime
from copy import deepcopy
from pathlib import Path

from cache.contracts import (
    CACHE_POSITION_KIND,
    CACHE_SCHEMA_VERSION,
    CACHE_WINDOW_SIZE,
    cache_identity,
    config_fingerprint,
    recent_periods,
    validate_cache_position_contract,
    validate_issue_window,
)
from cache.duplicates import (
    article_ids_from_entry,
    positions_from_entry,
    values_from_entry,
)
from domain.models import HistoryResult, Record, Result, Site
from validation.records import normalize_zodiac, validate_selected_record
from cache.serialization import serialize_cached_record


class _PreparedCache(dict[str, object]):
    def __init__(self, payload: Mapping[str, object], source_hash: str | None) -> None:
        super().__init__(payload)
        self.source_hash = source_hash


def _result_identity(result: Result | HistoryResult, index: int) -> tuple[str, str, str]:
    site = result.site
    if not isinstance(site.name, str) or not site.name.strip():
        raise ValueError(f"抓取结果第{index + 1}个站点身份无效：name不能为空")
    if not isinstance(site.url, str) or not site.url.strip():
        raise ValueError(f"抓取结果第{index + 1}个站点身份无效：url不能为空")
    if site.pick not in {"top", "bottom"}:
        raise ValueError(f"抓取结果第{index + 1}个站点身份无效：pick必须为top或bottom")
    return site.name, site.url, site.pick


def _result_keys(results: list[Result] | list[HistoryResult]) -> list[tuple[str, str, str]]:
    keys: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, result in enumerate(results):
        key = _result_identity(result, index)
        if key in seen:
            raise ValueError(f"抓取结果包含重复站点身份：{key[0]} {key[1]} {key[2]}")
        seen.add(key)
        keys.append(key)
    return keys


def _validate_record_position(record, site_name: str) -> None:
    if isinstance(record.position, bool) or not isinstance(record.position, int) or record.position < 0:
        raise ValueError(f"缓存位置结构无效：{site_name} {record.period}期position必须为非负整数")


def _validate_issues(cache: Mapping[str, object]) -> list[int]:
    return validate_issue_window(cache.get("issues"))


def _validate_current_period(current_period: object) -> int:
    if isinstance(current_period, bool) or not isinstance(current_period, int) or not 1 <= current_period <= 365:
        raise ValueError(f"期数必须在1-365内：{current_period}")
    return current_period


class RecentCacheRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_snapshot(self) -> tuple[dict[str, object], str | None]:
        if not self.path.exists():
            return {}, None
        try:
            raw = self.path.read_bytes()
            loaded = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"缓存文件损坏，已拒绝覆盖：{self.path}") from exc
        except OSError as exc:
            raise ValueError(f"缓存文件读取失败，已拒绝覆盖：{self.path}：{exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"缓存文件结构无效，已拒绝覆盖：{self.path}")
        return loaded, hashlib.sha256(raw).hexdigest()

    def read(self) -> dict[str, object]:
        return self._read_snapshot()[0]

    def _current_hash(self) -> str | None:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ValueError(f"缓存文件读取失败，已拒绝覆盖：{self.path}：{exc}") from exc
        return hashlib.sha256(raw).hexdigest()

    @contextmanager
    def _lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        with lock_path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ValueError("缓存正在被其他进程写入，已拒绝覆盖") from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ValueError("缓存正在被其他进程写入，已拒绝覆盖") from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def prepare_update(self, results: list[Result], current_period: int) -> dict[str, object] | None:
        current_period = _validate_current_period(current_period)
        result_keys = _result_keys(results)
        expected_fingerprint = config_fingerprint([result.site for result in results])
        cache, source_hash = self._read_snapshot()
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

        existing: dict[tuple[str, str, str], Mapping[str, object]] = {}
        raw_entries = cache.get("sites", [])
        if not isinstance(raw_entries, list):
            raise ValueError(f"缓存文件站点列表结构无效，已拒绝覆盖：{self.path}")
        existing_keys: list[tuple[str, str, str]] = []
        for index, entry in enumerate(raw_entries):
            if not isinstance(entry, Mapping):
                raise ValueError(f"缓存文件第{index + 1}个站点结构无效，已拒绝覆盖：{self.path}")
            key = cache_identity(entry, index)
            if key in existing:
                raise ValueError(f"缓存文件包含重复站点，已拒绝覆盖：{key[0]} {key[1]} {key[2]}")
            existing[key] = entry
            existing_keys.append(key)
        if cache and existing_keys != result_keys:
            raise ValueError("缓存站点身份缺失、额外或顺序不一致，已拒绝覆盖")

        result_by_key = dict(zip(result_keys, results))
        sites: list[dict[str, object]] = []
        period_key = str(current_period)
        for key in result_keys:
            entry = existing.get(key, {})
            values = values_from_entry(entry)
            positions = positions_from_entry(entry)
            article_ids = article_ids_from_entry(entry)
            result = result_by_key[key]
            if result.ok and result.record is not None:
                record = result.record
                if record.period != current_period:
                    raise ValueError(f"抓取结果期数不匹配：{result.site.name}")
                if record.position_kind != CACHE_POSITION_KIND:
                    raise ValueError(
                        f"缓存位置语义不兼容：{result.site.name}抓取结果position_kind="
                        f"{record.position_kind or '缺失'}，要求{CACHE_POSITION_KIND}"
                    )
                _validate_record_position(record, result.site.name)
                record_article_id = record.record_id if result.site.payload == "admin_article_api" else ""
                values[period_key] = record.zodiac
                positions[period_key] = record.position
                if record_article_id:
                    article_ids[period_key] = record_article_id
                else:
                    article_ids.pop(period_key, None)
            else:
                values.pop(period_key, None)
                positions.pop(period_key, None)
                article_ids.pop(period_key, None)

            values = {str(period): values[str(period)] for period in issues if str(period) in values}
            positions = {period: positions[period] for period in values if period in positions}
            article_ids = {period: article_ids[period] for period in values if period in article_ids}
            old_records = {str(item["period"]): item for item in entry.get("records", [])}
            if result.ok and result.record is not None:
                old_records[period_key] = serialize_cached_record(result.site, result.record)
            records = [deepcopy(old_records[period]) for period in values]
            output: dict[str, object] = {
                "name": result.site.name,
                "url": result.site.url,
                "pick": result.site.pick,
                "position_kind": CACHE_POSITION_KIND,
                "values": values,
                "positions": positions,
                "records": records,
                "fingerprint": "".join(values[str(period)] for period in issues if str(period) in values),
            }
            if not result.ok:
                output["status"] = "failed"
            if result.error:
                output["error"] = result.error
            if article_ids:
                output["article_ids"] = article_ids
            sites.append(output)

        return _PreparedCache(
            {
                "schema": CACHE_SCHEMA_VERSION,
                "position_kind": CACHE_POSITION_KIND,
                "config_fingerprint": expected_fingerprint,
                "description": "杀两肖重复检测最近10期基准数据；由每日指定期抓取自动覆盖更新。",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "window_size": CACHE_WINDOW_SIZE,
                "issues": issues,
                "sites": sites,
            },
            source_hash,
        )

    def prepare_current_period_update(
        self,
        period: int,
        results: list[Result],
        *,
        sites: list[Site] | None = None,
    ) -> dict[str, object]:
        """Prepare a restricted repair; preserve every other site and issue."""
        period = _validate_current_period(period)
        _result_keys(results)
        payload, source_hash = self._read_snapshot()
        validate_cache_position_contract(payload)
        issues = validate_issue_window(payload.get("issues"))
        if period not in issues:
            raise ValueError(f"当前期{period}不在缓存窗口，未同步")
        if sites is not None and payload.get("config_fingerprint") != config_fingerprint(sites):
            raise ValueError("缓存config_fingerprint与当前配置不匹配，未同步")
        updated = deepcopy(payload)
        entries = {cache_identity(entry, index): entry
                   for index, entry in enumerate(updated["sites"])}
        for result in results:
            if not result.ok or result.record is None:
                continue
            record = result.record
            if not validate_selected_record(record, period):
                raise ValueError(f"缓存修复结果期数或字段不合法：{result.site.name}")
            entry = entries.get(result.site.identity)
            if entry is None:
                raise ValueError(f"缓存中未找到站点：{result.site.name}")
            key = str(period)
            entry["values"][key] = record.zodiac
            entry["positions"][key] = record.position
            serialized = serialize_cached_record(result.site, record)
            records = entry["records"]
            index = next((i for i, item in enumerate(records) if item["period"] == period), None)
            if index is None:
                records.append(serialized)
            else:
                records[index] = serialized
            articles = entry.get("article_ids", {})
            if result.site.payload == "admin_article_api" and record.record_id:
                articles[key] = record.record_id
            else:
                articles.pop(key, None)
            if articles:
                entry["article_ids"] = articles
            else:
                entry.pop("article_ids", None)
            entry["fingerprint"] = "".join(entry["values"][str(issue)]
                                           for issue in issues if str(issue) in entry["values"])
            entry.pop("status", None)
            entry.pop("error", None)
        updated["updated_at"] = datetime.now().isoformat(timespec="seconds")
        validate_cache_position_contract(updated)
        return _PreparedCache(updated, source_hash)

    def update_current_period(
        self, period: int, results: list[Result], *, sites: list[Site] | None = None
    ) -> None:
        self.commit(self.prepare_current_period_update(period, results, sites=sites))

    def commit(self, payload: Mapping[str, object]) -> None:
        if not isinstance(payload, _PreparedCache):
            raise ValueError("缓存提交未经过prepare，已拒绝写入")
        validate_cache_position_contract(payload)
        source_hash = payload.source_hash
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        with self._lock():
            current_hash = self._current_hash()
            if current_hash != source_hash:
                raise ValueError("缓存在prepare后发生变化，已拒绝覆盖")
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
        current_period = _validate_current_period(current_period)
        _, source_hash = self._read_snapshot()
        issues = recent_periods(current_period)
        _result_keys(results)
        expected_fingerprint = config_fingerprint([result.site for result in results])
        sites: list[dict[str, object]] = []
        issue_keys = {str(period) for period in issues}
        for result in results:
            values: dict[str, str] = {}
            positions: dict[str, int] = {}
            article_ids: dict[str, str] = {}
            for record in result.records:
                if record.position_kind != CACHE_POSITION_KIND:
                    raise ValueError(
                        f"缓存位置语义不兼容：{result.site.name}抓取结果position_kind="
                        f"{record.position_kind or '缺失'}，要求{CACHE_POSITION_KIND}"
                    )
                period = str(record.period)
                record_article_id = record.record_id if result.site.payload == "admin_article_api" else ""
                if period not in issue_keys:
                    continue
                _validate_record_position(record, result.site.name)
                if period in values:
                    existing_article_id = article_ids.get(period, "")
                    if normalize_zodiac(values[period]) != normalize_zodiac(record.zodiac) or positions[period] != record.position:
                        raise ValueError(f"缓存历史记录同期冲突：{result.site.name} {period}期结果不一致")
                    if (
                        existing_article_id
                        and record_article_id
                        and existing_article_id != record_article_id
                    ):
                        raise ValueError(f"缓存历史记录同期冲突：{result.site.name} {period}期record_id不一致")
                    if not existing_article_id and record_article_id:
                        article_ids[period] = record_article_id
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
            record_by_period: dict[str, Record] = {}
            for record in result.records:
                record_by_period.setdefault(str(record.period), record)
            records = [
                serialize_cached_record(result.site, record_by_period[period])
                for period in ordered_values
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
        return _PreparedCache(
            {
                "schema": CACHE_SCHEMA_VERSION,
                "position_kind": CACHE_POSITION_KIND,
                "config_fingerprint": expected_fingerprint,
                "description": "杀两肖重复检测最近10期基准数据；由每日指定期抓取自动覆盖更新。",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "window_size": CACHE_WINDOW_SIZE,
                "issues": issues,
                "sites": sites,
            },
            source_hash,
        )

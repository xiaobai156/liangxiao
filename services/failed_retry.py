from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cache.repository import RecentCacheRepository
from domain.models import Result, Site
from output.locking import output_lock
from output.transaction import (
    _restore,
    _snapshot,
    _validate_distinct_paths,
    append_repaired_successes,
    atomic_write_bytes,
)
from validation.records import normalize_zodiac, validate_selected_record


def sites_from_failure_file(path: Path, sites: list[Site]) -> list[Site]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    found: list[Site] = []
    for line in lines:
        for site in sites:
            if line.startswith(f"{site.name} {site.pick} {site.url} ") and site not in found:
                found.append(site)
                break
    return found


def remove_successful_failures(path: Path, results: list[Result]) -> None:
    if not path.exists():
        return
    identities = tuple(f"{r.site.name} {r.site.pick} {r.site.url} " for r in results if r.ok)
    kept = [line for line in path.read_bytes().splitlines(keepends=True)
            if not any(line.decode("utf-8-sig").startswith(prefix) for prefix in identities)]
    atomic_write_bytes(path, b"".join(kept))


def update_current_cache(
    path: Path,
    period: int,
    results: list[Result],
    *,
    sites: list[Site] | None = None,
) -> None:
    # Never bypass the repository's lock or its compare-and-swap check.
    RecentCacheRepository(path).update_current_period(period, results, sites=sites)


def apply_retry(
    results: list[Result],
    success: Path,
    failure: Path,
    cache: Path,
    period: int,
    include_url: bool,
    *,
    sites: list[Site] | None = None,
) -> None:
    good: list[Result] = []
    seen: dict[tuple[str, str, str], tuple[str, int]] = {}
    for result in results:
        if not result.ok or result.record is None:
            continue
        if not validate_selected_record(result.record, period):
            raise ValueError(f"修复结果期数或字段不合法：{result.site.name}")
        signature = (normalize_zodiac(result.record.zodiac), result.record.position)
        if result.site.identity in seen:
            if seen[result.site.identity] != signature:
                raise ValueError(f"修复输入同站结果冲突：{result.site.name}")
            continue
        seen[result.site.identity] = signature
        good.append(replace(result, record=replace(
            result.record, zodiac=normalize_zodiac(result.record.zodiac)
        )))
    if not good:
        return
    _validate_distinct_paths((("成功TXT", success), ("失败TXT", failure), ("缓存", cache)))
    # Output and cache are deliberately separate transactions. Do not nest locks.
    with output_lock(success, failure):
        _apply_retry_outputs_locked(good, success, failure, include_url)
    try:
        update_current_cache(cache, period, good, sites=sites)
    except Exception as exc:
        raise RuntimeError(f"缓存更新未同步，成功TXT已保留：{exc}") from exc


def _apply_retry_outputs_locked(
    good: list[Result], success: Path, failure: Path, include_url: bool
) -> None:
    snapshot = _snapshot((success, failure))
    try:
        existing = success.read_text(encoding="utf-8-sig") if success.exists() else ""
        missing: list[Result] = []
        for result in good:
            assert result.record is not None
            found = False
            for line in existing.splitlines():
                parts = line.split(maxsplit=1)
                if len(parts) != 2 or parts[1] not in {
                    result.site.name, f"{result.site.name} {result.site.url}"
                }:
                    continue
                if normalize_zodiac(parts[0]) != result.record.zodiac:
                    raise ValueError(f"成功TXT已有同站不同结果：{result.site.name}")
                found = True
            if not found:
                missing.append(result)
        append_repaired_successes(missing, success, include_url=include_url)
        remove_successful_failures(failure, good)
    except BaseException:
        _restore(snapshot)
        raise

from __future__ import annotations

import json
from pathlib import Path

from output.transaction import append_repaired_successes, atomic_write_bytes
from cache.repository import RecentCacheRepository
from cache.contracts import validate_issue_window, validate_cache_position_contract
from contextlib import contextmanager
import os
import tempfile
from domain.models import Result, Site
from output.transaction import _validate_distinct_paths


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


def update_current_cache(path: Path, period: int, results: list[Result], *, locked: bool = False) -> None:
    repository = RecentCacheRepository(path)
    lock = repository._lock() if not locked else _null_lock()
    with lock:
        original = path.read_bytes()
        payload = json.loads(original.decode("utf-8-sig"))
        validate_cache_position_contract(payload)
        issues = validate_issue_window(payload.get("issues"))
        if period not in issues:
            raise ValueError(f"当前期{period}不在缓存窗口，未同步")
        entries = payload.get("sites")
        if not isinstance(entries, list): raise ValueError("缓存sites结构无效")
        by_identity = {(e.get("name"), e.get("url"), e.get("pick")): e for e in entries if isinstance(e, dict)}
        for result in results:
            if not result.ok or result.record is None: continue
            entry = by_identity.get(result.site.identity)
            if entry is None: raise ValueError(f"缓存中未找到站点：{result.site.name}")
            values = dict(entry.get("values", {})); positions = dict(entry.get("positions", {})); articles = dict(entry.get("article_ids", {}))
            key = str(period); values[key] = result.record.zodiac; positions[key] = result.record.position
            entry["values"], entry["positions"] = values, positions
            if result.record.record_id: articles[key] = result.record.record_id; entry["article_ids"] = articles
            entry["records"] = [{"period": int(p), "zodiac": values[p], "position": positions[p], "source_positions": [positions[p]], "position_kind": entry.get("position_kind", "visible_text_offset_v1")} for p in values]
            entry["fingerprint"] = "".join(values.values()); entry.pop("status", None); entry.pop("error", None)
        atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


class _null_lock:
    def __enter__(self): return self
    def __exit__(self, *args): return False

@contextmanager
def output_lock(path: Path):
    lock = path.parent / ".杀两肖输出.lock"
    with lock.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0: handle.write(b"0"); handle.flush()
            handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try: yield
            finally: handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def apply_retry(results: list[Result], success: Path, failure: Path, cache: Path, period: int, include_url: bool) -> None:
    good = [r for r in results if r.ok]
    if good:
        _validate_distinct_paths((("成功TXT", success), ("失败TXT", failure), ("缓存", cache)))
        with output_lock(success):
            _apply_retry_locked(good, success, failure, cache, period, include_url)


def _apply_retry_locked(good, success, failure, cache, period, include_url):
        success_before = success.read_bytes() if success.exists() else None
        failure_before = failure.read_bytes() if failure.exists() else None
        try:
            existing = success.read_text(encoding="utf-8-sig") if success.exists() else ""
            for result in good:
                expected = f"{result.record.zodiac} {result.site.name}"
                for line in existing.splitlines():
                    if line == expected or line.startswith(expected + " "): continue
                    if line.endswith(" " + result.site.name) or line.endswith(" " + result.site.name + " " + result.site.url):
                        raise ValueError(f"成功TXT已有同站不同结果：{result.site.name}")
            existing_keys = {line.split(" ", 2)[:2][0] + " " + line.split(" ", 2)[:2][1]
                             for line in existing.splitlines() if len(line.split(" ", 2)) >= 2}
            append_repaired_successes(
                [r for r in good if f"{r.record.zodiac} {r.site.name}" not in existing_keys],
                success,
                include_url=include_url,
            )
            remove_successful_failures(failure, good)
        except Exception:
            if success_before is None: success.unlink(missing_ok=True)
            else: atomic_write_bytes(success, success_before)
            if failure_before is not None: atomic_write_bytes(failure, failure_before)
            raise
        try:
            update_current_cache(cache, period, good, locked=True)
        except Exception as exc:
            raise RuntimeError(f"缓存更新未同步，成功TXT已保留：{exc}") from exc

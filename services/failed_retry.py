from __future__ import annotations

import json
from pathlib import Path

from output.transaction import append_repaired_successes, atomic_write_bytes
from cache.repository import RecentCacheRepository
from cache.contracts import validate_issue_window
from domain.models import Result, Site


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


def update_current_cache(path: Path, period: int, results: list[Result]) -> None:
    repository = RecentCacheRepository(path)
    with repository._lock():
        original = path.read_bytes()
        payload = json.loads(original.decode("utf-8-sig"))
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


def apply_retry(results: list[Result], success: Path, failure: Path, cache: Path, period: int, include_url: bool) -> None:
    good = [r for r in results if r.ok]
    if good:
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
            append_repaired_successes(good, success, include_url=include_url)
            remove_successful_failures(failure, good)
        except Exception:
            if success_before is None: success.unlink(missing_ok=True)
            else: atomic_write_bytes(success, success_before)
            if failure_before is not None: atomic_write_bytes(failure, failure_before)
            raise
        try:
            update_current_cache(cache, period, good)
        except Exception as exc:
            raise RuntimeError(f"缓存更新未同步，成功TXT已保留：{exc}") from exc

from __future__ import annotations

import json
from pathlib import Path

from output.transaction import append_repaired_successes, atomic_write_bytes
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
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = payload.get("sites")
    if not isinstance(entries, list):
        raise ValueError("缓存sites结构无效")
    by_identity = {(e.get("name"), e.get("url"), e.get("pick")): e for e in entries if isinstance(e, dict)}
    for result in results:
        if not result.ok or result.record is None:
            continue
        entry = by_identity.get(result.site.identity)
        if entry is None:
            raise ValueError(f"缓存中未找到站点：{result.site.name}")
        values = dict(entry.get("values", {})); positions = dict(entry.get("positions", {}))
        values[str(period)] = result.record.zodiac; positions[str(period)] = result.record.position
        entry["values"], entry["positions"] = values, positions
        entry["records"] = [{"period": int(p), "zodiac": values[p], "position": positions[p],
                             "source_positions": [positions[p]], "position_kind": entry.get("position_kind", "visible_text_offset_v1")}
                            for p in values]
        entry["fingerprint"] = "".join(values.values())
        entry.pop("status", None); entry.pop("error", None)
    atomic_write_bytes(path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))


def apply_retry(results: list[Result], success: Path, failure: Path, cache: Path, period: int, include_url: bool) -> None:
    good = [r for r in results if r.ok]
    if good:
        append_repaired_successes(good, success, include_url=include_url)
        remove_successful_failures(failure, good)
        update_current_cache(cache, period, good)

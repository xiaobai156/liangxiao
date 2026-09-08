from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path

from config.loader import load_sites
from output.transaction import atomic_write_bytes
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import scrape_sites


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"
SUCCESS = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳\232期-二肖.txt")
FAILURE = Path(r"C:\Users\Administrator\Desktop\每天工具\爬虫合集\七类数据统一归纳失败\232期-二肖-失败.txt")
TARGETS = {"枪打天下": "龙马", "雁落沙滩": "蛇猪"}
POSITION_KIND = "visible_text_offset_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def main() -> None:
    before_config = sha256(CONFIG)
    all_sites = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
    sites = [site for site in all_sites if site.name in TARGETS]
    if [site.name for site in sites] != ["枪打天下", "雁落沙滩"]:
        raise RuntimeError("指定站点配置缺失或顺序异常")

    registry = ParserRegistry.bind_sites(sites)
    results = scrape_sites(sites, 232, 25, 2, registry=registry, progress=print)
    if len(results) != 2:
        raise RuntimeError("指定站点抓取结果数量异常")
    for result in results:
        expected = TARGETS[result.site.name]
        if not result.ok or result.record is None:
            raise RuntimeError(f"{result.site.name}正式复抓失败：{result.error}")
        if result.record.zodiac != expected or result.record.position < 0:
            raise RuntimeError(
                f"{result.site.name}正式复抓结果异常：{result.record.zodiac}@{result.record.position}，期望{expected}"
            )

    success_text = SUCCESS.read_text(encoding="utf-8-sig")
    for name, zodiac in TARGETS.items():
        expected_line = f"{zodiac} {name}"
        if success_text.splitlines().count(expected_line) != 1:
            raise RuntimeError(f"成功TXT缺少唯一结果：{expected_line}")

    failure_text = FAILURE.read_text(encoding="utf-8-sig") if FAILURE.exists() else ""
    blocks = [block.strip() for block in failure_text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    remaining_blocks = [
        block
        for block in blocks
        if not any(block.startswith(name + " ") for name in TARGETS)
    ]
    removed_names = {
        name
        for name in TARGETS
        if any(block.startswith(name + " ") for block in blocks)
    }
    if removed_names != set(TARGETS):
        raise RuntimeError(f"失败TXT目标记录不完整：{sorted(removed_names)}")

    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    issues = [int(value) for value in cache.get("issues", [])]
    if 232 not in issues:
        raise RuntimeError("正式缓存窗口不含232期")
    entries = cache.get("sites")
    if not isinstance(entries, list):
        raise RuntimeError("正式缓存sites结构异常")
    by_name = {entry.get("name"): entry for entry in entries if isinstance(entry, dict)}

    result_by_name = {result.site.name: result for result in results}
    for name in TARGETS:
        entry = by_name.get(name)
        result = result_by_name[name]
        if not isinstance(entry, dict) or result.record is None:
            raise RuntimeError(f"缓存缺少指定站点：{name}")
        values = dict(entry.get("values") or {})
        positions = dict(entry.get("positions") or {})
        values["232"] = result.record.zodiac
        positions["232"] = result.record.position
        ordered_values = {str(issue): values[str(issue)] for issue in issues if str(issue) in values}
        ordered_positions = {
            period: int(positions[period])
            for period in ordered_values
            if period in positions
        }
        entry["values"] = ordered_values
        entry["positions"] = ordered_positions
        entry["records"] = [
            {
                "period": int(period),
                "zodiac": zodiac,
                "position": ordered_positions.get(period, -1),
                "source_positions": [ordered_positions[period]] if period in ordered_positions else [],
                "position_kind": POSITION_KIND,
            }
            for period, zodiac in ordered_values.items()
        ]
        entry["fingerprint"] = "".join(ordered_values.values())
        entry.pop("status", None)
        entry.pop("error", None)

    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
    cache_bytes = json.dumps(cache, ensure_ascii=False, indent=2).encode("utf-8")
    failure_bytes = ("\n\n".join(remaining_blocks) + ("\n" if remaining_blocks else "")).encode("utf-8")

    snapshots = {
        CACHE: CACHE.read_bytes(),
        FAILURE: FAILURE.read_bytes() if FAILURE.exists() else None,
    }
    try:
        atomic_write_bytes(CACHE, cache_bytes)
        if failure_bytes:
            atomic_write_bytes(FAILURE, failure_bytes)
        else:
            FAILURE.unlink(missing_ok=True)
    except BaseException:
        for path, content in snapshots.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, content)
        raise

    if sha256(CONFIG) != before_config:
        raise RuntimeError("正式配置发生意外变化")
    print(
        json.dumps(
            {
                "updated": {
                    result.site.name: {
                        "zodiac": result.record.zodiac,
                        "position": result.record.position,
                    }
                    for result in results
                    if result.record is not None
                },
                "success_txt_changed": False,
                "failure_txt_exists": FAILURE.exists(),
                "config_unchanged": True,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

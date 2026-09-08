from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from cache.contracts import config_fingerprint, validate_cache_position_contract
from cache.duplicates import detect_duplicate_findings
from cache.repository import RecentCacheRepository
from config.loader import load_sites, site_from_mapping
from fetching.client import FetchContext
from fetching.page import fetch_payload
from output.transaction import atomic_write_bytes
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"
NEW_ITEMS = (
    {
        "name": "步步高升",
        "pick": "top",
        "url": "https://azyqhiut.9kzow-3yq4j-hlwmev.work:17477/",
        "parser": "bubu_gaosheng_top",
        "payload": "page_and_scripts",
    },
    {
        "name": "天狼杀星",
        "pick": "bottom",
        "url": "https://etmchyhq.cee5t-w30bf-xgqspr.work:17477/topic/248728.html",
        "parser": "tianlang_shaxing_bottom",
        "payload": "page_and_scripts",
    },
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    raw_config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    validate_cache_position_contract(cache)
    issues = [int(period) for period in cache["issues"]]
    if issues != list(range(241, 231, -1)):
        raise RuntimeError(f"正式缓存期数不是241–232：{issues}")

    existing_names = {str(item.get("name") or "") for item in raw_config}
    existing_urls = {str(item.get("url") or "") for item in raw_config}
    for item in NEW_ITEMS:
        if item["name"] in existing_names or item["url"] in existing_urls:
            raise RuntimeError(f"正式配置已存在：{item['name']}")

    sites = [
        site_from_mapping(item, allowed_parsers=ENGINE_REGISTRY) for item in NEW_ITEMS
    ]
    registry = ParserRegistry.bind_sites(sites)
    context = FetchContext()
    pending_cache = copy.deepcopy(cache)
    verified: dict[str, object] = {}

    for site in sites:
        bundle = fetch_payload(
            site,
            241,
            25,
            context,
            lambda source, target, issue: any(
                record.period == issue for record in registry.parse(source, target)
            ),
        )
        _, selected = parse_bundle_for_period(bundle, site, 241, registry)
        parsed = [
            (document, registry.parse(document, site)) for document in bundle.documents
        ]
        parsed = [(document, records) for document, records in parsed if records]
        if len(parsed) != 1:
            raise RuntimeError(f"{site.name}权威文档数量不是1：{len(parsed)}")
        document, records = parsed[0]
        history = []
        for period in issues:
            matches = sorted(
                (record for record in records if record.period == period),
                key=lambda record: record.position,
            )
            if not matches:
                raise RuntimeError(f"{site.name}缺少{period}期真实历史")
            history.append(matches[0] if site.pick == "top" else matches[-1])
        if (selected.zodiac, selected.position) != (
            history[0].zodiac,
            history[0].position,
        ):
            raise RuntimeError(f"{site.name} 241期方向选择与历史位置不一致")

        entry = {
            "name": site.name,
            "url": site.url,
            "pick": site.pick,
            "position_kind": "visible_text_offset_v1",
            "values": {str(record.period): record.zodiac for record in history},
            "positions": {str(record.period): record.position for record in history},
            "records": [
                {
                    "period": record.period,
                    "zodiac": record.zodiac,
                    "position": record.position,
                    "source_positions": list(record.source_positions),
                    "position_kind": record.position_kind,
                }
                for record in history
            ],
            "fingerprint": "".join(record.zodiac for record in history),
        }
        pending_cache["sites"].append(entry)
        verified[site.name] = {
            "241": selected.zodiac,
            "position": selected.position,
            "history_count": len(history),
            "document": document.url,
        }

    target_names = {item["name"] for item in NEW_ITEMS}
    findings = [
        finding
        for finding in detect_duplicate_findings(pending_cache)
        if finding.first in target_names or finding.second in target_names
    ]
    if findings:
        details = "; ".join(
            f"{finding.first}/{finding.second}:{finding.consecutive}期"
            for finding in findings
        )
        raise RuntimeError(f"新增站判重未通过：{details}")

    new_config = [*raw_config, *NEW_ITEMS]
    loaded_new = [
        site_from_mapping(item, allowed_parsers=ENGINE_REGISTRY) for item in new_config
    ]
    pending_cache["config_fingerprint"] = config_fingerprint(loaded_new)
    pending_cache["updated_at"] = (
        datetime.now().astimezone().isoformat(timespec="seconds")
    )
    validate_cache_position_contract(pending_cache)

    before_hashes = {CONFIG: digest(CONFIG), CACHE: digest(CACHE)}
    snapshots = {CONFIG: CONFIG.read_bytes(), CACHE: CACHE.read_bytes()}
    repository = RecentCacheRepository(CACHE)
    with repository._lock():
        if any(digest(path) != value for path, value in before_hashes.items()):
            raise RuntimeError("正式配置或缓存抓取后发生变化，已拒绝覆盖")
        try:
            atomic_write_bytes(
                CONFIG,
                json.dumps(new_config, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            atomic_write_bytes(
                CACHE,
                json.dumps(pending_cache, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
            written_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
            validate_cache_position_contract(written_cache)
            if len(loaded) != len(raw_config) + len(NEW_ITEMS):
                raise RuntimeError("正式配置写入后数量异常")
            if written_cache["config_fingerprint"] != config_fingerprint(loaded):
                raise RuntimeError("正式缓存配置指纹不一致")
        except BaseException:
            for path, content in snapshots.items():
                atomic_write_bytes(path, content)
            raise

    print(
        json.dumps(
            {
                "verified": verified,
                "config_count": len(new_config),
                "cache_count": len(pending_cache["sites"]),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

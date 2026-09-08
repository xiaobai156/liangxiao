from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path

from cache.contracts import validate_cache_position_contract
from cache.duplicates import detect_duplicate_findings
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
        "name": "周公神算",
        "pick": "top",
        "url": "https://xxn08n.w2jqr-rbl71-ngvkmq.work/",
        "parser": "zhougong_shensuan_two_zodiac",
        "payload": "page_and_scripts",
        "history_authorization": "本次允许不足10期",
    },
    {
        "name": "托尼库尔莫妮卡",
        "pick": "top",
        "url": "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245",
        "parser": "tuoni_kulmonika_snapshots",
        "payload": "tuku_user_forums",
        "history_authorization": "本次允许不足10期",
    },
    {
        "name": "小算算",
        "pick": "bottom",
        "url": "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        "parser": "xiaosuan_bottom_two_zodiac",
        "payload": "tuku_user_forums",
        "history_authorization": "本次允许不足10期",
    },
)
EXPECTED_PERIODS = {
    "周公神算": [235, 234, 233, 232, 231, 230, 229, 228],
    "托尼库尔莫妮卡": [235, 234, 233, 232, 231, 230, 229, 228],
    "小算算": [235, 234],
}


def main() -> None:
    raw_config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    validate_cache_position_contract(cache)
    issues = [int(period) for period in cache.get("issues", [])]
    if issues != [235, 234, 233, 232, 231, 230, 229, 228, 227, 226]:
        raise RuntimeError(f"正式缓存期数不是235–226：{issues}")

    existing_names = {str(item.get("name") or "") for item in raw_config}
    existing_urls = {str(item.get("url") or "") for item in raw_config}
    for item in NEW_ITEMS:
        if item["name"] in existing_names:
            raise RuntimeError(f"正式配置已存在同名：{item['name']}")
        if item["url"] in existing_urls:
            raise RuntimeError(f"正式配置已存在同URL：{item['name']}")

    sites = [site_from_mapping(item, allowed_parsers=ENGINE_REGISTRY) for item in NEW_ITEMS]
    registry = ParserRegistry.bind_sites(sites)
    context = FetchContext()
    temporary_cache = copy.deepcopy(cache)
    cache_entries: list[dict[str, object]] = []
    verified: dict[str, object] = {}

    for site in sites:
        bundle = fetch_payload(
            site,
            235,
            25,
            context,
            lambda source, target, issue: any(
                record.period == issue for record in registry.parse(source, target)
            ),
        )
        _records, selected = parse_bundle_for_period(bundle, site, 235, registry)
        parsed_documents = [
            (document, registry.parse(document, site)) for document in bundle.documents
        ]
        parsed_documents = [(document, records) for document, records in parsed_documents if records]
        if len(parsed_documents) != 1:
            raise RuntimeError(f"{site.name}权威文档数量不是1：{len(parsed_documents)}")
        document, all_records = parsed_documents[0]
        history = []
        for period in issues:
            matches = sorted(
                (record for record in all_records if record.period == period),
                key=lambda record: record.position,
            )
            if matches:
                history.append(matches[0] if site.pick == "top" else matches[-1])
        actual_periods = [record.period for record in history]
        if actual_periods != EXPECTED_PERIODS[site.name]:
            raise RuntimeError(
                f"{site.name}特例历史与已验证范围不同：{actual_periods}"
            )
        if selected.zodiac != history[0].zodiac or selected.position != history[0].position:
            raise RuntimeError(f"{site.name}235期方向选择与缓存记录不一致")

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
            "history_authorization": "本次允许不足10期",
            "missing_issues": [period for period in issues if period not in actual_periods],
        }
        cache_entries.append(entry)
        temporary_cache["sites"].append(copy.deepcopy(entry))
        verified[site.name] = {
            "235": selected.zodiac,
            "history_count": len(history),
            "periods": actual_periods,
            "document": document.url,
        }

    target_names = {site.name for site in sites}
    findings = [
        finding
        for finding in detect_duplicate_findings(temporary_cache)
        if finding.first in target_names or finding.second in target_names
    ]
    if findings:
        details = "; ".join(
            f"{finding.first}/{finding.second}:{finding.consecutive}期"
            for finding in findings
        )
        raise RuntimeError(f"新增站判重未通过：{details}")

    new_config = [*raw_config, *NEW_ITEMS]
    cache["sites"].extend(cache_entries)
    cache["updated_at"] = datetime.now().isoformat(timespec="seconds")
    snapshots = {CONFIG: CONFIG.read_bytes(), CACHE: CACHE.read_bytes()}
    try:
        atomic_write_bytes(
            CONFIG,
            json.dumps(new_config, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        atomic_write_bytes(
            CACHE,
            json.dumps(cache, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        if len(loaded) != len(raw_config) + len(NEW_ITEMS):
            raise RuntimeError("正式配置写入后数量异常")
        written_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        validate_cache_position_contract(written_cache)
        config_names = [site.name for site in loaded]
        cache_names = [str(entry.get("name") or "") for entry in written_cache["sites"]]
        for item in NEW_ITEMS:
            if config_names.count(item["name"]) != 1 or cache_names.count(item["name"]) != 1:
                raise RuntimeError(f"正式写入后身份异常：{item['name']}")
    except BaseException:
        for path, content in snapshots.items():
            atomic_write_bytes(path, content)
        raise

    print(
        json.dumps(
            {
                "verified": verified,
                "config_count": len(new_config),
                "cache_count": len(cache["sites"]),
                "duplicate_findings": [],
                "txt_modified": False,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

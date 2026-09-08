from __future__ import annotations

from datetime import datetime
import copy
import json
from pathlib import Path

from cache.duplicates import detect_duplicate_findings
from config.loader import load_sites, site_from_mapping
from fetching.client import FetchContext
from fetching.dynamic_article import admin_article_id
from fetching.page import fetch_payload
from output.transaction import atomic_write_bytes
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"
NEW_ITEMS = (
    {
        "name": "妆罢低声",
        "pick": "bottom",
        "url": "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a4e87bd57dc857ae1c13558?url=dyj",
        "payload": "admin_article_api",
        "api_url": "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/api/proxy/landing-page-data?url=dyj",
        "parser": "zhuangba_disheng_manager_article",
    },
    {
        "name": "燕姬独擅",
        "pick": "bottom",
        "url": "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a64ec62b3f65fed7d6286e7?url=dyj",
        "payload": "admin_article_api",
        "api_url": "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/api/proxy/landing-page-data?url=dyj",
        "parser": "yanji_dushan_manager_article",
    },
    {
        "name": "金码赌神",
        "pick": "bottom",
        "url": "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/article/manager/6a081b9be0d076537e1df8a6?url=jdb",
        "payload": "admin_article_api",
        "api_url": "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/api/proxy/landing-page-data?url=jdb",
        "parser": "jinma_dushen_manager_article",
    },
)


def main() -> None:
    raw_config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    if not isinstance(raw_config, list):
        raise RuntimeError("正式配置根节点不是数组")
    existing_names = {str(item.get("name") or "") for item in raw_config if isinstance(item, dict)}
    existing_urls = {str(item.get("url") or "") for item in raw_config if isinstance(item, dict)}
    existing_ids = {
        admin_article_id(url)
        for url in existing_urls
        if "/article/admin/" in url or "/article/manager/" in url
    }
    for item in NEW_ITEMS:
        if item["name"] in existing_names or item["url"] in existing_urls:
            raise RuntimeError(f"正式配置已存在：{item['name']}")
        if admin_article_id(item["url"]) in existing_ids:
            raise RuntimeError(f"正式配置文章ID已存在：{item['name']}")

    sites = [site_from_mapping(item, allowed_parsers=ENGINE_REGISTRY) for item in NEW_ITEMS]
    registry = ParserRegistry.bind_sites(sites)
    context = FetchContext()
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    issues = [int(period) for period in cache.get("issues", [])]
    if len(issues) != 10 or issues[0] != 234:
        raise RuntimeError(f"正式缓存期数不是234–225：{issues}")
    temporary_cache = copy.deepcopy(cache)
    cache_entries: list[dict[str, object]] = []
    verified: dict[str, dict[str, object]] = {}

    for site in sites:
        bundle = fetch_payload(
            site,
            234,
            25,
            context,
            lambda source, target, issue: any(
                record.period == issue for record in registry.parse(source, target)
            ),
        )
        if len(bundle.documents) != 1:
            raise RuntimeError(f"{site.name}接口未唯一命中目标文章")
        document = bundle.documents[0]
        if document.record_id != admin_article_id(site.url):
            raise RuntimeError(f"{site.name}文章ID边界不一致")
        records, selected = parse_bundle_for_period(bundle, site, 234, registry)
        by_period = {record.period: record for record in records}
        history = [by_period[period] for period in issues if period in by_period]
        if len(history) != 10 or any(record.position < 0 for record in history):
            raise RuntimeError(f"{site.name}缺少234–225完整位置历史")
        entry: dict[str, object] = {
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
            "article_ids": {
                str(record.period): document.record_id for record in history
            },
        }
        cache_entries.append(entry)
        temporary_cache["sites"].append(copy.deepcopy(entry))
        verified[site.name] = {
            "zodiac": selected.zodiac,
            "position": selected.position,
            "history_count": len(history),
            "article_id": document.record_id,
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
    config_bytes = json.dumps(new_config, ensure_ascii=False, indent=2).encode("utf-8")
    cache_bytes = json.dumps(cache, ensure_ascii=False, indent=2).encode("utf-8")
    snapshots = {CONFIG: CONFIG.read_bytes(), CACHE: CACHE.read_bytes()}
    try:
        atomic_write_bytes(CONFIG, config_bytes)
        atomic_write_bytes(CACHE, cache_bytes)
        loaded = load_sites(CONFIG, allowed_parsers=ENGINE_REGISTRY)
        if len(loaded) != len(raw_config) + 3:
            raise RuntimeError("正式配置写入后数量异常")
        check_cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
        check_names = [entry.get("name") for entry in check_cache.get("sites", [])]
        if any(check_names.count(item["name"]) != 1 for item in NEW_ITEMS):
            raise RuntimeError("正式缓存写入后新增站身份异常")
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

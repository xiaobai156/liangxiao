from __future__ import annotations

import copy
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

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

TOPIC_ITEMS = (
    ("上官研之", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/479502.html"),
    ("杯中之水", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/453967.html"),
    ("林间语屑", "bottom", "https://citsyvlm.5ujgl-2q1ig-bszkno.work:17466/topic/682028.html"),
    ("恍然大悟", "top", "https://cxmdjok.8wtwc-boven-glylvt.xyz:16677/topic/437767.html"),
    ("粉骨糜身", "top", "https://qhfsrngx.x9mcp-vrrqh-dzqfro.xyz:16677/topic/451425.html"),
    ("安宅正路", "top", "https://uhaxnrzx.q76gf-deec8-zqckeo.xyz:16677/topic/615690.html"),
    ("坐怀不乱", "top", "https://gabqzzz.ahv8c-6713g-otpeiy.xyz:16677/topic/530161.html"),
    ("市区幸福", "top", "https://yelsagvn.1iwwa-jwubd-szbnwd.work:17466/topic/551397.html"),
    ("燃萁煎豆", "bottom", "https://kjqba.ipvrs-9mw2c-wdnuvo.work/topic/213294.html"),
)
NEW_ITEMS = tuple(
    {
        "name": name,
        "pick": pick,
        "url": url,
        "parser": "new_topic_235_exact",
        "payload": "page_and_scripts",
        "title": name,
    }
    for name, pick, url in TOPIC_ITEMS
) + (
    {
        "name": "飞龙骑士",
        "pick": "bottom",
        "url": "https://bbs02.836280.cyou/bbs/topic.php?id=18357",
        "parser": "feilong_qishi_bottom",
        "payload": "page_and_scripts",
    },
)


def topic_identity(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    match = re.search(r"/topic/(\d+)\.html$", parsed.path)
    if match:
        return parsed.netloc.lower(), match.group(1)
    if parsed.path.endswith("/bbs/topic.php"):
        topic_id = parse_qs(parsed.query).get("id", [""])[0]
        if topic_id:
            return parsed.netloc.lower(), topic_id
    return None


def main() -> None:
    raw_config = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    validate_cache_position_contract(cache)
    issues = [int(period) for period in cache.get("issues", [])]
    if issues != [235, 234, 233, 232, 231, 230, 229, 228, 227, 226]:
        raise RuntimeError(f"正式缓存期数不是235–226：{issues}")

    existing_names = {str(item.get("name") or "") for item in raw_config}
    existing_urls = {str(item.get("url") or "") for item in raw_config}
    existing_topics = {identity for url in existing_urls if (identity := topic_identity(url))}
    for item in NEW_ITEMS:
        if item["name"] in existing_names or item["url"] in existing_urls:
            raise RuntimeError(f"正式配置已存在：{item['name']}")
        identity = topic_identity(item["url"])
        if identity and identity in existing_topics:
            raise RuntimeError(f"正式配置topic身份已存在：{item['name']} {identity}")

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
        parse_bundle_for_period(bundle, site, 234, registry)
        try:
            parse_bundle_for_period(bundle, site, 237, registry)
        except ValueError:
            pass
        else:
            raise RuntimeError(f"{site.name}错误接受不存在的237期")

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
            if not matches:
                raise RuntimeError(f"{site.name}缺少{period}期真实历史")
            history.append(matches[0] if site.pick == "top" else matches[-1])
        if selected.zodiac != history[0].zodiac or selected.position != history[0].position:
            raise RuntimeError(f"{site.name}235期方向选择与历史位置不一致")

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
        cache_entries.append(entry)
        temporary_cache["sites"].append(copy.deepcopy(entry))
        verified[site.name] = {
            "235": selected.zodiac,
            "position": selected.position,
            "history_count": len(history),
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
        names = [str(entry.get("name") or "") for entry in written_cache["sites"]]
        if any(names.count(item["name"]) != 1 for item in NEW_ITEMS):
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

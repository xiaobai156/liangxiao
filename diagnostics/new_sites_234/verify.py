from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from cache.duplicates import detect_duplicate_findings
from domain.models import Site
from fetching.client import FetchContext
from fetching.dynamic_article import admin_article_id, landing_page_data_api_url
from fetching.page import fetch_payload
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"
SITE_DATA = (
    (
        "妆罢低声",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a4e87bd57dc857ae1c13558?url=dyj",
        "zhuangba_disheng_manager_article",
    ),
    (
        "燕姬独擅",
        "https://fansiggg.jc2q8-whfmg-vajjui.xyz:29433/article/manager/6a64ec62b3f65fed7d6286e7?url=dyj",
        "yanji_dushan_manager_article",
    ),
    (
        "金码赌神",
        "https://vnxqseiu.ymm13-381iq-zcgmtu.xyz:29400/article/manager/6a081b9be0d076537e1df8a6?url=jdb",
        "jinma_dushen_manager_article",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_sites() -> list[Site]:
    return [
        Site(
            name=name,
            pick="bottom",
            url=url,
            parser=parser,
            payload="admin_article_api",
            api_url=landing_page_data_api_url(url),
        )
        for name, url, parser in SITE_DATA
    ]


def main() -> None:
    before = {str(path): digest(path) for path in (CONFIG, CACHE)}
    sites = make_sites()
    registry = ParserRegistry.bind_sites(sites)
    context = FetchContext()
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    issues = [int(value) for value in cache["issues"]]
    temporary_cache = copy.deepcopy(cache)
    report: dict[str, object] = {"cache_issues": issues, "sites": {}}

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
            raise RuntimeError(f"{site.name}目标文章文档数量不是1")
        document = bundle.documents[0]
        if document.record_id != admin_article_id(site.url):
            raise RuntimeError(f"{site.name}文章ID边界不一致")
        records = registry.parse(document, site)
        records_by_period = {record.period: record for record in records}
        checks: dict[str, object] = {}
        for period in (234, 233, 232, 231, 235):
            try:
                _records, selected = parse_bundle_for_period(bundle, site, period, registry)
                checks[str(period)] = {
                    "ok": True,
                    "zodiac": selected.zodiac,
                    "position": selected.position,
                }
            except ValueError as exc:
                checks[str(period)] = {"ok": False, "error": str(exc)}

        history = [records_by_period[period] for period in issues if period in records_by_period]
        temporary_cache["sites"].append(
            {
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
                "article_ids": {
                    str(record.period): document.record_id for record in history
                },
                "fingerprint": "".join(record.zodiac for record in history),
            }
        )
        report["sites"][site.name] = {
            "article_id": document.record_id,
            "record_path": document.record_path,
            "record_count": document.record_count,
            "history_total": len(records),
            "cache_history": [
                {
                    "period": record.period,
                    "zodiac": record.zodiac,
                    "position": record.position,
                }
                for record in history
            ],
            "checks": checks,
        }

    target_names = {site.name for site in sites}
    findings = [
        finding
        for finding in detect_duplicate_findings(temporary_cache)
        if finding.first in target_names or finding.second in target_names
    ]
    report["duplicate_findings"] = [
        {
            "first": finding.first,
            "second": finding.second,
            "start": finding.start_period,
            "end": finding.end_period,
            "consecutive": finding.consecutive,
            "classification": finding.classification,
        }
        for finding in findings
    ]
    after = {str(path): digest(path) for path in (CONFIG, CACHE)}
    report["formal_unchanged"] = before == after
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

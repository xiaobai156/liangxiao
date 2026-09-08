from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from cache.duplicates import detect_duplicate_findings
from domain.models import Site
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.new_sites_235 import EXACT_TOPIC_SITES
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def target_sites() -> list[Site]:
    sites = [
        Site(
            name=name,
            pick=pick,
            url=url,
            parser="new_topic_235_exact",
            title=name,
            payload="page_and_scripts",
        )
        for name, (pick, url) in EXACT_TOPIC_SITES.items()
    ]
    sites.extend(
        (
            Site(
                "周公神算",
                "top",
                "https://xxn08n.w2jqr-rbl71-ngvkmq.work/",
                parser="zhougong_shensuan_two_zodiac",
                payload="page_and_scripts",
            ),
            Site(
                "托尼库尔莫妮卡",
                "top",
                "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245",
                parser="tuoni_kulmonika_snapshots",
                payload="tuku_user_forums",
            ),
            Site(
                "小算算",
                "bottom",
                "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
                parser="xiaosuan_bottom_two_zodiac",
                payload="tuku_user_forums",
            ),
            Site(
                "飞龙骑士",
                "bottom",
                "https://bbs02.836280.cyou/bbs/topic.php?id=18357",
                parser="feilong_qishi_bottom",
                payload="page_and_scripts",
            ),
        )
    )
    return sites


def main() -> None:
    before = {str(path): digest(path) for path in (CONFIG, CACHE)}
    cache = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    issues = [int(period) for period in cache["issues"]]
    sites = target_sites()
    registry = ParserRegistry.bind_sites(sites)
    context = FetchContext()
    temporary_cache = copy.deepcopy(cache)
    report: dict[str, object] = {"cache_issues": issues, "sites": {}}
    accepted_names: set[str] = set()

    for site in sites:
        site_report: dict[str, object] = {"pick": site.pick, "url": site.url}
        try:
            bundle = fetch_payload(
                site,
                235,
                25,
                context,
                lambda source, target, issue: any(
                    record.period == issue for record in registry.parse(source, target)
                ),
            )
            records, selected = parse_bundle_for_period(bundle, site, 235, registry)
            _adjacent_records, adjacent = parse_bundle_for_period(bundle, site, 234, registry)
            try:
                parse_bundle_for_period(bundle, site, 237, registry)
                missing_error = "237期被错误接受"
            except ValueError as exc:
                missing_error = str(exc)
            observed = [
                (document, registry.parse(document, site))
                for document in bundle.documents
            ]
            observed = [(document, values) for document, values in observed if values]
            if len(observed) != 1:
                raise ValueError(f"历史权威文档数量不是1：{len(observed)}")
            document, all_records = observed[0]
            history = []
            missing_periods = []
            for period in issues:
                matches = [record for record in all_records if record.period == period]
                if not matches:
                    missing_periods.append(period)
                    continue
                ordered = sorted(matches, key=lambda record: record.position)
                history.append(ordered[0] if site.pick == "top" else ordered[-1])
            site_report.update(
                {
                    "selected_235": {
                        "zodiac": selected.zodiac,
                        "position": selected.position,
                    },
                    "adjacent_234": {
                        "zodiac": adjacent.zodiac,
                        "position": adjacent.position,
                    },
                    "missing_237_error": missing_error,
                    "document_label": document.label,
                    "document_url": document.url,
                    "record_id": document.record_id,
                    "history": [
                        {
                            "period": record.period,
                            "zodiac": record.zodiac,
                            "position": record.position,
                        }
                        for record in history
                    ],
                    "missing_periods": missing_periods,
                }
            )
            if len(history) == 10 and not missing_periods:
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
                temporary_cache["sites"].append(entry)
                accepted_names.add(site.name)
                site_report["history_gate"] = "passed"
            else:
                site_report["history_gate"] = "insufficient"
        except Exception as exc:
            site_report["error"] = str(exc)
        report["sites"][site.name] = site_report

    findings = [
        finding
        for finding in detect_duplicate_findings(temporary_cache)
        if finding.first in accepted_names or finding.second in accepted_names
    ]
    report["accepted_names"] = sorted(accepted_names)
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

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from config.loader import load_sites
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.direction import direction_window


ROOT = Path(__file__).resolve().parents[2]
FORMAL_FILES = (
    ROOT / "config" / "sites.json",
    ROOT / "recent_10_cache.json",
)
TARGET_NAMES = ("枪打天下", "雁落沙滩")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    before = {str(path): digest(path) for path in FORMAL_FILES}
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    targets = [site for site in sites if site.name in TARGET_NAMES]
    registry = ParserRegistry.bind_sites(targets)
    context = FetchContext()
    report: dict[str, object] = {"formal_hashes_before": before, "sites": {}}

    for site in targets:
        bundle = fetch_payload(
            site,
            232,
            25,
            context,
            lambda source, target, issue: any(
                record.period == issue for record in registry.parse(source, target)
            ),
        )
        documents: list[dict[str, object]] = []
        for document in bundle.documents:
            records = registry.parse(document, site)
            window = direction_window(records, site) if records else []
            documents.append(
                {
                    "label": document.label,
                    "url": document.url,
                    "record_id": document.record_id,
                    "records": [
                        {
                            "period": record.period,
                            "zodiac": record.zodiac,
                            "position": record.position,
                            "anchor": record.anchor_text,
                            "block_id": record.block_id,
                        }
                        for record in records
                    ],
                    "window": [
                        {
                            "period": record.period,
                            "zodiac": record.zodiac,
                            "position": record.position,
                        }
                        for record in window
                    ],
                }
            )

        selections: dict[str, object] = {}
        for period in (232, 231, 230, 233):
            try:
                records, selected = parse_bundle_for_period(bundle, site, period, registry)
                selections[str(period)] = {
                    "ok": True,
                    "zodiac": selected.zodiac,
                    "position": selected.position,
                    "document_url": selected.document_url,
                    "window": [
                        {
                            "period": record.period,
                            "zodiac": record.zodiac,
                            "position": record.position,
                        }
                        for record in direction_window(records, site)
                    ],
                }
            except Exception as exc:  # diagnostic report must retain exact failure
                selections[str(period)] = {"ok": False, "error": str(exc)}

        report["sites"][site.name] = {
            "url": site.url,
            "pick": site.pick,
            "parser": site.parser,
            "payload": site.payload,
            "scan_complete": bundle.scan_complete,
            "documents": documents,
            "selections": selections,
        }

    after = {str(path): digest(path) for path in FORMAL_FILES}
    report["formal_hashes_after"] = after
    report["formal_files_unchanged"] = before == after
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

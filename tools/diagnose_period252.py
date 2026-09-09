from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from config.loader import load_sites
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import scrape_sites

PERIOD = 252
ROOT = Path(__file__).resolve().parents[1]
SITES_FILE = ROOT / "config" / "sites.json"
OUT = ROOT / "period252-diagnostic.json"


def category(error: str) -> str:
    if not error:
        return ""
    return error.split("：", 1)[0] if "：" in error else "未分类"


def main() -> int:
    sites = load_sites(SITES_FILE, allowed_parsers=ENGINE_REGISTRY)
    registry = ParserRegistry.bind_sites(sites)
    results = scrape_sites(
        sites,
        PERIOD,
        timeout=25,
        workers=10,
        registry=registry,
        progress=print,
    )
    success = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    categories = Counter(category(result.error) for result in failed)
    payload = {
        "period": PERIOD,
        "base_main_sha": os.environ.get("BASE_MAIN_SHA", ""),
        "total": len(results),
        "success": len(success),
        "failed": len(failed),
        "success_rate": round(len(success) / len(results) * 100, 3) if results else 0.0,
        "failure_categories": dict(sorted(categories.items())),
        "results": [
            {
                "name": result.site.name,
                "pick": result.site.pick,
                "url": result.site.url,
                "ok": result.ok,
                "zodiac": result.record.zodiac if result.record else "",
                "position": result.record.position if result.record else None,
                "record_id": result.record.record_id if result.record else "",
                "error": result.error,
            }
            for result in results
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== PERIOD252_SUMMARY ===")
    print(json.dumps({k: payload[k] for k in ("period", "base_main_sha", "total", "success", "failed", "success_rate", "failure_categories")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

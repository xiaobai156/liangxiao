from __future__ import annotations

import hashlib
import json
from pathlib import Path

from domain.models import Site
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.registry import ParserRegistry
from validation.direction import direction_window


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "sites.json"
CACHE = ROOT / "recent_10_cache.json"
PAGE_SITES = (
    ("上官研之", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/479502.html"),
    ("杯中之水", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/topic/453967.html"),
    ("周公神算", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/"),
    ("林间语屑", "bottom", "https://citsyvlm.5ujgl-2q1ig-bszkno.work:17466/topic/682028.html"),
    ("恍然大悟", "top", "https://cxmdjok.8wtwc-boven-glylvt.xyz:16677/topic/437767.html"),
    ("粉骨糜身", "top", "https://qhfsrngx.x9mcp-vrrqh-dzqfro.xyz:16677/topic/451425.html"),
    ("安宅正路", "top", "https://uhaxnrzx.q76gf-deec8-zqckeo.xyz:16677/topic/615690.html"),
    ("坐怀不乱", "top", "https://gabqzzz.ahv8c-6713g-otpeiy.xyz:16677/topic/530161.html"),
    ("市区幸福", "top", "https://yelsagvn.1iwwa-jwubd-szbnwd.work:17466/topic/551397.html"),
    ("燃萁煎豆", "bottom", "https://kjqba.ipvrs-9mw2c-wdnuvo.work/topic/213294.html"),
)
USER_SITES = (
    ("托尼库尔莫妮卡", "top", "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245"),
    ("小算算", "bottom", "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sites() -> list[Site]:
    page_sites = [
        Site(
            name=name,
            pick=pick,
            url=url,
            parser="site_scoped_two_zodiac",
            title=name,
            payload="page_and_scripts",
        )
        for name, pick, url in PAGE_SITES
    ]
    user_sites = [
        Site(
            name=name,
            pick=pick,
            url=url,
            parser="tuku_user_forums_two_zodiac",
            title="绝杀二肖",
            payload="tuku_user_forums",
        )
        for name, pick, url in USER_SITES
    ]
    return page_sites + user_sites


def main() -> None:
    before = {str(path): digest(path) for path in (CONFIG, CACHE)}
    targets = sites()
    registry = ParserRegistry.bind_sites(targets)
    context = FetchContext()
    report: dict[str, object] = {"sites": {}}
    for site in targets:
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
            documents = []
            for document in bundle.documents:
                records = registry.parse(document, site)
                if not records:
                    continue
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
                            }
                            for record in records
                        ],
                        "window": [
                            {
                                "period": record.period,
                                "zodiac": record.zodiac,
                                "position": record.position,
                            }
                            for record in direction_window(records, site)
                        ],
                    }
                )
            report["sites"][site.name] = {
                "pick": site.pick,
                "url": site.url,
                "documents": documents,
                "scan_complete": bundle.scan_complete,
            }
        except Exception as exc:
            report["sites"][site.name] = {
                "pick": site.pick,
                "url": site.url,
                "error": str(exc),
            }
    after = {str(path): digest(path) for path in (CONFIG, CACHE)}
    report["formal_unchanged"] = before == after
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
